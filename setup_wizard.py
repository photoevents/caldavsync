"""Interactive first-run setup wizard.

Builds config.yaml by listing your Google and CalDAV calendars and letting you
map them into sync pairs, choosing per-pair direction and source-of-truth.

Run: python main.py --setup

The config-assembly logic (build_config) is kept separate from the prompting
so it can be tested without interaction.
"""

from __future__ import annotations

import getpass
import os
from typing import Any, Optional

import caldav
import yaml

from google_client import GoogleClient

DIRECTIONS = ["bidirectional", "google_to_caldav", "caldav_to_google"]
CONFLICTS = ["newest_wins", "google_wins", "caldav_wins"]


# --- pure assembly (testable) -----------------------------------------

def build_config(
    nextcloud: dict[str, str],
    google_files: dict[str, str],
    pairs: list[dict[str, Any]],
    sync_options: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a config dict ready to be written as config.yaml."""
    return {
        "google": {
            "credentials_file": google_files["credentials_file"],
            "token_file": google_files["token_file"],
        },
        "nextcloud": nextcloud,
        "calendar_pairs": pairs,
        "sync": sync_options,
        "logging": {"level": "INFO", "file": "caldavsync.log"},
    }


def default_sync_options(past_days: int = 30, future_days: int = 365) -> dict[str, Any]:
    return {
        "direction": "bidirectional",
        "conflict_resolution": "newest_wins",
        "interval_seconds": 300,
        "sync_past_days": past_days,
        "sync_future_days": future_days,
        "delete_propagation": True,
        "send_invitations": False,
        "state_db": "sync.db",
    }


# --- prompt helpers ----------------------------------------------------

def _ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def _ask_choice(prompt: str, choices: list[str], default: str) -> str:
    while True:
        val = _ask(f"{prompt} ({'/'.join(choices)})", default)
        if val in choices:
            return val
        print(f"  Please choose one of: {', '.join(choices)}")


def _ask_yesno(prompt: str, default: bool = False) -> bool:
    val = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not val else val.startswith("y")


def _ask_int(prompt: str, default: int) -> int:
    while True:
        val = _ask(prompt, str(default))
        try:
            return int(val)
        except ValueError:
            print("  Please enter a number.")


# --- listing (reuses the existing clients) ----------------------------

def _list_google(credentials_file: str, token_file: str) -> list[tuple[str, str, str]]:
    gc = GoogleClient(credentials_file, token_file, "primary")
    items = gc.service.calendarList().list().execute().get("items", [])
    items.sort(key=lambda c: (not c.get("primary", False), c.get("summary", "").lower()))
    return [(c["id"], c.get("summary", "(no name)"), c.get("accessRole", "")) for c in items]


def _list_caldav(url: str, username: str, password: str) -> list[str]:
    client = caldav.DAVClient(url=url, username=username, password=password)
    return [str(c.get_display_name()) for c in client.principal().calendars()]


# --- the wizard --------------------------------------------------------

def run_setup(config_path: str = "config.yaml") -> int:
    print("=== CalDAVSync setup ===\n")
    if os.path.exists(config_path) and not _ask_yesno(
        f"{config_path} already exists. Overwrite?", False
    ):
        print("Aborted; nothing changed.")
        return 1

    # 1. Nextcloud / CalDAV
    print("[1/4] Nextcloud / CalDAV server")
    url = _ask("  CalDAV URL", "https://nextcloud.example.com/remote.php/dav")
    username = _ask("  Username")
    password = getpass.getpass("  App password (input hidden): ").strip()
    try:
        caldav_cals = _list_caldav(url, username, password)
    except Exception as exc:
        print(f"  Could not connect to CalDAV: {exc}")
        return 1
    if not caldav_cals:
        print("  No CalDAV calendars found for this user.")
        return 1
    print(f"  Connected — {len(caldav_cals)} calendars found.\n")

    # 2. Google
    print("[2/4] Google Calendar")
    credentials_file = _ask("  credentials.json path", "credentials.json")
    token_file = "token.json"
    if not os.path.exists(credentials_file):
        print(f"  {credentials_file!r} not found — download a Desktop-app OAuth client first.")
        return 1
    print("  Authorizing (a browser window will open; log in and approve)...")
    try:
        google_cals = _list_google(credentials_file, token_file)
    except Exception as exc:
        print(f"  Google authorization failed: {exc}")
        return 1
    print(f"  Authorized — {len(google_cals)} calendars found.\n")

    # 3. Map pairs
    print("[3/4] Map calendars into sync pairs")
    print("  Google calendars:")
    for i, (_cid, summary, role) in enumerate(google_cals, 1):
        print(f"    {i:2}) {summary}  [{role}]")
    print("  Nextcloud calendars:")
    for i, name in enumerate(caldav_cals, 1):
        print(f"    {i:2}) {name}")
    print("  (reader-only Google calendars should use direction google_to_caldav)\n")

    pairs: list[dict[str, Any]] = []
    names: set[str] = set()
    while True:
        msg = "  Add another pair?" if pairs else "  Add a pair?"
        if not _ask_yesno(msg, default=not pairs):
            break
        g = _pick("  Google calendar number", google_cals)
        if g is None:
            continue
        c = _pick("  Nextcloud calendar number", caldav_cals)
        if c is None:
            continue
        gid, gsummary, grole = g
        cname = c if isinstance(c, str) else c
        direction = _ask_choice("  Direction", DIRECTIONS, "bidirectional")
        conflict = _ask_choice(
            "  Source of truth on conflict", CONFLICTS, "newest_wins"
        )
        name = _ask("  Pair name", cname)
        if name in names:
            print(f"  A pair named {name!r} already exists; pick another name.")
            continue
        names.add(name)
        pairs.append(
            {
                "name": name,
                "google_calendar_id": gid,
                "caldav_calendar_name": cname,
                "direction": direction,
                "conflict_resolution": conflict,
            }
        )
        print(f"  Added pair {name!r}  ({gsummary} <-> {cname}, {direction}).\n")

    if not pairs:
        print("  No pairs configured — nothing to write.")
        return 1

    # 4. Options
    print("[4/4] Sync window")
    past = _ask_int("  Sync how many days into the past", 30)
    future = _ask_int("  Sync how many days into the future", 365)
    sync_options = default_sync_options(past, future)

    config = build_config(
        {"url": url, "username": username, "password": password},
        {"credentials_file": credentials_file, "token_file": token_file},
        pairs,
        sync_options,
    )
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, sort_keys=False, allow_unicode=True)

    print(f"\nWrote {config_path}  ({len(pairs)} pair(s)).")
    print("Your password is stored there in plain text; the file is gitignored.")
    print("Next steps:")
    print("  python backup_caldav.py        # back up your CalDAV calendars first")
    print("  python main.py --once --dry-run  # preview, writes nothing")
    return 0


def _pick(prompt: str, items: list):
    """Prompt for a 1-based index into items; return the item or None to retry."""
    raw = _ask(prompt)
    if not raw.isdigit():
        print("  Please enter a number from the list.")
        return None
    idx = int(raw)
    if not 1 <= idx <= len(items):
        print(f"  Out of range (1-{len(items)}).")
        return None
    return items[idx - 1]
