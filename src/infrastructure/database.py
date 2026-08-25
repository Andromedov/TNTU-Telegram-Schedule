import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from config import DB_PATH

EXPECTED_COLUMNS = {
    'username': 'TEXT',
    'group_name': 'TEXT',
    'notify_10_min': 'BOOLEAN DEFAULT 1',
    'reminder_offset': 'INTEGER DEFAULT 10',
    'notify_evening': 'BOOLEAN DEFAULT 1',
    'is_paused': 'BOOLEAN DEFAULT 0',
    'notify_schedule_update': 'BOOLEAN DEFAULT 1',
    'language': "TEXT DEFAULT 'uk'",
    'first_class_reminder_offset': 'INTEGER',
    'morning_digest': 'BOOLEAN DEFAULT 0',
    'morning_digest_hour': 'INTEGER DEFAULT 7',
    'notifications_muted_until': 'TEXT',
    'notify_lectures': 'BOOLEAN DEFAULT 1',
    'notify_laboratories': 'BOOLEAN DEFAULT 1',
    'notify_practicals': 'BOOLEAN DEFAULT 1',
    'quiet_hours_start': 'INTEGER',
    'quiet_hours_end': 'INTEGER',
    'created_at': 'TEXT',
    'last_seen_at': 'TEXT',
}


async def init_db():
    """Ініціалізація БД та автоматична міграція колонок."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
                        CREATE TABLE IF NOT EXISTS users
                        (
                            user_id
                            INTEGER
                            PRIMARY
                            KEY
                        )
                        """)

        db.row_factory = aiosqlite.Row
        async with db.execute("PRAGMA table_info(users)") as cursor:
            columns = await cursor.fetchall()
            existing_columns = [col['name'] for col in columns]

        for col_name, col_type in EXPECTED_COLUMNS.items():
            if col_name not in existing_columns:
                try:
                    await db.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
                    logging.info(f"База даних: Додано нову колонку '{col_name}'")
                except Exception as e:
                    logging.error(f"Помилка створення колонки {col_name}: {e}")

        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_username_nocase ON users(username COLLATE NOCASE)")

        await db.commit()


async def add_or_update_user(user_id: int, group_name: str = None, language: str = 'uk'):
    async with aiosqlite.connect(DB_PATH) as db:
        if group_name:
            await db.execute(
                """
                             INSERT INTO users (user_id, group_name, language)
                             VALUES (?, ?, ?) ON CONFLICT(user_id) DO
                             UPDATE SET group_name=excluded.group_name
                             """,
                (user_id, group_name, language),
            )
        else:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, language) VALUES (?, ?)",
                (user_id, language),
            )
        await db.commit()


async def record_user_activity(
    user_id: int,
    language: str = 'uk',
    occurred_at: datetime | None = None,
    username: str | None = None,
):
    """Record an interaction and the user's latest public Telegram username."""
    timestamp = (occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='seconds')
    normalized_username = username.lstrip('@').strip() if username else None
    normalized_username = normalized_username or None
    async with aiosqlite.connect(DB_PATH) as db:
        if normalized_username:
            await db.execute(
                "UPDATE users SET username = NULL WHERE user_id != ? AND username = ? COLLATE NOCASE",
                (user_id, normalized_username),
            )
        await db.execute(
            """
            INSERT INTO users (user_id, username, language, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                last_seen_at=excluded.last_seen_at
            """,
            (user_id, normalized_username, language, timestamp, timestamp),
        )
        await db.commit()


async def clear_user_group(user_id: int):
    """Очищає групу наявного користувача, не змінюючи його налаштування."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET group_name = NULL WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()


async def get_user_by_username(username: str):
    """Find a user by their latest known Telegram username, case-insensitively."""
    normalized_username = username.lstrip('@').strip()
    if not normalized_username:
        return None
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (normalized_username,),
        ) as cursor:
            return await cursor.fetchone()


async def delete_user_data(user_id: int) -> bool:
    """Permanently delete all persisted data associated with a Telegram user ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_setting(user_id: int, setting: str, value: Any):
    if setting not in EXPECTED_COLUMNS:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE users SET {setting} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()


