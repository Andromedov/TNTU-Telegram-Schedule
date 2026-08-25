import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
                self.assertIsNone(user["created_at"])
                self.assertIsNone(user["last_seen_at"])
                self.assertIsNone(user["username"])
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

    async def test_activity_tracking_preserves_creation_time_and_saved_language(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "users.sqlite3")
            created_at = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
            seen_at = datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)
            try:
                await database.init_db()
                await database.record_user_activity(7, "en", created_at, username="First_User")
                await database.update_setting(7, "language", "uk")
                await database.record_user_activity(7, "en", seen_at, username="Current_User")

                user = await database.get_user(7)
                self.assertEqual("uk", user["language"])
                self.assertEqual("Current_User", user["username"])
                self.assertEqual(created_at.isoformat(timespec="seconds"), user["created_at"])
                self.assertEqual(seen_at.isoformat(timespec="seconds"), user["last_seen_at"])
            finally:
                database.DB_PATH = original_path

    async def test_username_lookup_tracks_current_owner_and_deletion_removes_everything(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "users.sqlite3")
            try:
                await database.init_db()
                await database.record_user_activity(7, username="Exchange_Student")
                await database.add_or_update_user(7, "КН-21")

                found = await database.get_user_by_username("@exchange_student")
                self.assertEqual(7, found["user_id"])

                await database.record_user_activity(8, username="EXCHANGE_STUDENT")
                self.assertIsNone((await database.get_user(7))["username"])
                self.assertEqual(8, (await database.get_user_by_username("@exchange_student"))["user_id"])

                self.assertTrue(await database.delete_user_data(8))
                self.assertIsNone(await database.get_user(8))
                self.assertIsNone(await database.get_user_by_username("@exchange_student"))
                self.assertFalse(await database.delete_user_data(8))
            finally:
                database.DB_PATH = original_path

    async def test_statistics_distinguish_activity_registration_and_notification_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "users.sqlite3")
            now = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
            try:
                await database.init_db()

                await database.record_user_activity(1, "uk", now - timedelta(hours=1))
                await database.add_or_update_user(1, "КН-21", "uk")

                await database.record_user_activity(2, "en", now - timedelta(days=10))
                await database.add_or_update_user(2, "КН-21", "en")
                await database.record_user_activity(2, "en", now - timedelta(days=2))
                await database.update_setting(2, "is_paused", 1)
                await database.update_setting(2, "notify_10_min", 0)
                await database.update_setting(2, "morning_digest", 1)
                await database.update_setting(2, "quiet_hours_start", 22)
                await database.update_setting(2, "quiet_hours_end", 7)
                await database.update_setting(
                    2,
                    "notifications_muted_until",
                    (now + timedelta(hours=12)).isoformat(timespec="seconds"),
                )

                async with aiosqlite.connect(database.DB_PATH) as connection:
                    await connection.execute("INSERT INTO users (user_id, group_name) VALUES (3, 'СІ-31')")
                    await connection.commit()

                await database.record_user_activity(4, "en", now - timedelta(days=2))

                stats = await database.get_statistics(now)

                self.assertEqual(4, stats["total"])
                self.assertEqual(3, stats["notifications_enabled"])
                self.assertEqual(1, stats["notifications_paused"])
                self.assertEqual(3, stats["users_with_group"])
                self.assertEqual(2, stats["distinct_groups"])
                self.assertEqual(1, stats["active_24h"])
                self.assertEqual(3, stats["active_7d"])
                self.assertEqual(1, stats["new_24h"])
                self.assertEqual(2, stats["new_7d"])
                self.assertEqual(2, stats["language_uk"])
                self.assertEqual(2, stats["language_en"])
                self.assertEqual(1, stats["temporarily_muted"])
                self.assertEqual("КН-21", stats["top_groups"][0]["group_name"])
                self.assertEqual(2, stats["top_groups"][0]["count"])
            finally:
                database.DB_PATH = original_path
