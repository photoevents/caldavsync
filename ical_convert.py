"""Convert between normalized event dicts and iCalendar VEVENTs.

Used by both sync directions:
  - event_to_ical:  normalized event -> iCalendar bytes (write to CalDAV)
  - ical_to_event:  iCalendar text   -> normalized event (read from CalDAV)

Phase 3 fields carried through: recurrence (RRULE/EXDATE/RDATE), attendees,
organizer, color (CSS name), and categories.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional, Union

from dateutil import parser as dtparser
from icalendar import Calendar, Event
from icalendar.prop import vCalAddress, vRecur

PRODID = "-//CalDAVSync//Google-Nextcloud Sync//EN"


def event_to_ical(event: dict[str, Any]) -> bytes:
    """Build a single-VEVENT iCalendar document (bytes) from a normalized event."""
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")

    vevent = Event()
    vevent.add("uid", event["uid"])
    vevent.add("summary", event.get("summary") or "(no title)")
    vevent.add("dtstamp", datetime.now(timezone.utc))

    vevent.add("dtstart", event["start"])
    end = event.get("end")
    if end is not None:
        vevent.add("dtend", end)

    if event.get("description"):
        vevent.add("description", event["description"])
    if event.get("location"):
        vevent.add("location", event["location"])

    _add_recurrence(vevent, event.get("recurrence"))
    _add_people(vevent, event.get("organizer"), event.get("attendees"))

    if event.get("color"):
        vevent.add("color", event["color"])
    if event.get("categories"):
        vevent.add("categories", event["categories"])

    updated = event.get("google_updated") or event.get("caldav_updated")
    if updated:
        try:
            vevent.add("last-modified", dtparser.parse(updated))
        except (ValueError, TypeError):
            pass

    cal.add_component(vevent)
    return cal.to_ical()


def ical_to_event(
    ical_data: Union[str, bytes],
    href: Optional[str] = None,
    etag: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Parse the first VEVENT of an iCalendar document into a normalized event."""
    cal = Calendar.from_ical(ical_data)
    for comp in cal.walk("VEVENT"):
        uid = comp.get("uid")
        dtstart = comp.get("dtstart")
        if uid is None or dtstart is None:
            return None

        start = dtstart.dt
        end_prop = comp.get("dtend")
        end = end_prop.dt if end_prop is not None else None
        all_day = isinstance(start, date) and not isinstance(start, datetime)

        last_mod = comp.get("last-modified") or comp.get("dtstamp")
        caldav_updated = None
        if last_mod is not None:
            dt = last_mod.dt
            caldav_updated = dt.isoformat() if isinstance(dt, (date, datetime)) else None

        tzid = None
        if isinstance(start, datetime) and start.tzinfo is not None:
            tzid = getattr(start.tzinfo, "key", None) or str(start.tzinfo)

        return {
            "uid": str(uid),
            "summary": str(comp.get("summary") or "(no title)"),
            "description": str(comp.get("description")) if comp.get("description") else None,
            "location": str(comp.get("location")) if comp.get("location") else None,
            "start": start,
            "end": end,
            "all_day": all_day,
            "tzid": tzid,
            "recurrence": _read_recurrence(comp),
            "recurring": comp.get("rrule") is not None,
            "is_override": comp.get("recurrence-id") is not None,
            "organizer": _read_organizer(comp),
            "attendees": _read_attendees(comp),
            "color": str(comp.get("color")) if comp.get("color") else None,
            "categories": _read_categories(comp),
            "caldav_href": href,
            "caldav_etag": etag,
            "caldav_updated": caldav_updated,
        }
    return None


# --- recurrence --------------------------------------------------------

def _add_recurrence(vevent: Event, recurrence: Optional[list[str]]) -> None:
    """Apply Google-style recurrence strings (RRULE/EXDATE/RDATE) to a VEVENT.

    RRULE is fully supported; EXDATE/RDATE are best-effort (TZID params may be
    flattened to wall-clock values).
    """
    if not recurrence:
        return
    for line in recurrence:
        name, _, value = line.partition(":")
        prop = name.split(";")[0].upper()
        if not value:
            continue
        if prop == "RRULE":
            vevent.add("rrule", vRecur.from_ical(value))
        elif prop in ("EXDATE", "RDATE"):
            dates = []
            for token in value.split(","):
                try:
                    dates.append(dtparser.parse(token))
                except (ValueError, TypeError):
                    pass
            if dates:
                vevent.add(prop.lower(), dates)


def _read_recurrence(comp: Event) -> Optional[list[str]]:
    out: list[str] = []
    for prop in ("rrule", "exdate", "rdate"):
        value = comp.get(prop)
        if value is None:
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            try:
                out.append(f"{prop.upper()}:{item.to_ical().decode()}")
            except Exception:
                pass
    return out or None


# --- people ------------------------------------------------------------

def _add_people(vevent: Event, organizer: Optional[dict], attendees: Optional[list]) -> None:
    if organizer and organizer.get("email"):
        org = vCalAddress(f"mailto:{organizer['email']}")
        if organizer.get("name"):
            org.params["CN"] = organizer["name"]
        vevent.add("organizer", org, encode=0)

    for att in attendees or []:
        if not att.get("email"):
            continue
        addr = vCalAddress(f"mailto:{att['email']}")
        if att.get("name"):
            addr.params["CN"] = att["name"]
        addr.params["PARTSTAT"] = att.get("partstat", "NEEDS-ACTION")
        addr.params["ROLE"] = att.get("role", "REQ-PARTICIPANT")
        vevent.add("attendee", addr, encode=0)


def _read_organizer(comp: Event) -> Optional[dict]:
    org = comp.get("organizer")
    if org is None:
        return None
    return {"email": _strip_mailto(str(org)), "name": org.params.get("CN")}


def _read_attendees(comp: Event) -> Optional[list[dict]]:
    raw = comp.get("attendee")
    if raw is None:
        return None
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for a in items:
        out.append(
            {
                "email": _strip_mailto(str(a)),
                "name": a.params.get("CN"),
                "partstat": a.params.get("PARTSTAT", "NEEDS-ACTION"),
                "role": a.params.get("ROLE", "REQ-PARTICIPANT"),
            }
        )
    return out or None


def _read_categories(comp: Event) -> Optional[list[str]]:
    cats = comp.get("categories")
    if cats is None:
        return None
    try:
        return [str(c) for c in cats.cats]
    except AttributeError:
        return [str(cats)]


def _strip_mailto(value: str) -> str:
    return value[7:] if value.lower().startswith("mailto:") else value
