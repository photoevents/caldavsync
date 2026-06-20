"""Export the configured Nextcloud calendars to .ics files as a safety net.

Writes one importable .ics per calendar into backups/<timestamp>/. Run this
before the first real multi-calendar sync so you can restore if anything goes
wrong. Backups are gitignored (they contain real event data).

Run: python backup_caldav.py
"""

from __future__ import annotations

import datetime
import os

import caldav
from icalendar import Calendar

from config_loader import load_config


def main() -> None:
    cfg = load_config("config.yaml")
    n = cfg["nextcloud"]
    wanted = {p["caldav_calendar_name"] for p in cfg["calendar_pairs"]}

    client = caldav.DAVClient(url=n["url"], username=n["username"], password=n["password"])
    principal = client.principal()

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join("backups", ts)
    os.makedirs(outdir, exist_ok=True)

    total = 0
    for cal in principal.calendars():
        name = cal.get_display_name()
        if name not in wanted:
            continue
        merged = Calendar()
        merged.add("prodid", "-//CalDAVSync backup//EN")
        merged.add("version", "2.0")
        count = 0
        for ev in cal.events():
            try:
                sub = Calendar.from_ical(ev.data)
            except Exception:
                continue
            for comp in sub.walk():
                if comp.name in ("VEVENT", "VTIMEZONE", "VTODO"):
                    merged.add_component(comp)
                    if comp.name == "VEVENT":
                        count += 1
        safe = name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        path = os.path.join(outdir, f"{safe}.ics")
        with open(path, "wb") as fh:
            fh.write(merged.to_ical())
        total += count
        print(f"  {name:<22} {count:>4} events -> {path}")

    print(f"\nBacked up {total} events into {outdir}")


if __name__ == "__main__":
    main()
