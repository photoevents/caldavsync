"""Offline tests for Phase 3 features: recurrence, attendees, color/category
mapping, multiple calendar pairs, and config normalization.

Run: python test_phase3.py
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import mappings
from config_loader import ConfigError, load_config
from ical_convert import event_to_ical, ical_to_event
from sync_db import SyncDB

NOW = datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)


def base_event(uid="e1"):
    return {
        "uid": uid, "summary": "Standup", "description": None, "location": None,
        "start": NOW, "end": NOW + timedelta(minutes=30), "all_day": False,
        "tzid": "UTC", "google_updated": NOW.isoformat(),
    }


def main():
    passed = 0

    # 1. RRULE survives normalized -> iCal -> normalized
    ev = base_event(); ev["recurrence"] = ["RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"]
    back = ical_to_event(event_to_ical(ev))
    assert back["recurring"] is True
    assert any("FREQ=WEEKLY" in r for r in back["recurrence"]), back["recurrence"]
    passed += 1; print("PASS 1: RRULE round-trips through iCal")

    # 2. EXDATE carried (best-effort)
    ev = base_event()
    ev["recurrence"] = ["RRULE:FREQ=DAILY;COUNT=5", "EXDATE:20260623T090000Z"]
    back = ical_to_event(event_to_ical(ev))
    joined = " ".join(back["recurrence"])
    assert "RRULE" in joined and "EXDATE" in joined, back["recurrence"]
    passed += 1; print("PASS 2: RRULE + EXDATE both carried")

    # 3. Attendees + organizer round-trip
    ev = base_event()
    ev["organizer"] = {"email": "alice@example.com", "name": "Alice"}
    ev["attendees"] = [
        {"email": "bob@example.com", "name": "Bob", "partstat": "ACCEPTED", "role": "REQ-PARTICIPANT"},
        {"email": "carol@example.com", "name": "Carol", "partstat": "DECLINED", "role": "OPT-PARTICIPANT"},
    ]
    back = ical_to_event(event_to_ical(ev))
    emails = {a["email"]: a for a in back["attendees"]}
    assert emails["bob@example.com"]["partstat"] == "ACCEPTED"
    assert emails["carol@example.com"]["partstat"] == "DECLINED"
    assert back["organizer"]["email"] == "alice@example.com"
    passed += 1; print("PASS 3: attendees + organizer round-trip with PARTSTAT")

    # 4. Color round-trips as CSS name
    ev = base_event(); ev["color"] = "tomato"
    back = ical_to_event(event_to_ical(ev))
    assert back["color"] == "tomato", back["color"]
    passed += 1; print("PASS 4: COLOR round-trips")

    # 5. Categories round-trip
    ev = base_event(); ev["categories"] = ["Work", "Important"]
    back = ical_to_event(event_to_ical(ev))
    assert set(back["categories"]) == {"Work", "Important"}, back["categories"]
    passed += 1; print("PASS 5: CATEGORIES round-trip")

    # 6. Google colorId <-> CSS mapping is invertible
    for cid in mappings.GOOGLE_COLOR_TO_CSS:
        css = mappings.google_color_to_css(cid)
        assert mappings.css_to_google_color(css) == cid, cid
    passed += 1; print("PASS 6: colorId<->CSS mapping invertible for all 11 colors")

    # 7. responseStatus <-> PARTSTAT mapping
    assert mappings.google_status_to_partstat("accepted") == "ACCEPTED"
    assert mappings.partstat_to_google_status("NEEDS-ACTION") == "needsAction"
    assert mappings.partstat_to_google_status("bogus") == "needsAction"  # safe default
    passed += 1; print("PASS 7: responseStatus<->PARTSTAT mapping")

    # 8. Multi-pair DB isolation: same UID, two pairs, independent state
    if os.path.exists("test_p3.db"):
        os.remove("test_p3.db")
    db = SyncDB("test_p3.db")
    db.upsert_state("shared-uid", pair="Personal", google_etag="P1", caldav_href="h/p")
    db.upsert_state("shared-uid", pair="Work", google_etag="W1", caldav_href="h/w")
    assert db.get_state("shared-uid", "Personal")["google_etag"] == "P1"
    assert db.get_state("shared-uid", "Work")["google_etag"] == "W1"
    assert db.all_uids("Personal") == {"shared-uid"}
    db.delete_state("shared-uid", "Personal")
    assert db.get_state("shared-uid", "Personal") is None
    assert db.get_state("shared-uid", "Work") is not None  # other pair untouched
    db.close(); os.remove("test_p3.db")
    passed += 1; print("PASS 8: multi-pair DB state is isolated per pair")

    # 9. Config: legacy single-pair form normalizes to one pair
    import yaml as _yaml
    legacy = {
        "google": {"credentials_file": "c.json", "token_file": "t.json", "calendar_id": "primary"},
        "nextcloud": {"url": "https://x/dav", "username": "u", "password": "p",
                      "calendar_name": "Personal"},
        "sync": {"direction": "bidirectional"},
    }
    with open("test_legacy.yaml", "w", encoding="utf-8") as fh:
        _yaml.safe_dump(legacy, fh)
    cfg = load_config("test_legacy.yaml")
    assert len(cfg["calendar_pairs"]) == 1
    assert cfg["calendar_pairs"][0]["caldav_calendar_name"] == "Personal"
    assert cfg["calendar_pairs"][0]["conflict_resolution"] == "newest_wins"
    assert cfg["sync"]["send_invitations"] is False
    os.remove("test_legacy.yaml")
    passed += 1; print("PASS 9: legacy config normalizes to single pair")

    # 10. Config: explicit calendar_pairs honored + duplicate names rejected
    import yaml
    multi = {
        "google": {"credentials_file": "c.json", "token_file": "t.json"},
        "nextcloud": {"url": "https://x/dav", "username": "u", "password": "p"},
        "sync": {"direction": "bidirectional"},
        "calendar_pairs": [
            {"name": "A", "google_calendar_id": "primary", "caldav_calendar_name": "A"},
            {"name": "B", "google_calendar_id": "g2", "caldav_calendar_name": "B"},
        ],
    }
    with open("test_multi.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(multi, fh)
    cfg = load_config("test_multi.yaml")
    assert [p["name"] for p in cfg["calendar_pairs"]] == ["A", "B"]

    multi["calendar_pairs"][1]["name"] = "A"  # duplicate
    with open("test_multi.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(multi, fh)
    try:
        load_config("test_multi.yaml")
        raise AssertionError("duplicate pair name not rejected")
    except ConfigError:
        pass
    os.remove("test_multi.yaml")
    passed += 1; print("PASS 10: multi-pair config honored; duplicate names rejected")

    # 11. Setup wizard: build_config output round-trips through load_config
    import yaml as _y
    from setup_wizard import build_config, default_sync_options
    cfg_dict = build_config(
        {"url": "https://nc/dav", "username": "u", "password": "p"},
        {"credentials_file": "credentials.json", "token_file": "token.json"},
        [
            {"name": "Personal", "google_calendar_id": "primary",
             "caldav_calendar_name": "personal", "direction": "bidirectional",
             "conflict_resolution": "caldav_wins"},
            {"name": "Holidays", "google_calendar_id": "h@g", "caldav_calendar_name": "Holidays",
             "direction": "google_to_caldav", "conflict_resolution": "newest_wins"},
        ],
        default_sync_options(past_days=14, future_days=180),
    )
    with open("test_wizard.yaml", "w", encoding="utf-8") as fh:
        _y.safe_dump(cfg_dict, fh, sort_keys=False)
    loaded = load_config("test_wizard.yaml")
    assert len(loaded["calendar_pairs"]) == 2
    assert loaded["calendar_pairs"][0]["conflict_resolution"] == "caldav_wins"
    assert loaded["calendar_pairs"][1]["direction"] == "google_to_caldav"
    assert loaded["sync"]["sync_past_days"] == 14 and loaded["sync"]["sync_future_days"] == 180
    os.remove("test_wizard.yaml")
    passed += 1; print("PASS 11: setup wizard output is a valid, loadable config")

    print("\nAll %d Phase 3 tests passed." % passed)


if __name__ == "__main__":
    main()
