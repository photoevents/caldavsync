# CalDAVSync

**Bidirectional sync between Google Calendar and any CalDAV server (Nextcloud,
Radicale, Baïkal, SOGo, …).** Self-hosted, privacy-first, MIT licensed.

Google Calendar doesn't speak CalDAV and Nextcloud doesn't speak Google's API,
so self-hosters who collaborate with Google Calendar users are usually stuck
with read-only iCal feeds (12–48 h delay) or paid bridge services. CalDAVSync
fills that gap: a single Python tool, SQLite for state, cron or Docker for
scheduling — no inbound webhooks, no third-party cloud, credentials never leave
your machine.

## Why this exists — and why it isn't a Nextcloud app

This was originally meant to be a *Nextcloud app*. Two things changed that:

- **Nothing free did the job.** The existing bidirectional Google ↔ CalDAV
  bridges were paid services or abandoned; the free options were one-way iCal
  feeds with a 12–48 h delay. So it had to be built.
- **A Nextcloud app would have been the wrong shape.** It would only ever work
  on Nextcloud, be locked to PHP and the Nextcloud release cycle, and need a
  server-side OAuth flow. A standalone tool instead works with **any** CalDAV
  server (Nextcloud, Radicale, Baïkal, SOGo…), runs anywhere, and keeps your
  credentials on your own machine.

So CalDAVSync is deliberately small and self-hosted — no groupware suite, no
cloud middleman.

## Features

- **Bidirectional** — create / edit / delete flows both ways, or one-way if you
  prefer (`direction: google_to_caldav | caldav_to_google | bidirectional`).
- **Conflict resolution** — `newest_wins`, `google_wins`, or `caldav_wins`.
- **Recurring events** — series sync with their `RRULE`/`EXDATE`/`RDATE`.
- **Attendees & organizer** — synced both ways (response status ↔ `PARTSTAT`).
  Invitation emails are **never** sent unless you opt in (`send_invitations`).
- **Colors & categories** — Google `colorId` ↔ iCal `COLOR`; categories kept.
- **Multiple calendar pairs** — sync many calendars in one run, each with its
  own isolated sync state.
- **Dry-run** — `--dry-run` reports exactly what would change, writing nothing.

## Quick start

1. **Install dependencies**
   ```bash
   python -m pip install -r requirements.txt
   ```
2. **Google OAuth2 credentials** — Google Cloud Console → APIs & Services →
   Credentials → Create OAuth client ID → **Desktop app**. Download the JSON as
   `credentials.json` in this folder.
3. **Nextcloud app password** — Nextcloud → Settings → Security → Create new app
   password (use this, *not* your login password).
4. **Configure** — easiest is the interactive wizard, which lists your Google
   and CalDAV calendars and writes `config.yaml` for you (asking per pair which
   direction and which side wins conflicts):
   ```bash
   python main.py --setup
   ```
   Prefer to do it by hand? Copy `config.example.yaml` to `config.yaml` and edit
   it — set the Nextcloud `password` and make `calendar_name` match an existing
   calendar's display name.
5. **First run** (opens a browser once to authorize, then caches `token.json`):
   ```bash
   python main.py --once --dry-run      # preview
   python main.py --once                # do it
   ```

## Running

```bash
python main.py --once              # single sync run
python main.py --once --dry-run    # show what would sync, change nothing
python main.py --daemon            # continuous sync at the configured interval
python main.py --review            # approve/skip/ignore each change interactively
```

### Reviewing changes interactively

`--review` walks you through every change the sync wants to make and asks per
event: **[a]pply**, **[s]kip** (just this run), **[i]gnore forever**, or
**[q]uit**. Events you ignore are remembered, so automatic `--once` / `--daemon`
runs will silently skip them from then on. Handy for one-off events you never
want mirrored, or for cautiously working through the first few syncs.

**Acceptance test:** create / edit / delete an event on *either* side (try a
recurring event and one with attendees/color too), run `python main.py --once`,
and confirm the change appears on the other side.

## Deployment

### Docker (recommended for always-on hosts)

> First-time OAuth needs a browser, which a container doesn't have. Authorize
> once on a desktop (`python main.py --once`) to create `token.json`, then mount
> that token in. It auto-refreshes, so the container never needs a browser again.

In `config.yaml`, point state and logs at the persistent volume:
```yaml
sync:
  state_db: "/data/sync.db"
logging:
  file: "/data/caldavsync.log"
```
Then:
```bash
docker compose up -d --build
docker compose logs -f          # watch it sync
```
See `docker-compose.yml` for the mounts.

### cron

```cron
*/5 * * * * cd /opt/caldavsync && /usr/bin/python3 main.py --once >> /var/log/caldavsync.log 2>&1
```

### systemd

