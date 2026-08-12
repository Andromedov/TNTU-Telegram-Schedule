import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

import database  # noqa: E402


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_user_group_preserves_user_and_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = database.DB_PATH
            database.DB_PATH = str(Path(temp_dir) / "users.sqlite3")
            try:
                await database.init_db()
                await database.add_or_update_user(42, "СТс-61")
                await database.update_setting(42, "notify_evening", 0)

                await database.clear_user_group(42)

                user = await database.get_user(42)
                self.assertIsNotNone(user)
                self.assertIsNone(user["group_name"])
                self.assertEqual(0, user["notify_evening"])
            finally:
                database.DB_PATH = original_path
