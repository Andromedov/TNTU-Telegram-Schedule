import os
import sys
import tempfile
import unittest
from pathlib import Path

import aiosqlite


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from infrastructure import database  # noqa: E402


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_init_db_adds_language_to_existing_users(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "legacy.sqlite3")
            try:
                async with aiosqlite.connect(database.DB_PATH) as connection:
                    await connection.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, group_name TEXT)")
                    await connection.execute("INSERT INTO users (user_id, group_name) VALUES (1, 'СТс-21')")
                    await connection.commit()

                await database.init_db()

                user = await database.get_user(1)
                self.assertEqual("uk", user["language"])
                self.assertEqual("СТс-21", user["group_name"])
                self.assertIsNone(user["first_class_reminder_offset"])
                self.assertEqual(0, user["morning_digest"])
                self.assertEqual(7, user["morning_digest_hour"])
                self.assertIsNone(user["notifications_muted_until"])
                self.assertEqual(1, user["notify_lectures"])
                self.assertEqual(1, user["notify_laboratories"])
                self.assertEqual(1, user["notify_practicals"])
                self.assertIsNone(user["quiet_hours_start"])
                self.assertIsNone(user["quiet_hours_end"])
            finally:
                database.DB_PATH = original_path

    async def test_clear_user_group_preserves_user_and_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "users.sqlite3")
            try:
                await database.init_db()
                await database.add_or_update_user(42, "СТс-61")
                await database.update_setting(42, "notify_evening", 0)
                await database.update_setting(42, "language", "en")

                await database.clear_user_group(42)

                user = await database.get_user(42)
                self.assertIsNotNone(user)
                self.assertIsNone(user["group_name"])
                self.assertEqual(0, user["notify_evening"])
                self.assertEqual("en", user["language"])
            finally:
                database.DB_PATH = original_path
