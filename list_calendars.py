"""Utility: authorize once and list all Google calendars with their IDs and
access roles. Use the printed calendar_id values when configuring
calendar_pairs in config.yaml.

Run: python list_calendars.py
"""

from __future__ import annotations

from config_loader import load_config
from google_client import GoogleClient


def main() -> None:
    cfg = load_config("config.yaml")
    g = cfg["google"]
    # calendar_id here is irrelevant; we only use the authorized service.
    client = GoogleClient(g["credentials_file"], g["token_file"], "primary")

    items = client.service.calendarList().list().execute().get("items", [])
    items.sort(key=lambda c: (not c.get("primary", False), c.get("summary", "").lower()))

    print(f"\n{'ACCESS':<8} {'PRIMARY':<8} SUMMARY  ->  CALENDAR_ID")
    print("-" * 90)
    for c in items:
        print(
            f"{c.get('accessRole',''):<8} "
            f"{'yes' if c.get('primary') else '':<8} "
            f"{c.get('summary','(no name)')}  ->  {c['id']}"
        )
    print(f"\n{len(items)} calendars. "
          "accessRole: owner/writer = editable (bidirectional ok); "
          "reader = one-way only (caldav side can't push back).")


if __name__ == "__main__":
    main()
