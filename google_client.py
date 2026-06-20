"""Google Calendar API wrapper.

Phase 2: read AND write. Reads events for reconciliation and can create
(via import_, preserving the shared iCalUID), update, and delete events.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Optional

from dateutil import parser as dtparser
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import mappings

log = logging.getLogger("caldavsync.google")

# Phase 2 needs read/write access (Phase 1 used calendar.readonly).
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleClient:
    def __init__(
        self,
        credentials_file: str,
        token_file: str,
        calendar_id: str = "primary",
    ) -> None:
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.calendar_id = calendar_id
        self.service = self._build_service()

    def _build_service(self):
        creds: Optional[Credentials] = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
            # A token minted with narrower (Phase 1 read-only) scopes must be
            # re-authorized for write access.
            granted = set(creds.scopes or [])
            if not set(SCOPES).issubset(granted):
                log.info("Existing token lacks required scopes; re-authorizing")
                creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                log.info("Refreshing expired Google token")
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_file):
                    raise FileNotFoundError(
                        f"Google credentials file not found: {self.credentials_file!r}. "
                        "Download a Desktop-app OAuth client from the Google Cloud Console."
                    )
                log.info("Starting Google OAuth2 flow (browser will open)")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
            log.info("Saved Google token to %s", self.token_file)

        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    # --- read ----------------------------------------------------------

    def list_events(self, time_min: str, time_max: str) -> list[dict[str, Any]]:
        """Return normalized events overlapping [time_min, time_max].

        singleEvents=False keeps one stable iCalUID per event (recurring
        masters are flagged ``recurring`` and skipped by the engine until
        Phase 3).
        """
        events: list[dict[str, Any]] = []
        page_token: Optional[str] = None
        while True:
            resp = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=False,
                    showDeleted=False,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute()
            )
            for raw in resp.get("items", []):
                normalized = self._normalize(raw)
                if normalized is not None:
                    events.append(normalized)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        log.info("Fetched %d events from Google calendar %r", len(events), self.calendar_id)
        return events

    @staticmethod
    def _normalize(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
        if raw.get("status") == "cancelled":
            return None

        uid = raw.get("iCalUID")
        start = raw.get("start", {})
        end = raw.get("end", {})
        if not uid or not start:
            log.warning("Skipping Google event without UID/start: %s", raw.get("id"))
            return None

        all_day = "date" in start
        if all_day:
            start_dt = dtparser.parse(start["date"]).date()
            end_dt = dtparser.parse(end["date"]).date() if end.get("date") else None
        else:
            start_dt = dtparser.parse(start["dateTime"])
            end_dt = dtparser.parse(end["dateTime"]) if end.get("dateTime") else None

        attendees = None
        if raw.get("attendees"):
            attendees = [
                {
                    "email": a.get("email"),
                    "name": a.get("displayName"),
                    "partstat": mappings.google_status_to_partstat(a.get("responseStatus")),
                    "role": "OPT-PARTICIPANT" if a.get("optional") else "REQ-PARTICIPANT",
                }
                for a in raw["attendees"]
                if a.get("email")
            ]

        organizer = None
        if raw.get("organizer", {}).get("email"):
            organizer = {
                "email": raw["organizer"]["email"],
                "name": raw["organizer"].get("displayName"),
            }

        return {
            "uid": uid,
            "google_event_id": raw.get("id"),
            "google_etag": raw.get("etag"),
            "google_updated": raw.get("updated"),
            "summary": raw.get("summary", "(no title)"),
            "description": raw.get("description"),
            "location": raw.get("location"),
            "start": start_dt,
            "end": end_dt,
            "all_day": all_day,
            "tzid": start.get("timeZone"),
            "recurrence": raw.get("recurrence"),
            "recurring": bool(raw.get("recurrence")),
            # A modified single instance of a recurring series; synced in a
            # later phase, skipped for now so the series master stays intact.
            "is_override": bool(raw.get("recurringEventId")),
            "organizer": organizer,
            "attendees": attendees,
            "color": mappings.google_color_to_css(raw.get("colorId")),
            "categories": None,
        }

    # --- write ---------------------------------------------------------

    def create_event(self, event: dict[str, Any], send_updates: str = "none") -> dict[str, Any]:
        """Create an event, preserving the shared UID via events.import.

        import_ requires an iCalUID and is the supported way to set it on a
        new Google event. Note: import never sends attendee notifications and
        does not accept a sendUpdates parameter, so send_updates is ignored
        here (it applies to updates/deletes).
        """
        body = self._to_body(event)
        body["iCalUID"] = event["uid"]
        created = (
            self.service.events()
            .import_(calendarId=self.calendar_id, body=body)
            .execute()
        )
        return self._write_result(created)

    def update_event(
        self, google_event_id: str, event: dict[str, Any], send_updates: str = "none"
    ) -> dict[str, Any]:
        body = self._to_body(event)
        updated = (
            self.service.events()
            .patch(
                calendarId=self.calendar_id,
                eventId=google_event_id,
                body=body,
                sendUpdates=send_updates,
            )
            .execute()
        )
        return self._write_result(updated)

    def delete_event(self, google_event_id: str, send_updates: str = "none") -> None:
        try:
            self.service.events().delete(
                calendarId=self.calendar_id, eventId=google_event_id, sendUpdates=send_updates
            ).execute()
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                log.debug("Google event already deleted: %s", google_event_id)
            else:
                raise

    @staticmethod
    def _write_result(resp: dict[str, Any]) -> dict[str, Any]:
        return {
            "google_event_id": resp.get("id"),
            "google_etag": resp.get("etag"),
            "google_updated": resp.get("updated"),
        }

    @staticmethod
    def _to_body(event: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {"summary": event.get("summary") or "(no title)"}
        if event.get("description"):
            body["description"] = event["description"]
        if event.get("location"):
            body["location"] = event["location"]

        start = event["start"]
        end = event.get("end") or start

        # Always clear the opposite time field so a patch that switches an
        # event between all-day and timed can't leave both date and dateTime
        # set (Google rejects that with "Invalid start time").
        if event.get("all_day") or (isinstance(start, date) and not isinstance(start, datetime)):
            body["start"] = {"date": _date_str(start), "dateTime": None, "timeZone": None}
            body["end"] = {"date": _date_str(end), "dateTime": None, "timeZone": None}
        else:
            body["start"] = {**_datetime_field(start, event.get("tzid")), "date": None}
            body["end"] = {**_datetime_field(end, event.get("tzid")), "date": None}

        if event.get("recurrence"):
            body["recurrence"] = event["recurrence"]

        if event.get("attendees"):
            body["attendees"] = [
                {
                    "email": a["email"],
                    "displayName": a.get("name"),
                    "responseStatus": mappings.partstat_to_google_status(a.get("partstat")),
                    "optional": a.get("role") == "OPT-PARTICIPANT",
                }
                for a in event["attendees"]
                if a.get("email")
            ]

        color_id = mappings.css_to_google_color(event.get("color"))
        if color_id:
            body["colorId"] = color_id

        return body


def _date_str(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _datetime_field(value: datetime, tzid: Optional[str]) -> dict[str, str]:
    """Build a Google dateTime field. Aware datetimes carry their offset;
    naive ones get an explicit timeZone (falling back to UTC)."""
    field = {"dateTime": value.isoformat()}
    if value.tzinfo is None:
        field["timeZone"] = tzid or "UTC"
    return field
