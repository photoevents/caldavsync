"""One-time adoption of pre-existing events.

When a calendar was previously migrated one-way WITHOUT preserving iCalendar
UIDs, the same logical event has a different UID on each side. A naive sync
would treat them as unrelated and create duplicates. Adoption matches the
existing events by content (title + start time) and records a link between
them in the sync database — writing to neither calendar (link-only).

Preview mode just reports the counts; --apply writes the links.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

log = logging.getLogger("caldavsync.adopt")


@dataclass
class AdoptStats:
    pair: str
    matched: int = 0
    already_linked: int = 0
    leftover_google: int = 0
    leftover_caldav: int = 0
    skipped_overrides: int = 0

    def summary(self) -> str:
        return (
            f"matched={self.matched} already_linked={self.already_linked} "
            f"leftover_google={self.leftover_google} leftover_caldav={self.leftover_caldav} "
            f"skipped_overrides={self.skipped_overrides}"
        )


def _match_key(ev: dict[str, Any]) -> tuple[str, str]:
    """Content key for pairing the same event across systems."""
    summary = (ev.get("summary") or "").strip().lower()
    start = ev["start"]
    if ev.get("all_day") or (isinstance(start, date) and not isinstance(start, datetime)):
        start_key = start.isoformat()[:10] if isinstance(start, (date, datetime)) else str(start)
    elif isinstance(start, datetime):
        dt = start.astimezone(timezone.utc) if start.tzinfo else start.replace(tzinfo=timezone.utc)
        start_key = dt.replace(microsecond=0).isoformat()
    else:
        start_key = str(start)
    return summary, start_key


def adopt_pair(engine, apply: bool = False) -> AdoptStats:
    """Match and (optionally) link pre-existing events for one engine's pair."""
    db = engine.db
    pair = engine.pair
    stats = AdoptStats(pair=pair)
    prefix = "" if apply else "[PREVIEW] "

    start, end = engine._window()
    google = [e for e in engine.google.list_events(start.isoformat(), end.isoformat())
              if not _override(e, stats)]
    caldav = [e for e in engine.caldav.events_in_window(start, end)
              if not _override(e, stats)]

    # Exclude events already linked in the database.
    links = db.all_states(pair)
    linked_g = {l["uid"] for l in links}
    linked_c = {(l["caldav_uid"] or l["uid"]) for l in links}
    g_unlinked = [e for e in google if e["uid"] not in linked_g]
    c_unlinked = [e for e in caldav if e["uid"] not in linked_c]
    stats.already_linked = len(links)

    # Bucket Google events by content key, then greedily pair CalDAV events.
    g_buckets: dict[tuple[str, str], deque] = defaultdict(deque)
    for e in g_unlinked:
        g_buckets[_match_key(e)].append(e)

    leftover_c = []
    for ce in c_unlinked:
        bucket = g_buckets.get(_match_key(ce))
        if bucket:
            ge = bucket.popleft()
            stats.matched += 1
            log.info("%sADOPT %r -> link Google %s <-> CalDAV %s",
                     prefix, ce.get("summary", ""), ge["uid"], ce["uid"])
            if apply:
                db.upsert_state(
                    ge["uid"], pair=pair, caldav_uid=ce["uid"],
                    google_event_id=ge["google_event_id"], google_etag=ge["google_etag"],
                    google_updated=ge["google_updated"], caldav_href=ce["caldav_href"],
                    caldav_etag=ce["caldav_etag"], caldav_updated=ce["caldav_updated"],
                    sync_direction="adopted",
                )
        else:
            leftover_c.append(ce)

    stats.leftover_caldav = len(leftover_c)
    stats.leftover_google = sum(len(b) for b in g_buckets.values())
    log.info("%sPair %r: %s", prefix, pair, stats.summary())
    return stats


def _override(ev: dict[str, Any], stats: AdoptStats) -> bool:
    if ev.get("is_override"):
        stats.skipped_overrides += 1
        return True
    return False