```ini
[Unit]
Description=CalDAVSync
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/caldavsync
ExecStart=/usr/bin/python3 main.py --daemon
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

## Multiple calendars

Replace the single `calendar_id` / `calendar_name` with a `calendar_pairs:` list
(see `config.example.yaml`). Each pair keeps independent sync state, so the same
event UID can exist in different pairs without collision. A pair may override
`direction` and `conflict_resolution` — e.g. one-way `google_to_caldav` for a
read-only subscribed calendar, or `caldav_wins` for calendars where your CalDAV
server is the source of truth.

## Migrating from an existing one-way copy (adoption)

If your CalDAV calendars already contain a **one-way copy** of your Google
events (e.g. from an earlier migration) and that copy did **not** preserve the
iCalendar UIDs, the same event has a different UID on each side. A naive first
sync would treat them as unrelated and create duplicates everywhere.

`--adopt` fixes this: it matches the existing events on both sides by content
(title + start time) and records a link between them — writing to neither
calendar. Afterwards only genuinely new events sync.

```bash
python backup_caldav.py                 # 1. safety net: export Nextcloud calendars to .ics
python main.py --adopt                   # 2. PREVIEW: how many would link / remain
python main.py --adopt --apply           # 3. write the links into sync.db (local only)
python main.py --once --dry-run          # 4. review: should be mostly "unchanged"
python main.py --once                    # 5. sync the real remaining differences
```

For near-misses adoption can't auto-match (a title edited on one side), link them
by hand and optionally pick a winner:

```bash
python link_events.py "<pair>" "<google_uid>" "<caldav_uid>" --prefer caldav
```

Helper scripts: `list_calendars.py` (Google calendar IDs) and `list_caldav.py`
(Nextcloud calendar names).

## Configuration reference

| Key | Default | Meaning |
|-----|---------|---------|
| `sync.direction` | — | `bidirectional`, `google_to_caldav`, `caldav_to_google` |
| `sync.conflict_resolution` | `newest_wins` | `newest_wins` / `google_wins` / `caldav_wins` |
| `sync.interval_seconds` | `300` | daemon sync interval |
| `sync.sync_past_days` | `30` | how far back to sync |
| `sync.sync_future_days` | `365` | how far ahead to sync |
| `sync.delete_propagation` | `true` | mirror deletions |
| `sync.send_invitations` | `false` | email attendees on changes |
| `sync.state_db` | `sync.db` | SQLite state path |
| `logging.level` / `logging.file` | `INFO` / `caldavsync.log` | logging |

## Supported CalDAV servers

Anything speaking standard CalDAV: **Nextcloud** (primary target), **Radicale**,
**Baïkal**, **SOGo**, and others. Only the `nextcloud.url` /`username`/`password`
/`calendar_name` fields change.

## Troubleshooting

- **`insufficient permission` / scope errors** — an old read-only token. Delete
  `token.json` and re-run to re-authorize (the client detects the scope gap).
- **`CalDAV calendar 'X' not found`** — the error lists available calendars;
  match `calendar_name` to one exactly (display name, case-sensitive).
- **Recurring instance edits not syncing** — modified single occurrences
  (overrides) are intentionally skipped to protect the series master; they're
  logged as `skipped_overrides`.
- **Nothing happens for old/far-future events** — widen `sync_past_days` /
  `sync_future_days`.

## Development & tests

The test suites are fully offline (in-memory fakes — no network or credentials):
```bash
python test_phase2.py    # reconcile logic
python test_phase3.py    # recurrence, attendees, colors, multi-pair, config
```
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Project layout

| File | Role |
|------|------|
| `main.py` | CLI entry point + per-pair orchestration |
| `config_loader.py` | Loads / validates / normalizes config |
| `google_client.py` | Google Calendar OAuth2 + read/write |
| `caldav_client.py` | CalDAV/Nextcloud connect, read, create/update/delete |
| `ical_convert.py` | Normalized event ↔ iCalendar (recurrence, attendees, color) |
| `mappings.py` | Color (`colorId`↔CSS) and attendee-status lookups |
| `sync_engine.py` | Bidirectional reconcile, conflicts, deletions, per-pair state |
| `sync_db.py` | SQLite sync-state + log, scoped per calendar pair |

Secrets (`config.yaml`, `credentials.json`, `token.json`) and runtime files
(`sync.db`, `*.log`) are gitignored.

## Project status & expectations

I'm not a professional developer — I built this for my own setup and decided to
share it because I've gotten a lot out of open source over the years and like
giving something back.

Treat it as **a working base to use and adapt**, not a supported product. I'm
not planning to actively maintain it or work through other people's issues.
Fork it, change it, make it yours. PRs that help the next person are welcome
(see [CONTRIBUTING.md](CONTRIBUTING.md)), but please don't expect a support desk.

## Safety & your data

This tool writes to *both* your calendars, so it's built to be cautious:

- **Back up first.** `python backup_caldav.py` exports your CalDAV calendars to
  `.ics` files before you change anything (Google keeps its own history too).
  Do this before the first real sync.
- **Dry-run everything.** `--once --dry-run` shows exactly what *would* change
  and writes nothing. Use it before every real run while setting up.
- **No blind duplication.** When adopting an existing one-way copy, events are
  matched by content and *linked*, never blindly recreated (see the adoption
  guide above).
- **You choose the winner.** Per calendar you set the source of truth
  (`conflict_resolution`) and direction; read-only calendars can be one-way only.
- **No surprise emails.** Attendee invitations are never sent unless you
  explicitly opt in (`send_invitations`).
- **Secrets stay local.** `config.yaml`, `credentials.json`, `token.json`,
  `sync.db` and backups are gitignored and never leave your machine.
- **Bounded scope.** Only events within the configured time window are touched;
  your deep history is left alone.

## Support

If this saved you some time and you'd like to say thanks, a small donation is
always welcome — see the **Sponsor** button at the top of the repo. Completely
optional; using and improving it is thanks enough.

## License

MIT — see [LICENSE](LICENSE).
