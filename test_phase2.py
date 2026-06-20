"""Offline reconcile tests for Phase 2 using in-memory fake clients.

Exercises every branch of the engine without touching the network.
Run: python test_phase2.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from ical_convert import ical_to_event
from sync_db import SyncDB
from sync_engine import SyncEngine

NOW = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)


def gevent(uid, etag, updated, summary="Ev", recurring=False):
    return {
        "uid": uid, "google_event_id": "g_" + uid, "google_etag": etag,
        "google_updated": updated, "summary": summary, "description": None,
        "location": None, "start": NOW, "end": NOW + timedelta(hours=1),
        "all_day": False, "tzid": "UTC", "recurring": recurring,
    }


class FakeGoogle:
    def __init__(self, events):
        self.store = {e["uid"]: e for e in events}
        self._n = 0

    def list_events(self, tmin, tmax):
        return list(self.store.values())

    def create_event(self, event, send_updates="none"):
        self._n += 1
        eid = "gnew_%d" % self._n
        return {"google_event_id": eid, "google_etag": "ge%d" % self._n,
                "google_updated": NOW.isoformat()}

    def update_event(self, eid, event, send_updates="none"):
        self._n += 1
        return {"google_event_id": eid, "google_etag": "gupd%d" % self._n,
                "google_updated": NOW.isoformat()}

    def delete_event(self, eid, send_updates="none"):
        self.deleted = getattr(self, "deleted", [])
        self.deleted.append(eid)


class FakeCalDAV:
    def __init__(self, events):
        # events: list of normalized caldav dicts
        self.store = {e["uid"]: e for e in events}
        self._n = 0
        self.deleted = []

    def events_in_window(self, start, end):
        return list(self.store.values())

    def create_event(self, ical_bytes):
        self._n += 1
        href = "http://cal/new%d.ics" % self._n
        return {"href": href, "etag": "ce%d" % self._n}

    def update_event(self, href, ical_bytes):
        self._n += 1
        return {"href": href, "etag": "cupd%d" % self._n}

    def delete_event(self, href):
        self.deleted.append(href)


def cevent(uid, etag, updated, summary="Ev", recurring=False):
    return {
        "uid": uid, "summary": summary, "description": None, "location": None,
        "start": NOW, "end": NOW + timedelta(hours=1), "all_day": False,
        "tzid": "UTC", "recurring": recurring,
        "caldav_href": "http://cal/%s.ics" % uid, "caldav_etag": etag,
        "caldav_updated": updated,
    }


def fresh_db():
    if os.path.exists("test_p2.db"):
        os.remove("test_p2.db")
    return SyncDB("test_p2.db")


def engine(g, c, db, **kw):
    return SyncEngine(g, c, db, direction="bidirectional", **kw)


def main():
    passed = 0

    # 1. New on Google -> created in CalDAV
    db = fresh_db()
    g = FakeGoogle([gevent("u1", "e1", NOW.isoformat())])
    c = FakeCalDAV([])
    s = engine(g, c, db).run_once()
    assert s.created_caldav == 1, s.summary()
    row = db.get_state("u1")
    assert row["caldav_href"] and row["google_etag"] == "e1"
    db.close(); passed += 1; print("PASS 1: new Google event -> created in CalDAV")

    # 2. New on CalDAV -> created in Google
    db = fresh_db()
    g = FakeGoogle([])
    c = FakeCalDAV([cevent("u2", "ce1", NOW.isoformat())])
    s = engine(g, c, db).run_once()
    assert s.created_google == 1, s.summary()
    assert db.get_state("u2")["google_event_id"].startswith("gnew"), "google id recorded"
    db.close(); passed += 1; print("PASS 2: new CalDAV event -> created in Google")

    # 3. Modified on Google (etag changed) -> update CalDAV
    db = fresh_db()
    db.upsert_state("u3", google_event_id="g_u3", google_etag="OLD",
                    caldav_href="http://cal/u3.ics", caldav_etag="ce1")
    g = FakeGoogle([gevent("u3", "NEW", NOW.isoformat())])
    c = FakeCalDAV([cevent("u3", "ce1", NOW.isoformat())])
    s = engine(g, c, db).run_once()
    assert s.updated_caldav == 1 and s.unchanged == 0, s.summary()
    db.close(); passed += 1; print("PASS 3: Google edit -> update CalDAV")

    # 4. Modified on CalDAV -> update Google
    db = fresh_db()
    db.upsert_state("u4", google_event_id="g_u4", google_etag="e1",
                    caldav_href="http://cal/u4.ics", caldav_etag="OLD")
    g = FakeGoogle([gevent("u4", "e1", NOW.isoformat())])
    c = FakeCalDAV([cevent("u4", "NEW", NOW.isoformat())])
    s = engine(g, c, db).run_once()
    assert s.updated_google == 1, s.summary()
    db.close(); passed += 1; print("PASS 4: CalDAV edit -> update Google")

    # 5. Unchanged on both -> no action
    db = fresh_db()
    db.upsert_state("u5", google_event_id="g_u5", google_etag="e1",
                    caldav_href="http://cal/u5.ics", caldav_etag="ce1")
    g = FakeGoogle([gevent("u5", "e1", NOW.isoformat())])
    c = FakeCalDAV([cevent("u5", "ce1", NOW.isoformat())])
    s = engine(g, c, db).run_once()
    assert s.unchanged == 1 and s.updated_caldav == 0 and s.updated_google == 0, s.summary()
    db.close(); passed += 1; print("PASS 5: unchanged -> no action")

    # 6. Conflict, newest_wins -> Google newer
    db = fresh_db()
    db.upsert_state("u6", google_event_id="g_u6", google_etag="OLD_G",
                    caldav_href="http://cal/u6.ics", caldav_etag="OLD_C")
    g = FakeGoogle([gevent("u6", "NEW_G", (NOW + timedelta(hours=2)).isoformat())])
    c = FakeCalDAV([cevent("u6", "NEW_C", NOW.isoformat())])
    s = engine(g, c, db, conflict_resolution="newest_wins").run_once()
    assert s.conflicts == 1 and s.updated_caldav == 1 and s.updated_google == 0, s.summary()
    db.close(); passed += 1; print("PASS 6: conflict newest_wins (Google newer) -> CalDAV updated")

    # 7. Conflict, caldav_wins
    db = fresh_db()
    db.upsert_state("u7", google_event_id="g_u7", google_etag="OLD_G",
                    caldav_href="http://cal/u7.ics", caldav_etag="OLD_C")
    g = FakeGoogle([gevent("u7", "NEW_G", (NOW + timedelta(hours=5)).isoformat())])
    c = FakeCalDAV([cevent("u7", "NEW_C", NOW.isoformat())])
    s = engine(g, c, db, conflict_resolution="caldav_wins").run_once()
    assert s.conflicts == 1 and s.updated_google == 1 and s.updated_caldav == 0, s.summary()
    db.close(); passed += 1; print("PASS 7: conflict caldav_wins -> Google updated")

    # 8. Deleted on Google (known, present CalDAV, absent Google) -> delete CalDAV
    db = fresh_db()
    db.upsert_state("u8", google_event_id="g_u8", google_etag="e1",
                    caldav_href="http://cal/u8.ics", caldav_etag="ce1")
    g = FakeGoogle([])
    c = FakeCalDAV([cevent("u8", "ce1", NOW.isoformat())])
    s = engine(g, c, db, delete_propagation=True).run_once()
    assert s.deleted_caldav == 1 and "http://cal/u8.ics" in c.deleted, s.summary()
    assert db.get_state("u8") is None, "state removed after deletion"
    db.close(); passed += 1; print("PASS 8: Google deletion -> CalDAV deleted + state removed")

    # 9. Deleted on CalDAV -> delete Google
    db = fresh_db()
    db.upsert_state("u9", google_event_id="g_u9", google_etag="e1",
                    caldav_href="http://cal/u9.ics", caldav_etag="ce1")
    g = FakeGoogle([gevent("u9", "e1", NOW.isoformat())])
    c = FakeCalDAV([])
    s = engine(g, c, db, delete_propagation=True).run_once()
    assert s.deleted_google == 1 and "g_u9" in g.deleted, s.summary()
    db.close(); passed += 1; print("PASS 9: CalDAV deletion -> Google deleted")

    # 10. delete_propagation off -> no deletion
    db = fresh_db()
    db.upsert_state("u10", google_event_id="g_u10", google_etag="e1",
                    caldav_href="http://cal/u10.ics", caldav_etag="ce1")
    g = FakeGoogle([])
    c = FakeCalDAV([cevent("u10", "ce1", NOW.isoformat())])
    s = engine(g, c, db, delete_propagation=False).run_once()
    assert s.deleted_caldav == 0 and db.get_state("u10") is not None, s.summary()
    db.close(); passed += 1; print("PASS 10: delete_propagation off -> event kept")

    # 11. Recurring series masters are now synced; only overrides are skipped
    db = fresh_db()
    master = gevent("u11", "e1", NOW.isoformat())
    master["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    override = gevent("u11inst", "e2", NOW.isoformat())
    override["is_override"] = True
    g = FakeGoogle([master, override])
    c = FakeCalDAV([])
    s = engine(g, c, db).run_once()
    assert s.created_caldav == 1 and s.skipped_overrides == 1, s.summary()
    db.close(); passed += 1; print("PASS 11: recurring master synced, override skipped")

    # 12. Gone from both but known -> state preserved (window-safety)
    db = fresh_db()
    db.upsert_state("u12", google_event_id="g_u12", google_etag="e1",
                    caldav_href="http://cal/u12.ics", caldav_etag="ce1")
    g = FakeGoogle([]); c = FakeCalDAV([])
    s = engine(g, c, db, delete_propagation=True).run_once()
    assert db.get_state("u12") is not None, "out-of-window event must not be deleted"
    assert s.deleted_caldav == 0 and s.deleted_google == 0, s.summary()
    db.close(); passed += 1; print("PASS 12: absent-from-both -> state preserved (no false delete)")

    # 13. One-way google_to_caldav ignores CalDAV-only new events
    db = fresh_db()
    g = FakeGoogle([])
    c = FakeCalDAV([cevent("u13", "ce1", NOW.isoformat())])
    eng = SyncEngine(g, c, db, direction="google_to_caldav")
    s = eng.run_once()
    assert s.created_google == 0 and db.get_state("u13") is None, s.summary()
    db.close(); passed += 1; print("PASS 13: google_to_caldav ignores CalDAV-only events")

    # 14. Round-trip iCal: event_to_ical -> ical_to_event preserves fields
    from ical_convert import event_to_ical
    ev = gevent("rt1", "e", NOW.isoformat(), summary="Round Trip")
    ev["description"] = "hello"; ev["location"] = "HQ"
    ical = event_to_ical(ev)
    back = ical_to_event(ical, href="http://x", etag="z")
    assert back["uid"] == "rt1" and back["summary"] == "Round Trip"
    assert back["description"] == "hello" and back["location"] == "HQ"
    assert back["start"] == ev["start"]
    passed += 1; print("PASS 14: iCal round-trip preserves uid/summary/desc/location/start")

    # 15. Adoption: same title+time but DIFFERENT UIDs -> linked, no duplicates
    from adopt import adopt_pair
    db = fresh_db()
    g = FakeGoogle([gevent("G-uid", "ge", NOW.isoformat(), summary="Lunch")])
    c = FakeCalDAV([cevent("C-uid", "ce", NOW.isoformat(), summary="Lunch")])
    eng = engine(g, c, db)
    st = adopt_pair(eng, apply=True)
    assert st.matched == 1, st.summary()
    row = db.get_state("G-uid")
    assert row is not None and row["caldav_uid"] == "C-uid", "diverged UIDs linked"
    s = eng.run_once()
    assert s.unchanged == 1 and s.created_google == 0 and s.created_caldav == 0, s.summary()
    db.close(); passed += 1
    print("PASS 15: adopt links diverged-UID events; sync sees them unchanged (no dup)")

    # 16. After adoption, an edit on one side UPDATES the other (not create)
    db = fresh_db()
    db.upsert_state("G-uid", caldav_uid="C-uid", google_event_id="g_G-uid",
                    google_etag="ge", caldav_href="http://cal/C-uid.ics", caldav_etag="ce")
    g = FakeGoogle([gevent("G-uid", "NEWETAG", NOW.isoformat(), summary="Lunch")])
    c = FakeCalDAV([cevent("C-uid", "ce", NOW.isoformat(), summary="Lunch")])
    s = engine(g, c, db).run_once()
    assert s.updated_caldav == 1 and s.created_caldav == 0, s.summary()
    db.close(); passed += 1
    print("PASS 16: edit on linked diverged-UID event updates the other side")

    # 17. Adoption is link-only when --apply is not set (preview writes nothing)
    db = fresh_db()
    g = FakeGoogle([gevent("G2", "ge", NOW.isoformat(), summary="Sync")])
    c = FakeCalDAV([cevent("C2", "ce", NOW.isoformat(), summary="Sync")])
    st = adopt_pair(engine(g, c, db), apply=False)
    assert st.matched == 1 and db.get_state("G2") is None, "preview must not write links"
    db.close(); passed += 1
    print("PASS 17: adopt preview reports matches without writing")

    # 18. One-way pair acknowledges a change on the non-authoritative side and
    #     settles (refreshes stored etag), instead of re-triggering forever
    db = fresh_db()
    db.upsert_state("u18", caldav_uid="u18", google_event_id="g_u18", google_etag="ge",
                    caldav_href="http://cal/u18.ics", caldav_etag="OLD")
    g = FakeGoogle([gevent("u18", "ge", NOW.isoformat())])
    c = FakeCalDAV([cevent("u18", "NEW", NOW.isoformat())])
    s = SyncEngine(g, c, db, direction="google_to_caldav").run_once()
    assert s.unchanged == 1 and s.updated_google == 0, s.summary()
    assert db.get_state("u18")["caldav_etag"] == "NEW", "stored etag refreshed -> settles"
    db.close(); passed += 1
    print("PASS 18: one-way pair acknowledges non-authoritative change and settles")

    os.remove("test_p2.db")
    print("\nAll %d Phase 2 tests passed." % passed)


if __name__ == "__main__":
    main()
