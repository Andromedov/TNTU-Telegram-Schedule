import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from schedule_sharing import (  # noqa: E402
    COPY_TEXT_LIMIT,
    build_day_share,
    build_week_share,
    get_share_message_keyboard,
)
from handlers import ScheduleBotHandlers  # noqa: E402
import handlers as handlers_module  # noqa: E402


class DaySharingTests(unittest.TestCase):
    def test_builds_localized_share_text_and_ignores_pdf(self):
        result = build_day_share(
            "СТс-21",
            datetime(2026, 8, 17),
            [
                {"time": "08:00-09:20", "name": "Algorithms", "is_pdf": False},
                {"time": "PDF", "name": "Official.pdf", "is_pdf": True},
            ],
            "en",
        )

        self.assertIsNotNone(result)
        html_text, plain_text = result
        self.assertIn("Schedule: Monday, 17.08.2026", plain_text)
        self.assertIn("Group: СТс-21", plain_text)
        self.assertIn("Algorithms", plain_text)
        self.assertNotIn("Official.pdf", plain_text)
        self.assertIn("<b>08:00-09:20</b>", html_text)

    def test_escapes_schedule_data_for_html(self):
        html_text, plain_text = build_day_share(
            "A&B",
            datetime(2026, 8, 17),
            [{"time": "08:00", "name": "A < B & C", "is_pdf": False}],
            "uk",
        )

        self.assertIn("A &lt; B &amp; C", html_text)
        self.assertIn("A < B & C", plain_text)
        self.assertNotIn("A < B", html_text)

    def test_returns_none_without_actual_classes(self):
        self.assertIsNone(build_day_share(
            "СТс-21",
            datetime(2026, 8, 17),
            [{"time": "PDF", "name": "Schedule.pdf", "is_pdf": True}],
        ))


class WeekSharingTests(unittest.TestCase):
    def test_skips_empty_days_and_preserves_week_dates(self):
        schedules = [[] for _ in range(7)]
        schedules[0] = [{"time": "08:00", "name": "Math", "is_pdf": False}]
        schedules[2] = [{"time": "10:00", "name": "Physics", "is_pdf": False}]

        html_text, plain_text = build_week_share(
            "СТс-21", datetime(2026, 8, 17), schedules, "en"
        )

        self.assertIn("17.08–23.08.2026", plain_text)
        self.assertIn("Monday, 17.08", plain_text)
        self.assertIn("Wednesday, 19.08", plain_text)
        self.assertNotIn("Tuesday", plain_text)
        self.assertLessEqual(len(html_text), 4096)

    def test_truncates_at_complete_lines(self):
        schedules = [[
            {"time": f"{index:02d}:00", "name": "Дуже довга назва " * 30, "is_pdf": False}
            for index in range(20)
        ]] + [[] for _ in range(6)]

        html_text, plain_text = build_week_share(
            "СТс-21", datetime(2026, 8, 17), schedules, "uk"
        )

        self.assertLessEqual(len(html_text), 4096)
        self.assertIn("Частину розкладу приховано", plain_text)


class ShareKeyboardTests(unittest.TestCase):
    def test_adds_copy_button_only_when_full_text_fits(self):
        short_keyboard = get_share_message_keyboard("short", "en")
        long_keyboard = get_share_message_keyboard("x" * (COPY_TEXT_LIMIT + 1), "en")

        self.assertIsNotNone(short_keyboard.inline_keyboard[0][0].copy_text)
        self.assertEqual("short", short_keyboard.inline_keyboard[0][0].copy_text.text)
        self.assertEqual("delete_msg", long_keyboard.inline_keyboard[0][0].callback_data)


class ShareHandlerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _callback(data: str):
        return SimpleNamespace(
            data=data,
            from_user=SimpleNamespace(id=42, language_code="en"),
            answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()),
        )

    async def test_day_callback_sends_standalone_forwardable_message(self):
        callback = self._callback("share_day:0")
        handler = object.__new__(ScheduleBotHandlers)
        user = {"user_id": 42, "group_name": "СТс-21", "language": "en"}
        schedule = [{"time": "08:00", "name": "Math", "is_pdf": False}]

        with (
            patch.object(handlers_module.db, "get_user", new=AsyncMock(return_value=user)),
            patch.object(handlers_module.scraper, "_get_schedule_for_date", new=AsyncMock(return_value=schedule)),
        ):
            await handler.process_share_day(callback)

        callback.answer.assert_awaited_once_with("⏳ Preparing message...")
        callback.message.answer.assert_awaited_once()
        sent_text = callback.message.answer.await_args.args[0]
        self.assertIn("Math", sent_text)
        self.assertEqual("HTML", callback.message.answer.await_args.kwargs["parse_mode"])

    async def test_invalid_week_callback_does_not_fetch_schedule(self):
        callback = self._callback("share_week:not-a-number")
        handler = object.__new__(ScheduleBotHandlers)
        user = {"user_id": 42, "group_name": "СТс-21", "language": "en"}

        with (
            patch.object(handlers_module.db, "get_user", new=AsyncMock(return_value=user)),
            patch.object(handlers_module.scraper, "_get_schedule_for_date", new=AsyncMock()) as get_schedule,
        ):
            await handler.process_share_week(callback)

        callback.answer.assert_awaited_once_with(
            "❌ Could not determine the requested period.", show_alert=True
        )
        get_schedule.assert_not_awaited()
