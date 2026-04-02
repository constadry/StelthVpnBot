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
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

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
) -> None:
    async with aiosqlite.connect(_db_path) as db:
        await db.execute("""
            INSERT OR REPLACE INTO inbounds (telegram_id, inbound_id, port, client_uuid)
            VALUES (?, ?, ?, ?)
        """, (telegram_id, inbound_id, port, client_uuid))
        await db.commit()


async def get_used_ports() -> set[int]:
    async with aiosqlite.connect(_db_path) as db:
        async with db.execute("SELECT port FROM inbounds") as cur:
            rows = await cur.fetchall()
            return {r[0] for r in rows}
