# Deploying CalDAVSync on an always-on host (Docker)

This runs the sync continuously (daemon mode) with auto-restart. Do this once
you've validated the sync on your desktop — and **carry over your existing
`sync.db`** so the server continues from the already-established links instead
of re-adopting.

Paths below use `/opt/caldavsync` as an example; use wherever you keep your
container stacks.

## 0. Why the existing sync.db matters

`sync.db` holds the link between every Google event and its CalDAV counterpart
(including adopted/migrated events). If the server starts with an empty
database it will try to reconcile from scratch and may create duplicates. Copy
the working `sync.db` over.

## 1. Get the code onto the server

```bash
ssh you@your-server
mkdir -p /opt/caldavsync && cd /opt/caldavsync
git clone https://github.com/youruser/caldavsync.git .
mkdir -p data
```

## 2. Copy the gitignored files from your desktop

These never live in git, so transfer them manually (scp / a network share):

```bash
# config.yaml, credentials.json, token.json -> project root on the server
# sync.db                                    -> the data/ subfolder
scp config.yaml credentials.json token.json you@your-server:/opt/caldavsync/
scp sync.db you@your-server:/opt/caldavsync/data/
```

## 3. Point the config at the container's data volume

Edit `config.yaml` on the server so state and logs live on the mounted volume:

```yaml
sync:
  state_db: "/data/sync.db"
logging:
  file: "/data/caldavsync.log"
```

## 4. Sanity-check, then start

```bash
docker compose build
# one dry run — should be all "unchanged", 0 errors
docker compose run --rm caldavsync --once --dry-run
# if that looks right, start the daemon
docker compose up -d
docker compose logs -f
```

## Operating it

```bash
docker compose logs -f                 # follow sync activity
docker compose restart                 # restart
docker compose down                    # stop
docker compose run --rm caldavsync --once          # manual one-off sync
docker compose run --rm caldavsync --once --dry-run
```

The OAuth token (`token.json`) auto-refreshes and is written back to the host
file, so no browser is ever needed on the server after the first copy.

## Notes for specific environments

- **Read-only-root appliance OSes** (some NAS/home-server distros): if Docker
  can't write its config dir, point it at a writable path per command, e.g.
  `DOCKER_CONFIG="$PWD/.docker" docker compose ...` (prefix with `sudo` if your
  user isn't in the `docker` group).
- **Flaky container DNS** (e.g. a local Pi-hole as resolver): the bundled
  `docker-compose.yml` sets explicit public DNS (`1.1.1.1` / `8.8.8.8`) to keep
  name resolution reliable. Remove or change that block if you prefer your own.

## Alternative: cron (no Docker)

If you'd rather not use Docker and the host has Python 3:

```bash
cd /opt/caldavsync
python3 -m pip install -r requirements.txt
# crontab -e
*/5 * * * * cd /opt/caldavsync && /usr/bin/python3 main.py --once >> caldavsync.log 2>&1
```

Keep `state_db: "sync.db"` (or an absolute path) for the cron setup.
