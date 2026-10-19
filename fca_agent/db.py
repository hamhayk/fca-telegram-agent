"""SQLite storage: tablets, releases and per-tablet deliveries."""

import re
import sqlite3
from datetime import datetime
from pathlib import Path

from . import APP_NAME

SCHEMA = """
CREATE TABLE IF NOT EXISTS tablets (
    id            INTEGER PRIMARY KEY,
    chat_id       INTEGER NOT NULL UNIQUE,
    username      TEXT,
    full_name     TEXT,
    branch        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | active | removed
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS releases (
    id           INTEGER PRIMARY KEY,
    app_name     TEXT NOT NULL,
    version      TEXT NOT NULL,
    file_id      TEXT NOT NULL,
    file_name    TEXT NOT NULL,
    file_size    INTEGER,
    notes        TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',    -- pending | approved | cancelled
    uploaded_by  INTEGER NOT NULL,
    uploaded_at  TEXT NOT NULL,
    decided_at   TEXT
);
CREATE TABLE IF NOT EXISTS deliveries (
    id           INTEGER PRIMARY KEY,
    release_id   INTEGER NOT NULL REFERENCES releases(id),
    tablet_id    INTEGER NOT NULL REFERENCES tablets(id),
    status       TEXT NOT NULL,                      -- sent | failed | installed
    error        TEXT,
    sent_at      TEXT NOT NULL,
    confirmed_at TEXT,
    UNIQUE (release_id, tablet_id)
);
"""

# Matches "FcaSoft-v1.4.2.apk", "fcasoft_1.4.2.apk", "app-v2.0.0-beta.1.apk"
VERSION_RE = re.compile(r"v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)(?=\.apk$|[-_ ])", re.IGNORECASE)


def parse_apk_name(file_name: str) -> tuple[str, str] | None:
    """Return (app_name, version) from an APK file name, or None if no version is in it."""
    if not file_name.lower().endswith(".apk"):
        return None
    m = VERSION_RE.search(file_name)
    if not m:
        return None
    app = file_name[: m.start()].rstrip("-_ v").strip() or "App"
    return app, m.group(1)


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # ---- tablets -------------------------------------------------------
    def register_tablet(self, chat_id: int, username: str | None, full_name: str, branch: str | None):
        """Create a pending tablet, or return the existing one. Returns (row, created)."""
        row = self.tablet_by_chat(chat_id)
        if row and row["status"] != "removed":
            return row, False
        with self.conn:
            if row:  # a removed tablet asking again goes back to pending
                self.conn.execute(
                    "UPDATE tablets SET status='pending', username=?, full_name=?, branch=COALESCE(?, branch), registered_at=? WHERE id=?",
                    (username, full_name, branch, now(), row["id"]),
                )
            else:
                self.conn.execute(
                    "INSERT INTO tablets (chat_id, username, full_name, branch, registered_at) VALUES (?,?,?,?,?)",
                    (chat_id, username, full_name, branch, now()),
                )
        return self.tablet_by_chat(chat_id), True

    def tablet_by_chat(self, chat_id: int):
        return self.conn.execute("SELECT * FROM tablets WHERE chat_id=?", (chat_id,)).fetchone()

    def tablet(self, tablet_id: int):
        return self.conn.execute("SELECT * FROM tablets WHERE id=?", (tablet_id,)).fetchone()

    def set_tablet_status(self, tablet_id: int, status: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE tablets SET status=? WHERE id=?", (status, tablet_id))

    def set_branch(self, tablet_id: int, branch: str) -> bool:
        with self.conn:
            cur = self.conn.execute("UPDATE tablets SET branch=? WHERE id=?", (branch, tablet_id))
        return cur.rowcount > 0

    def tablets(self, status: str | None = None):
        if status:
            return self.conn.execute("SELECT * FROM tablets WHERE status=? ORDER BY branch, id", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM tablets WHERE status!='removed' ORDER BY status, branch, id").fetchall()

    # ---- releases ------------------------------------------------------
    def add_release(self, app_name, version, file_id, file_name, file_size, notes, uploaded_by) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO releases (app_name, version, file_id, file_name, file_size, notes, uploaded_by, uploaded_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (app_name, version, file_id, file_name, file_size, notes, uploaded_by, now()),
            )
        return cur.lastrowid

    def release(self, release_id: int):
        return self.conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()

    def latest_release(self):
        return self.conn.execute("SELECT * FROM releases WHERE status='approved' ORDER BY id DESC LIMIT 1").fetchone()

    def releases(self, limit: int = 10):
        return self.conn.execute("SELECT * FROM releases ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def decide_release(self, release_id: int, status: str) -> bool:
        """Move a pending release to approved/cancelled. False if it was already decided."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE releases SET status=?, decided_at=? WHERE id=? AND status='pending'",
                (status, now(), release_id),
            )
        return cur.rowcount > 0

    # ---- deliveries ----------------------------------------------------
    def record_delivery(self, release_id: int, tablet_id: int, ok: bool, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO deliveries (release_id, tablet_id, status, error, sent_at) VALUES (?,?,?,?,?)"
                " ON CONFLICT(release_id, tablet_id) DO UPDATE SET"
                "   status=CASE WHEN deliveries.status='installed' THEN 'installed' ELSE excluded.status END,"
                "   error=excluded.error, sent_at=excluded.sent_at",
                (release_id, tablet_id, "sent" if ok else "failed", error, now()),
            )

    def confirm_installed(self, release_id: int, tablet_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE deliveries SET status='installed', confirmed_at=? WHERE release_id=? AND tablet_id=? AND status!='installed'",
                (now(), release_id, tablet_id),
            )
        return cur.rowcount > 0

    def delivery_report(self, release_id: int):
        """Every active tablet with its delivery state for this release ('not sent' if none)."""
        return self.conn.execute(
            "SELECT t.id AS tablet_id, t.branch, t.full_name, t.chat_id,"
            "       COALESCE(d.status, 'not sent') AS status, d.error, d.sent_at, d.confirmed_at"
            " FROM tablets t LEFT JOIN deliveries d ON d.tablet_id=t.id AND d.release_id=?"
            " WHERE t.status='active' ORDER BY t.branch, t.id",
            (release_id,),
        ).fetchall()

    # ---- backup --------------------------------------------------------
    def backup(self, backup_dir: Path) -> Path:
        """Consistent copy of the database named <AppName>_backup_YYYY-MM-DD_HHmm.db."""
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"{APP_NAME}_backup_{datetime.now():%Y-%m-%d_%H%M}.db"
        dest = sqlite3.connect(target)
        with dest:
            self.conn.backup(dest)
        dest.close()
        return target