async def get_active_users():
    """Отримати всіх користувачів, у яких не увімкнена пауза."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE is_paused = 0") as cursor:
            return await cursor.fetchall()


async def get_users_batch(limit: int, offset: int):
    """Отримати користувачів порціями (батчами)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users LIMIT ? OFFSET ?", (limit, offset)) as cursor:
            return await cursor.fetchall()


async def get_statistics(now: datetime | None = None) -> dict:
    """Collect aggregate, privacy-preserving statistics for the admin dashboard."""
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    active_24h_since = (current_time - timedelta(hours=24)).isoformat(timespec='seconds')
    active_7d_since = (current_time - timedelta(days=7)).isoformat(timespec='seconds')
    current_timestamp = current_time.isoformat(timespec='seconds')

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN is_paused = 0 THEN 1 ELSE 0 END), 0) AS notifications_enabled,
                COALESCE(SUM(CASE WHEN is_paused = 1 THEN 1 ELSE 0 END), 0) AS notifications_paused,
                COALESCE(SUM(CASE WHEN group_name IS NOT NULL AND group_name != '' THEN 1 ELSE 0 END), 0)
                    AS users_with_group,
                COUNT(DISTINCT CASE WHEN group_name IS NOT NULL AND group_name != '' THEN group_name END)
                    AS distinct_groups,
                COALESCE(SUM(CASE WHEN notify_10_min = 1 THEN 1 ELSE 0 END), 0) AS reminders_enabled,
                COALESCE(SUM(CASE WHEN notify_evening = 1 THEN 1 ELSE 0 END), 0) AS evening_enabled,
                COALESCE(SUM(CASE WHEN notify_schedule_update = 1 THEN 1 ELSE 0 END), 0)
                    AS schedule_updates_enabled,
                COALESCE(SUM(CASE WHEN morning_digest = 1 THEN 1 ELSE 0 END), 0) AS digest_enabled,
                COALESCE(SUM(CASE WHEN quiet_hours_start IS NOT NULL AND quiet_hours_end IS NOT NULL
                    THEN 1 ELSE 0 END), 0) AS quiet_hours_enabled,
                COALESCE(SUM(CASE WHEN notifications_muted_until > ? THEN 1 ELSE 0 END), 0)
                    AS temporarily_muted,
                COALESCE(SUM(CASE WHEN language = 'uk' THEN 1 ELSE 0 END), 0) AS language_uk,
                COALESCE(SUM(CASE WHEN language = 'en' THEN 1 ELSE 0 END), 0) AS language_en,
                COALESCE(SUM(CASE WHEN language NOT IN ('uk', 'en') OR language IS NULL THEN 1 ELSE 0 END), 0)
                    AS language_other,
                COALESCE(SUM(CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END), 0) AS active_24h,
                COALESCE(SUM(CASE WHEN last_seen_at >= ? THEN 1 ELSE 0 END), 0) AS active_7d,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS new_24h,
                COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS new_7d
            FROM users
            """,
            (
                current_timestamp,
                active_24h_since,
                active_7d_since,
                active_24h_since,
                active_7d_since,
            ),
        ) as cursor:
            statistics = dict(await cursor.fetchone())

        async with db.execute(
            """
            SELECT group_name, COUNT(*) AS count
            FROM users
            WHERE group_name IS NOT NULL AND group_name != ''
            GROUP BY group_name
            ORDER BY count DESC, group_name ASC
            LIMIT 5
            """
        ) as cursor:
            top_groups = await cursor.fetchall()

        statistics['top_groups'] = top_groups
        return statistics


def database_size_bytes() -> int:
    try:
        return os.path.getsize(DB_PATH)
    except OSError:
        return 0
