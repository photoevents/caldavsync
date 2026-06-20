# Contributing to CalDAVSync

Thanks for your interest! CalDAVSync aims to do one thing well: reliably sync
calendar events between Google Calendar and any CalDAV server. Contributions
that keep it focused, self-hostable, and dependency-light are very welcome.

## Development setup

```bash
git clone <your-fork-url> caldavsync
cd caldavsync
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml             # then fill in your details
```

## Running the tests

The test suites are fully offline — they use in-memory fakes for Google and
CalDAV, so no network or credentials are needed:

```bash
python test_phase2.py    # bidirectional reconcile logic
python test_phase3.py    # recurrence, attendees, colors, multi-pair, config
```

Please add a test for any behavior change. New reconcile branches belong in
`test_phase2.py`; new field/mapping/config behavior in `test_phase3.py`.

## Project layout

| File | Responsibility |
|------|----------------|
| `main.py` | CLI + per-pair orchestration |
| `config_loader.py` | Config loading / validation / normalization |
| `google_client.py` | Google Calendar API (auth, read, write) |
| `caldav_client.py` | CalDAV/Nextcloud client |
| `ical_convert.py` | Normalized event ↔ iCalendar |
| `mappings.py` | Color and attendee-status lookups |
| `sync_engine.py` | Reconciliation, conflicts, deletions |
| `sync_db.py` | SQLite sync state |

## Guidelines

- **Keep it self-hosted and simple.** No mandatory cloud services; Docker stays
  optional.
- **Match the surrounding style** — type hints, small focused functions, clear
  log messages.
- **Don't break the flat layout.** It's intentional so `python main.py` works
  from a checkout.
- **Never commit secrets.** `config.yaml`, `credentials.json`, `token.json`,
  `sync.db`, and logs are gitignored — keep it that way.
- **Recurring-event overrides** (per-instance edits) are a known gap; PRs that
  add them with tests are especially appreciated.

## Reporting issues

Include your CalDAV server (Nextcloud/Radicale/Baïkal/SOGo + version), Python
version, the relevant log lines (`caldavsync.log`, scrub anything sensitive),
and steps to reproduce.
