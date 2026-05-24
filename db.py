import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

_db_path: str = "/app/data/bot.db"
_backup_path: str = "/app/data/bot.db.bak"


def init_db_config(db_path: str, backup_path: str) -> None:
    global _db_path, _backup_path
    _db_path = db_path
    _backup_path = backup_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


async def init_db() -> None:
    """Create tables and enable WAL mode for safer concurrent access."""
    async with aiosqlite.connect(_db_path) as db:
        # WAL mode: readers don't block writers, survives crashes better
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id  INTEGER PRIMARY KEY,
                username     TEXT,
                full_name    TEXT,
                approved     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS inbounds (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id  INTEGER NOT NULL REFERENCES users(telegram_id),
                inbound_id   INTEGER NOT NULL,
                port         INTEGER NOT NULL UNIQUE,
                client_uuid  TEXT NOT NULL,
                sub_id       TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS moderators (
                telegram_id  INTEGER PRIMARY KEY,
                username     TEXT,
                added_by     INTEGER NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                inbound_id  INTEGER PRIMARY KEY,
                name        TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_clients (
                telegram_id  INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                client_uuid  TEXT NOT NULL,
                sub_id       TEXT NOT NULL,
                email        TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_locations (
                telegram_id  INTEGER NOT NULL,
                inbound_id   INTEGER NOT NULL,
                added_at     TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (telegram_id, inbound_id)
            )
        """)

        # Migrations for columns added after initial release
        for migration in [
            "ALTER TABLE inbounds ADD COLUMN sub_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE inbounds ADD COLUMN issued_by INTEGER",
        ]:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass  # column already exists

        await db.commit()
    logger.info("Database initialised at %s", _db_path)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

async def backup_db() -> None:
    """Hot backup using SQLite online backup API (safe while DB is in use)."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_backup)
    logger.info("Database backup written to %s", _backup_path)


def _sync_backup() -> None:
    import sqlite3
    src = sqlite3.connect(_db_path)
    dst = sqlite3.connect(_backup_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

async def upsert_user(telegram_id: int, username: Optional[str], full_name: Optional[str]) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT INTO users (telegram_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name
        """, (telegram_id, username, full_name))
        await db.commit()


async def is_approved(telegram_id: int) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT approved FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def approve_user(telegram_id: int) -> bool:
    """Returns True if user was found and approved, False if user not in DB."""
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            if not await cur.fetchone():
                return False
        await db.execute(
            "UPDATE users SET approved = 1 WHERE telegram_id = ?", (telegram_id,)
        )
        await db.commit()
        return True


async def revoke_user(telegram_id: int) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            if not await cur.fetchone():
                return False
        await db.execute(
            "UPDATE users SET approved = 0 WHERE telegram_id = ?", (telegram_id,)
        )
        await db.commit()
        return True


async def list_users() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.telegram_id, u.username, u.full_name, u.approved,
                   i.port, i.inbound_id, i.client_uuid, i.created_at AS inbound_created
            FROM users u
            LEFT JOIN inbounds i ON i.telegram_id = u.telegram_id
            ORDER BY u.created_at DESC
        """) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Inbounds
# ---------------------------------------------------------------------------

async def get_user_inbound(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM inbounds WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def save_inbound(
    telegram_id: int,
    inbound_id: int,
    port: int,
    client_uuid: str,
    sub_id: str = "",
    issued_by: Optional[int] = None,
) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT OR REPLACE INTO inbounds (telegram_id, inbound_id, port, client_uuid, sub_id, issued_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (telegram_id, inbound_id, port, client_uuid, sub_id, issued_by))
        await db.commit()


async def count_issued_by_admin(admin_id: int) -> int:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM inbounds WHERE issued_by = ?", (admin_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


# ---------------------------------------------------------------------------
# Moderators
# ---------------------------------------------------------------------------

async def add_moderator(telegram_id: int, username: Optional[str], added_by: int) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT OR REPLACE INTO moderators (telegram_id, username, added_by)
            VALUES (?, ?, ?)
        """, (telegram_id, username, added_by))
        await db.commit()


async def remove_moderator(telegram_id: int) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT telegram_id FROM moderators WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            if not await cur.fetchone():
                return False
        await db.execute("DELETE FROM moderators WHERE telegram_id = ?", (telegram_id,))
        await db.commit()
        return True


async def list_moderators() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT telegram_id, username, added_by, created_at FROM moderators ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def is_moderator(telegram_id: int) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT telegram_id FROM moderators WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            return bool(await cur.fetchone())


async def get_user_by_username(username: str) -> Optional[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT telegram_id, username, full_name FROM users WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_users_with_inbounds() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.telegram_id, u.username, u.full_name,
                   i.inbound_id, i.port, i.client_uuid, i.sub_id
            FROM users u
            JOIN inbounds i ON i.telegram_id = u.telegram_id
            ORDER BY i.created_at ASC
        """) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_used_ports() -> set[int]:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT port FROM inbounds") as cur:
            rows = await cur.fetchall()
            return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Locations (shared inbounds)
# ---------------------------------------------------------------------------

async def add_location(inbound_id: int, name: str) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO locations (inbound_id, name) VALUES (?, ?)",
            (inbound_id, name),
        )
        await db.commit()


async def remove_location(inbound_id: int) -> bool:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT inbound_id FROM locations WHERE inbound_id = ?", (inbound_id,)
        ) as cur:
            if not await cur.fetchone():
                return False
        await db.execute("DELETE FROM locations WHERE inbound_id = ?", (inbound_id,))
        await db.commit()
        return True


async def list_locations() -> list[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT inbound_id, name, created_at FROM locations ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# User clients (new subscription system)
# ---------------------------------------------------------------------------

async def save_user_client(
    telegram_id: int, client_uuid: str, sub_id: str, email: str
) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT OR REPLACE INTO user_clients (telegram_id, client_uuid, sub_id, email)
            VALUES (?, ?, ?, ?)
        """, (telegram_id, client_uuid, sub_id, email))
        await db.commit()


async def get_user_client(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_clients WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def mark_user_in_location(telegram_id: int, inbound_id: int) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO user_locations (telegram_id, inbound_id) VALUES (?, ?)",
            (telegram_id, inbound_id),
        )
        await db.commit()


async def get_user_locations(telegram_id: int) -> set[int]:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute(
            "SELECT inbound_id FROM user_locations WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            rows = await cur.fetchall()
            return {r[0] for r in rows}


async def get_users_for_migration() -> list[dict]:
    """Approved users with credentials from old or new system (for /migrateall)."""
    async with aiosqlite.connect(_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.telegram_id, u.username,
                   COALESCE(uc.client_uuid, i.client_uuid) AS client_uuid,
                   COALESCE(uc.sub_id,      i.sub_id)      AS sub_id,
                   COALESCE(uc.email, 'tg_' || u.telegram_id) AS email
            FROM users u
            LEFT JOIN user_clients uc ON uc.telegram_id = u.telegram_id
            LEFT JOIN inbounds     i  ON i.telegram_id  = u.telegram_id
            WHERE u.approved = 1
              AND (uc.client_uuid IS NOT NULL OR i.client_uuid IS NOT NULL)
        """) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
