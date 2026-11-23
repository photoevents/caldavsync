"""CalDAV client wrapper for Nextcloud (and any CalDAV server).

Connects, locates the target calendar by display name, reads events within
a date window as normalized dicts, and creates / updates / deletes events.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import caldav
from caldav.elements import dav
from caldav.lib.error import NotFoundError

from ical_convert import ical_to_event

log = logging.getLogger("caldavsync.caldav")

_ETAG = "{DAV:}getetag"


class CalDAVError(Exception):
    """Raised when the CalDAV server or target calendar can't be reached."""


class CalDAVClient:
    def __init__(self, url: str, username: str, password: str, calendar_name: str) -> None:
        self.calendar_name = calendar_name
        try:
            self._client = caldav.DAVClient(url=url, username=username, password=password)
            self._principal = self._client.principal()
        except Exception as exc:  # caldav raises a variety of low-level errors
            raise CalDAVError(f"Could not connect to CalDAV server at {url!r}: {exc}") from exc
        self.calendar = self._find_calendar(calendar_name)

    def _find_calendar(self, name: str):
        for cal in self._principal.calendars():
            # display name can be a property object; str() normalizes it
            if str(cal.name) == name:
                log.info("Using CalDAV calendar %r", name)
                return cal
        available = [str(c.name) for c in self._principal.calendars()]
        raise CalDAVError(
            f"CalDAV calendar {name!r} not found. Available: {available}"
        )

    def events_in_window(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Return normalized events overlapping [start, end].

        Uses date-range search so both sides reconcile over the same window
        (older caldav servers/libs fall back to listing all events).
        """
        # Request getetag in the same REPORT. Without it every etag reads as
        # None, so edits made on the CalDAV side are never detected.
        try:
            raw_events = self.calendar.search(start=start, end=end, event=True,
                                              expand=False, props=[dav.GetEtag()])
        except (TypeError, NotImplementedError):
            raw_events = self.calendar.events()

        result: list[dict[str, Any]] = []
        for ev in raw_events:
            etag = (getattr(ev, "props", None) or {}).get(_ETAG) or self._fetch_etag(ev)
            try:
                normalized = ical_to_event(ev.data, href=str(ev.url), etag=etag)
            except Exception:
                log.warning("Could not parse CalDAV event %s", getattr(ev, "url", "?"))
                continue
            if normalized is not None:
                result.append(normalized)
        log.info("Fetched %d events from CalDAV calendar %r", len(result), self.calendar_name)
        return result

    def create_event(self, ical_bytes: bytes) -> dict[str, Any]:
        """Create an event from an iCalendar document; return its href/etag."""
        ev = self.calendar.save_event(ical_bytes.decode("utf-8"))
        return {"href": str(ev.url), "etag": self._fetch_etag(ev)}

    def update_event(self, href: str, ical_bytes: bytes) -> dict[str, Any]:
        """Replace the event at href with new iCalendar content.

        A plain PUT, then re-read the etag: servers may rewrite the stored
        data, so the PUT response etag can differ from what later reads show.
        Storing that read-side etag is what keeps our own write from looking
        like a CalDAV-side change on the next run (and being echoed back).
        """
        resp = self._client.put(href, ical_bytes.decode("utf-8"),
                                {"Content-Type": 'text/calendar; charset="utf-8"'})
        status = getattr(resp, "status", 200)
        if status >= 300:
            raise CalDAVError(f"PUT {href} failed: HTTP {status}")
        return {"href": href, "etag": self._fetch_etag(self.calendar.event_by_url(href))}

    @staticmethod
    def _fetch_etag(ev) -> Any:
        try:
            return ev.get_properties([dav.GetEtag()]).get(_ETAG)
        except Exception:
            return None

    def delete_event(self, href: str) -> None:
        """Phase 2: delete the event at href (no-op if already gone)."""
        try:
            self.calendar.event_by_url(href).delete()
        except NotFoundError:
            log.debug("Event already absent on CalDAV: %s", href)
