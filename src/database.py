import aiosqlite
from config import DB_PATH
import os

async def init_db():
    """Ініціалізація бази даних та створення таблиць, якщо їх немає."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT,
                notify_10_min BOOLEAN DEFAULT 1,
                notify_evening BOOLEAN DEFAULT 1,
                is_paused BOOLEAN DEFAULT 0
            )
        """)

        try:
            await db.execute("ALTER TABLE users ADD COLUMN notify_schedule_update BOOLEAN DEFAULT 1")
        except:
            pass

        await db.commit()

async def add_or_update_user(user_id: int, group_name: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if group_name:
            await db.execute("""
                INSERT INTO users (user_id, group_name) 
                VALUES (?, ?) 
                ON CONFLICT(user_id) DO UPDATE SET group_name=excluded.group_name
            """, (user_id, group_name))
        else:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def update_setting(user_id: int, setting: str, value: int):
    allowed_settings = ['notify_10_min', 'notify_evening', 'is_paused', 'notify_schedule_update']
    if setting not in allowed_settings:
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