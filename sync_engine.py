"""Sync engine — bidirectional reconciliation.

Reconciliation is link-centric: each row in sync_state links one Google event
to one CalDAV event (by their possibly-different UIDs). Each run:

  1. Process known links: detect modified / deleted / conflicted / unchanged.
  2. Link same-UID events that aren't linked yet (UID-preserving overlap).
  3. Create genuinely new Google-only events on the CalDAV side.
  4. Create genuinely new CalDAV-only events on the Google side.

Direction gating (per pair):
  google_to_caldav : Google changes flow to CalDAV only
  caldav_to_google : CalDAV changes flow to Google only
  bidirectional    : both, with conflict resolution when both sides changed

Modified single instances of a recurring series (overrides) are skipped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from caldav_client import CalDAVClient
from dateutil import parser as dtparser
from google_client import GoogleClient
from ical_convert import event_to_ical
from sync_db import SyncDB

log = logging.getLogger("caldavsync.engine")

G2C = "google_to_caldav"
C2G = "caldav_to_google"
BIDI = "bidirectional"


@dataclass
class SyncStats:
    created_caldav: int = 0
    created_google: int = 0
    updated_caldav: int = 0
    updated_google: int = 0
    deleted_caldav: int = 0
    deleted_google: int = 0
    linked: int = 0
    conflicts: int = 0
    unchanged: int = 0
    skipped_overrides: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"caldav(+{self.created_caldav}/~{self.updated_caldav}/-{self.deleted_caldav}) "
            f"google(+{self.created_google}/~{self.updated_google}/-{self.deleted_google}) "
            f"linked={self.linked} conflicts={self.conflicts} unchanged={self.unchanged} "
            f"skipped_overrides={self.skipped_overrides} errors={self.errors}"
        )


class SyncEngine:
    def __init__(
        self,
        google: GoogleClient,
        caldav: CalDAVClient,
        db: SyncDB,
        direction: str = BIDI,
        conflict_resolution: str = "newest_wins",
        delete_propagation: bool = True,
        sync_past_days: int = 30,
        sync_future_days: int = 365,
        dry_run: bool = False,
        pair: str = "default",
        send_invitations: bool = False,
    ) -> None:
        self.google = google
        self.caldav = caldav
        self.db = db
        self.direction = direction
        self.conflict_resolution = conflict_resolution
        self.delete_propagation = delete_propagation
        self.sync_past_days = sync_past_days
        self.sync_future_days = sync_future_days
        self.dry_run = dry_run
        self.pair = pair
        self.send_updates = "all" if send_invitations else "none"
        self.prefix = "[DRY-RUN] " if dry_run else ""

        self.to_caldav = direction in (G2C, BIDI)  # Google changes -> CalDAV
        self.to_google = direction in (C2G, BIDI)  # CalDAV changes -> Google

    def _window(self) -> tuple[datetime, datetime]:
        now = datetime.now(timezone.utc)
        return (
            now - timedelta(days=self.sync_past_days),
            now + timedelta(days=self.sync_future_days),
        )

    def run_once(self) -> SyncStats:
        stats = SyncStats()
        start, end = self._window()

        google = self._collect(self.google.list_events(start.isoformat(), end.isoformat()), stats)
        caldav = self._collect(self.caldav.events_in_window(start, end), stats)

        consumed_g: set[str] = set()
        consumed_c: set[str] = set()

        # 1) known links
        for link in self.db.all_states(self.pair):
            guid = link["uid"]
            cuid = link["caldav_uid"] or guid
            consumed_g.add(guid)
            consumed_c.add(cuid)
            self._guard(stats, guid, lambda l=link, g=google.get(guid), c=caldav.get(cuid):
                        self._reconcile_known(l, g, c, stats))

        # 2) same-UID overlap not yet linked (e.g. UID-preserving migration)
        for uid in (set(google) - consumed_g) & (set(caldav) - consumed_c):
            consumed_g.add(uid)
            consumed_c.add(uid)
            self._guard(stats, uid, lambda u=uid: self._link_existing(google[u], caldav[u], stats))

        # 3) Google-only new events
        for uid in set(google) - consumed_g:
            self._guard(stats, uid, lambda u=uid: self._create_in_caldav(google[u], stats))

        # 4) CalDAV-only new events
        for uid in set(caldav) - consumed_c:
            self._guard(stats, uid, lambda u=uid: self._create_in_google(caldav[u], stats))

        log.info("%sSync complete: %s", self.prefix, stats.summary())
        return stats

    def _guard(self, stats: SyncStats, uid: str, fn) -> None:
        """Run one reconcile step; a single failure must not abort the run."""
        try:
            fn()
        except Exception as exc:
            stats.errors += 1
            stats.details.append(f"{uid}: {exc}")
            log.exception("Failed to reconcile %s", uid)
            if not self.dry_run:
                self.db.log_action("error", self.direction, uid, "", str(exc), pair=self.pair)

    @staticmethod
    def _collect(events: list[dict[str, Any]], stats: SyncStats) -> dict[str, dict[str, Any]]:
        """Index events by UID, dropping modified single-instance overrides
        (recurring series masters are kept and synced)."""
        out: dict[str, dict[str, Any]] = {}
        for ev in events:
            if ev.get("is_override"):
                stats.skipped_overrides += 1
                continue
            out[ev["uid"]] = ev
        return out

    # --- known links ---------------------------------------------------

    def _reconcile_known(self, link, g: Optional[dict], c: Optional[dict], stats: SyncStats) -> None:
        if g and c:
            g_changed = link["google_etag"] != g["google_etag"]
            c_changed = link["caldav_etag"] != c["caldav_etag"]
            if g_changed and c_changed:
                self._resolve_conflict(link, g, c, stats)
            elif g_changed:
                self._update_caldav(link, g, stats)
            elif c_changed:
                self._update_google(link, c, stats)
            else:
                stats.unchanged += 1
        elif g and not c:
            # CalDAV side vanished -> mirror the deletion to Google.
            self._delete_on_google(link, g, stats)
        elif c and not g:
            # Google side vanished -> mirror the deletion to CalDAV.
            self._delete_on_caldav(link, c, stats)
        else:
            # Gone from both within the window (or simply out of window) -> keep
            # the link rather than risk re-creating later.
            stats.unchanged += 1

    def _link_existing(self, g: dict, c: dict, stats: SyncStats) -> None:
        """Both sides already share a UID but aren't linked yet — record the
        link without writing to either calendar (link-only)."""
        uid = g["uid"]
        log.info("%sLINK %r (%s) [same UID on both sides]", self.prefix, g["summary"], uid)
        stats.linked += 1
        if not self.dry_run:
            self._record(uid, g=g, c=c, caldav_uid=c["uid"], origin="linked")

    # --- conflict ------------------------------------------------------

    def _resolve_conflict(self, link, g, c, stats: SyncStats) -> None:
        winner = self._pick_winner(g, c)
        log.info("%sCONFLICT %r (%s) -> %s wins", self.prefix, g["summary"], link["uid"], winner)
        stats.conflicts += 1
        if not self.dry_run:
            self.db.log_action(
                "conflict", self.direction, link["uid"], g.get("summary", ""),
                f"{winner} wins", pair=self.pair,
            )
        if winner == "google":
            self._update_caldav(link, g, stats, from_conflict=True)
        else:
            self._update_google(link, c, stats, from_conflict=True)

    def _pick_winner(self, g, c) -> str:
        if self.conflict_resolution == "google_wins":
            return "google"
        if self.conflict_resolution == "caldav_wins":
            return "caldav"
        g_time = _parse_time(g.get("google_updated"))
        c_time = _parse_time(c.get("caldav_updated"))
        return "google" if g_time >= c_time else "caldav"

    # --- writes: Google -> CalDAV --------------------------------------

    def _create_in_caldav(self, g, stats: SyncStats) -> None:
        if not self.to_caldav:
            return
        uid = g["uid"]
        log.info("%sCREATE -> CalDAV %r (%s)", self.prefix, g["summary"], uid)
        if self.dry_run:
            stats.created_caldav += 1
            return
        res = self.caldav.create_event(event_to_ical(g))
        self._record(uid, g=g, caldav_uid=uid, caldav_res=res, origin="from_google")
        self.db.log_action("create", G2C, uid, g.get("summary", ""), pair=self.pair)
        stats.created_caldav += 1

    def _update_caldav(self, link, g, stats: SyncStats, from_conflict=False) -> None:
        if not self.to_caldav:
            # Direction disables Google->CalDAV: acknowledge the Google-side
            # change (refresh stored etag) so it doesn't re-trigger every run.
            if not self.dry_run:
                self._record(link["uid"], g=g)
            stats.unchanged += 1
            return
        href = link["caldav_href"]
        if not href:
            self._create_in_caldav(g, stats)
            return
        log.info("%sUPDATE -> CalDAV %r (%s)", self.prefix, g["summary"], link["uid"])
        if self.dry_run:
            stats.updated_caldav += 1
            return
        res = self.caldav.update_event(href, event_to_ical(g))
        self._record(link["uid"], g=g, caldav_res=res)
        if not from_conflict:
            self.db.log_action("update", G2C, link["uid"], g.get("summary", ""), pair=self.pair)
        stats.updated_caldav += 1

    def _delete_on_caldav(self, link, c, stats: SyncStats) -> None:
        if not (self.delete_propagation and self.to_caldav):
            stats.unchanged += 1
            return
        log.info("%sDELETE -> CalDAV %r (%s) [removed on Google]", self.prefix, c["summary"], link["uid"])
        if self.dry_run:
            stats.deleted_caldav += 1
            return
        self.caldav.delete_event(link["caldav_href"])
        self.db.log_action("delete", G2C, link["uid"], c.get("summary", ""), pair=self.pair)
        self.db.delete_state(link["uid"], self.pair)
        stats.deleted_caldav += 1

    # --- writes: CalDAV -> Google --------------------------------------

    def _create_in_google(self, c, stats: SyncStats) -> None:
        if not self.to_google:
            return
        uid = c["uid"]
        log.info("%sCREATE -> Google %r (%s)", self.prefix, c["summary"], uid)
        if self.dry_run:
            stats.created_google += 1
            return
        res = self.google.create_event(c, send_updates=self.send_updates)
        self._record(uid, c=c, caldav_uid=uid, google_res=res, origin="from_caldav")
        self.db.log_action("create", C2G, uid, c.get("summary", ""), pair=self.pair)
        stats.created_google += 1

    def _update_google(self, link, c, stats: SyncStats, from_conflict=False) -> None:
        if not self.to_google:
            # Direction disables CalDAV->Google: acknowledge the CalDAV-side
            # change (refresh stored etag) so it doesn't re-trigger every run.
            if not self.dry_run:
                self._record(link["uid"], c=c)
            stats.unchanged += 1
            return
        google_event_id = link["google_event_id"]
        if not google_event_id:
            self._create_in_google(c, stats)
            return
        log.info("%sUPDATE -> Google %r (%s)", self.prefix, c["summary"], link["uid"])
        if self.dry_run:
            stats.updated_google += 1
            return
        res = self.google.update_event(google_event_id, c, send_updates=self.send_updates)
        self._record(link["uid"], c=c, google_res=res)
        if not from_conflict:
            self.db.log_action("update", C2G, link["uid"], c.get("summary", ""), pair=self.pair)
        stats.updated_google += 1

    def _delete_on_google(self, link, g, stats: SyncStats) -> None:
        if not (self.delete_propagation and self.to_google):
            stats.unchanged += 1
            return
        log.info("%sDELETE -> Google %r (%s) [removed on CalDAV]", self.prefix, g["summary"], link["uid"])
        if self.dry_run:
            stats.deleted_google += 1
            return
        self.google.delete_event(link["google_event_id"], send_updates=self.send_updates)
        self.db.log_action("delete", C2G, link["uid"], g.get("summary", ""), pair=self.pair)
        self.db.delete_state(link["uid"], self.pair)
        stats.deleted_google += 1

    # --- state bookkeeping ---------------------------------------------

    def _record(self, uid, g=None, c=None, google_res=None, caldav_res=None,
                caldav_uid=None, origin=None) -> None:
        """Persist current identifiers/etags so the next run sees no spurious
        change from our own write."""
        fields: dict[str, Any] = {}
        if caldav_uid is not None:
            fields["caldav_uid"] = caldav_uid
        if g is not None:
            fields["google_event_id"] = g["google_event_id"]
            fields["google_etag"] = g["google_etag"]
            fields["google_updated"] = g["google_updated"]
        if c is not None:
            fields["caldav_href"] = c["caldav_href"]
            fields["caldav_etag"] = c["caldav_etag"]
            fields["caldav_updated"] = c["caldav_updated"]
        if google_res is not None:
            fields["google_event_id"] = google_res["google_event_id"]
            fields["google_etag"] = google_res["google_etag"]
            fields["google_updated"] = google_res["google_updated"]
        if caldav_res is not None:
            fields["caldav_href"] = caldav_res["href"]
            fields["caldav_etag"] = caldav_res["etag"]
        if origin is not None:
            fields["sync_direction"] = origin
        self.db.upsert_state(uid, pair=self.pair, **fields)


def _parse_time(value: Optional[str]) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = dtparser.parse(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)
