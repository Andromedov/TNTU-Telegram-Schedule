import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from calendar_ui import get_calendar_keyboard  # noqa: E402
from handlers import ScheduleBotHandlers  # noqa: E402
from messages import get_msg, normalize_language  # noqa: E402


class LocalizationTests(unittest.TestCase):
    def test_normalizes_telegram_locale_and_falls_back_to_ukrainian(self):
        self.assertEqual("en", normalize_language("en-US"))
        self.assertEqual("uk", normalize_language("de-DE"))
        self.assertIn("Notification settings", get_msg("settings.title", language="en-US"))
        self.assertIn("Налаштування", get_msg("settings.title", language="de-DE"))

    def test_settings_keyboard_uses_saved_language(self):
        keyboard = ScheduleBotHandlers.get_settings_keyboard({
            "language": "en",
            "notify_10_min": 1,
            "reminder_offset": 15,
            "notify_evening": 1,
            "is_paused": 0,
            "notify_schedule_update": 1,
        })
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("✅ Reminder (15 min)", labels)
        self.assertIn("🌐 Language: English", labels)

    def test_calendar_uses_english_months_and_weekdays(self):
        keyboard = get_calendar_keyboard(2026, 8, "en")
        self.assertEqual("August 2026", keyboard.inline_keyboard[0][1].text)
        self.assertEqual("Mon", keyboard.inline_keyboard[1][0].text)
        self.assertEqual("🎯 Today", keyboard.inline_keyboard[-2][0].text)
