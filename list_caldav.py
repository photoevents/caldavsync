"""Utility: list the CalDAV (Nextcloud) calendars the configured user can see,
with their display names and whether they're writable.

Run: python list_caldav.py
"""

from __future__ import annotations

import caldav

from config_loader import load_config


def main() -> None:
    n = load_config("config.yaml")["nextcloud"]
    client = caldav.DAVClient(url=n["url"], username=n["username"], password=n["password"])
    principal = client.principal()

    print("\nDISPLAY NAME")
    print("-" * 50)
    for cal in principal.calendars():
        print(cal.get_display_name())
    print()


if __name__ == "__main__":
    main()
