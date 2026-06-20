"""CalDAVSync entry point / CLI.

Usage:
    python main.py --once              # single sync run
    python main.py --once --dry-run    # show what would sync, change nothing
    python main.py --daemon            # continuous sync at configured interval
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from caldav_client import CalDAVClient, CalDAVError
from config_loader import ConfigError, load_config
from google_client import GoogleClient
from sync_db import SyncDB
from sync_engine import SyncEngine


def setup_logging(level: str, logfile: str) -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    # Event titles can contain emoji/accents that a legacy Windows console
    # (cp1252) can't encode; switch stdout to UTF-8 so logging never crashes.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    except OSError:
        pass  # console-only is fine if the log file can't be opened
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )


def build_engines(config: dict, db: SyncDB, dry_run: bool, decider=None) -> list[SyncEngine]:
    """Build one SyncEngine per configured calendar pair, sharing the DB."""
    g = config["google"]
    n = config["nextcloud"]
    s = config["sync"]

    engines: list[SyncEngine] = []
    for pair in config["calendar_pairs"]:
        google = GoogleClient(g["credentials_file"], g["token_file"], pair["google_calendar_id"])
        caldav = CalDAVClient(n["url"], n["username"], n["password"], pair["caldav_calendar_name"])
        engines.append(
            SyncEngine(
                google=google,
                caldav=caldav,
                db=db,
                direction=pair["direction"],
                conflict_resolution=pair["conflict_resolution"],
                delete_propagation=s["delete_propagation"],
                sync_past_days=s["sync_past_days"],
                sync_future_days=s["sync_future_days"],
                dry_run=dry_run,
                pair=pair["name"],
                send_invitations=s["send_invitations"],
                decider=decider,
            )
        )
    return engines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CalDAVSync - Google <-> Nextcloud calendar sync")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run a single sync and exit")
    mode.add_argument("--daemon", action="store_true", help="sync continuously at the configured interval")
    mode.add_argument("--adopt", action="store_true",
                      help="link pre-existing events across both sides by content (one-time migration aid)")
    mode.add_argument("--setup", action="store_true",
                      help="interactive wizard to create config.yaml")
    mode.add_argument("--review", action="store_true",
                      help="interactively approve/skip/ignore each proposed change")
    parser.add_argument("--dry-run", action="store_true", help="report actions without writing changes")
    parser.add_argument("--apply", action="store_true",
                        help="with --adopt: actually write the links (default is preview only)")
    parser.add_argument("--config", default="config.yaml", help="path to config file")
    args = parser.parse_args(argv)

    if args.setup:
        # Runs before load_config — config.yaml may not exist yet.
        from setup_wizard import run_setup
        return run_setup(args.config)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config["logging"]["level"], config["logging"]["file"])
    log = logging.getLogger("caldavsync")

    decider = None
    if args.review:
        from review import make_review_decider
        decider = make_review_decider()

    db = SyncDB(config["sync"]["state_db"])
    try:
        engines = build_engines(config, db, args.dry_run, decider=decider)
    except (CalDAVError, FileNotFoundError) as exc:
        log.error("Startup failed: %s", exc)
        db.close()
        return 1

    def sync_all() -> None:
        for engine in engines:
            log.info("Syncing pair %r", engine.pair)
            try:
                engine.run_once()
            except Exception:
                # A transient failure on one pair (e.g. a DNS blip) must not
                # abort the others; the next run picks it back up.
                log.exception("Pair %r failed; continuing with the next", engine.pair)

    def adopt_all() -> None:
        from adopt import adopt_pair
        if not args.apply:
            log.info("ADOPT preview (no changes written). Re-run with --apply to link.")
        totals = {"matched": 0, "leftover_google": 0, "leftover_caldav": 0}
        for engine in engines:
            st = adopt_pair(engine, apply=args.apply)
            totals["matched"] += st.matched
            totals["leftover_google"] += st.leftover_google
            totals["leftover_caldav"] += st.leftover_caldav
        log.info("ADOPT %s totals: matched=%d, would-create-on-caldav(google-only)=%d, "
                 "would-create-on-google(caldav-only)=%d",
                 "applied" if args.apply else "preview",
                 totals["matched"], totals["leftover_google"], totals["leftover_caldav"])

    try:
        if args.adopt:
            adopt_all()
        elif args.review:
            from review import ReviewQuit
            print("Review mode — for each change: [a]pply / [s]kip / [i]gnore forever / [q]uit "
                  "(Enter = skip).")
            try:
                sync_all()
            except ReviewQuit:
                log.info("Review stopped by user.")
        elif args.once:
            sync_all()
        else:
            interval = config["sync"]["interval_seconds"]
            log.info("Daemon mode: syncing every %ds (Ctrl+C to stop)", interval)
            while True:
                try:
                    sync_all()
                except Exception:
                    log.exception("Sync run failed; will retry next interval")
                time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Interrupted; shutting down")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
