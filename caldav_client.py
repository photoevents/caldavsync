"""CalDAV client wrapper for Nextcloud (and any CalDAV server).

Connects, locates the target calendar by display name, reads events within
a date window as normalized dicts, and creates / updates / deletes events.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import caldav
from caldav.lib.error import NotFoundError

from ical_convert import ical_to_event

log = logging.getLogger("caldavsync.caldav")


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
        try:
            raw_events = self.calendar.search(start=start, end=end, event=True, expand=False)
        except (TypeError, NotImplementedError):
            raw_events = self.calendar.events()

        result: list[dict[str, Any]] = []
        for ev in raw_events:
            try:
                normalized = ical_to_event(
                    ev.data, href=str(ev.url), etag=getattr(ev, "etag", None)
                )
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
        return {"href": str(ev.url), "etag": getattr(ev, "etag", None)}

    def update_event(self, href: str, ical_bytes: bytes) -> dict[str, Any]:
        """Phase 2: replace the event at href with new iCalendar content."""
        ev = self.calendar.event_by_url(href)
        ev.data = ical_bytes.decode("utf-8")
        ev.save()
        return {"href": str(ev.url), "etag": getattr(ev, "etag", None)}

    def delete_event(self, href: str) -> None:
        """Phase 2: delete the event at href (no-op if already gone)."""
        try:
            self.calendar.event_by_url(href).delete()
        except NotFoundError:
            log.debug("Event already absent on CalDAV: %s", href)
