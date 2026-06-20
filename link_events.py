"""Manually link one Google event to one CalDAV event (for near-misses that
content-based adoption couldn't auto-match, e.g. a title edited on one side).

  python link_events.py <pair_name> <google_uid> <caldav_uid> [--prefer caldav|google]

Without --prefer: link-only (both sides keep their current content).
With --prefer caldav|google: the next sync pushes the preferred side's content
to the other (done by marking the other side's stored etag stale).
"""

from __future__ import annotations

import argparse
import sys

from caldav_client import CalDAVClient
from config_loader import load_config
from google_client import GoogleClient
from sync_db import SyncDB

STALE = "__force_resync__"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pair")
    ap.add_argument("google_uid")
    ap.add_argument("caldav_uid")
    ap.add_argument("--prefer", choices=["caldav", "google"])
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    pair = next((p for p in cfg["calendar_pairs"] if p["name"] == args.pair), None)
    if pair is None:
        print(f"No calendar pair named {args.pair!r}", file=sys.stderr)
        return 2

    g = cfg["google"]; n = cfg["nextcloud"]
    google = GoogleClient(g["credentials_file"], g["token_file"], pair["google_calendar_id"])
    caldav = CalDAVClient(n["url"], n["username"], n["password"], pair["caldav_calendar_name"])

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    tmin = (now - timedelta(days=cfg["sync"]["sync_past_days"])).isoformat()
    tmax = (now + timedelta(days=cfg["sync"]["sync_future_days"])).isoformat()

    ge = next((e for e in google.list_events(tmin, tmax) if e["uid"] == args.google_uid), None)
    ce = next((e for e in caldav.events_in_window(
        now - timedelta(days=cfg["sync"]["sync_past_days"]),
        now + timedelta(days=cfg["sync"]["sync_future_days"]),
    ) if e["uid"] == args.caldav_uid), None)
    if ge is None or ce is None:
        print(f"Could not find google={ge is not None} caldav={ce is not None} event in window",
              file=sys.stderr)
        return 1

    google_etag = ge["google_etag"]
    caldav_etag = ce["caldav_etag"]
    if args.prefer == "caldav":
        caldav_etag = STALE      # marks CalDAV "changed" -> next sync pushes it to Google
    elif args.prefer == "google":
        google_etag = STALE      # marks Google "changed" -> next sync pushes it to CalDAV

    db = SyncDB(cfg["sync"]["state_db"])
    db.upsert_state(
        ge["uid"], pair=args.pair, caldav_uid=ce["uid"],
        google_event_id=ge["google_event_id"], google_etag=google_etag,
        google_updated=ge["google_updated"], caldav_href=ce["caldav_href"],
        caldav_etag=caldav_etag, caldav_updated=ce["caldav_updated"],
        sync_direction="manual",
    )
    db.close()
    print(f"Linked {args.pair}: Google {args.google_uid} <-> CalDAV {args.caldav_uid}"
          + (f" (prefer {args.prefer})" if args.prefer else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
