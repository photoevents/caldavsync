"""SQLite-backed sync state for CalDAVSync.

Each row links one Google event to one CalDAV event. The canonical key is the
Google-side UID (`uid`); `caldav_uid` records the CalDAV VEVENT UID, which is
normally identical but CAN differ for events adopted from a migration that did
not preserve UIDs. Storing both lets the engine match the two sides even when
their UIDs diverge.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_state (
    pair TEXT NOT NULL DEFAULT 'default',
    uid TEXT NOT NULL,               -- canonical (Google-side) UID
    caldav_uid TEXT,                 -- CalDAV VEVENT UID (may differ after adoption)
    google_event_id TEXT,
    google_etag TEXT,
    google_updated TEXT,
    caldav_href TEXT,
    caldav_etag TEXT,
    caldav_updated TEXT,
    last_synced TEXT,
    sync_direction TEXT,
    PRIMARY KEY (pair, uid)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    pair TEXT,
    action TEXT,
    direction TEXT,
    uid TEXT,
    summary TEXT,
    details TEXT
);

-- Events the user chose to permanently ignore during --review. Automatic
-- runs skip these so they are never proposed again.
CREATE TABLE IF NOT EXISTS ignored (
    pair TEXT NOT NULL DEFAULT 'default',
    uid TEXT NOT NULL,
    summary TEXT,
    reason TEXT,
    created TEXT,
    PRIMARY KEY (pair, uid)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncDB:
    """Thin wrapper around the sync state SQLite database."""

    def __init__(self, path: str = "sync.db") -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Bring an older sync.db up to the current schema in place."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(sync_state)")}
        if "caldav_uid" not in cols:
            # Pre-mapping databases: add the column and assume both sides shared
            # a UID (true for events the tool itself created).
            self.conn.execute("ALTER TABLE sync_state ADD COLUMN caldav_uid TEXT")
            self.conn.execute("UPDATE sync_state SET caldav_uid = uid WHERE caldav_uid IS NULL")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sync_state_caldav_uid "
            "ON sync_state (pair, caldav_uid)"
        )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SyncDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- sync_state ----------------------------------------------------

    def get_state(self, uid: str, pair: str = "default") -> Optional[sqlite3.Row]:
        """Look up a link by its canonical (Google-side) UID."""
        cur = self.conn.execute(
            "SELECT * FROM sync_state WHERE pair = ? AND uid = ?", (pair, uid)
        )
        return cur.fetchone()

    def get_by_caldav_uid(self, caldav_uid: str, pair: str = "default") -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM sync_state WHERE pair = ? AND caldav_uid = ?", (pair, caldav_uid)
        )
        return cur.fetchone()

    def all_states(self, pair: str = "default") -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM sync_state WHERE pair = ?", (pair,))
        return cur.fetchall()

    def all_uids(self, pair: str = "default") -> set[str]:
        cur = self.conn.execute("SELECT uid FROM sync_state WHERE pair = ?", (pair,))
        return {row["uid"] for row in cur.fetchall()}

    def upsert_state(self, uid: str, pair: str = "default", **fields: Any) -> None:
        """Insert or update a link row. Only provided fields change.

        On insert, caldav_uid defaults to the canonical uid (the common case
        where both sides share a UID).
        """
        fields["last_synced"] = _now()
        existing = self.get_state(uid, pair)
        if existing is None:
            fields.setdefault("caldav_uid", uid)
            cols = ["pair", "uid", *fields.keys()]
            placeholders = ", ".join("?" for _ in cols)
            self.conn.execute(
                f"INSERT INTO sync_state ({', '.join(cols)}) VALUES ({placeholders})",
                [pair, uid, *fields.values()],
            )
        else:
            assignments = ", ".join(f"{k} = ?" for k in fields)
            self.conn.execute(
                f"UPDATE sync_state SET {assignments} WHERE pair = ? AND uid = ?",
                [*fields.values(), pair, uid],
            )
        self.conn.commit()

    def delete_state(self, uid: str, pair: str = "default") -> None:
        self.conn.execute("DELETE FROM sync_state WHERE pair = ? AND uid = ?", (pair, uid))
        self.conn.commit()

    # --- sync_log ------------------------------------------------------

    def log_action(
        self,
        action: str,
        direction: str,
        uid: str,
        summary: str = "",
        details: str = "",
        pair: str = "default",
    ) -> None:
        self.conn.execute(
            "INSERT INTO sync_log (timestamp, pair, action, direction, uid, summary, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), pair, action, direction, uid, summary, details),
        )
        self.conn.commit()

    # --- ignore list ---------------------------------------------------

    def is_ignored(self, uid: str, pair: str = "default") -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM ignored WHERE pair = ? AND uid = ?", (pair, uid)
        )
        return cur.fetchone() is not None

    def add_ignored(self, uid: str, pair: str = "default", summary: str = "", reason: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO ignored (pair, uid, summary, reason, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (pair, uid, summary, reason, _now()),
        )
        self.conn.commit()

    def remove_ignored(self, uid: str, pair: str = "default") -> None:
        self.conn.execute("DELETE FROM ignored WHERE pair = ? AND uid = ?", (pair, uid))
        self.conn.commit()

    def list_ignored(self, pair: str = "default") -> list[sqlite3.Row]:
        cur = self.conn.execute("SELECT * FROM ignored WHERE pair = ?", (pair,))
        return cur.fetchall()
