import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import router as handlers  # noqa: E402
from bot.router import ScheduleBotHandlers  # noqa: E402
from i18n.messages import get_html_msg, trusted_html  # noqa: E402


class HtmlMessageTests(unittest.TestCase):
    def test_dynamic_values_are_escaped_by_default(self):
        text = get_html_msg(
            "start.greeting_existing",
            language="en",
            name='<b>A & B</b>',
            group='X<1>',
            next_class="none & later",
        )

        self.assertIn("&lt;b&gt;A &amp; B&lt;/b&gt;", text)
        self.assertIn("X&lt;1&gt;", text)
        self.assertIn("none &amp; later", text)

    def test_only_explicitly_trusted_markup_is_preserved(self):
        text = get_html_msg(
            "start.next_class",
            language="en",
            time="10:00",
            subject=trusted_html("<a href=\"https://example.com\">A &amp; B</a>"),
        )

        self.assertIn('<a href="https://example.com">A &amp; B</a>', text)
        self.assertNotIn("&lt;a", text)


class ScheduleHtmlTests(unittest.IsolatedAsyncioTestCase):
    async def test_day_schedule_escapes_group_pdf_name_and_time(self):
        handler = object.__new__(ScheduleBotHandlers)
        user = {"user_id": 1, "group_name": "A<1> & B", "language": "en"}
        schedule = [
            {
                "is_pdf": True,
                "name": "PDF <draft> & notes",
                "url": "https://example.com/a.pdf",
                "viewer_url": "https://example.com/view",
            },
            {
                "is_pdf": False,
                "time": "10:00 < 11:00",
                "subject": "A < B",
                "lesson_type": "lecture",
                "location": "K1 & K2",
            },
        ]

        with (
            patch.object(handlers.db, "get_user", new=AsyncMock(return_value=user)),
            patch.object(handlers.scraper, "_get_schedule_for_date", new=AsyncMock(return_value=schedule)),
        ):
            text, keyboard = await handler._generate_schedule_ui(1, 0)

        self.assertIn("A&lt;1&gt; &amp; B", text)
        self.assertIn("PDF &lt;draft&gt; &amp; notes", text)
        self.assertIn("10:00 &lt; 11:00", text)
        self.assertIn("A &lt; B (lecture, K1 &amp; K2)", text)
        self.assertNotIn("A<1>", text)
        self.assertIn('<a href="https://example.com/a.pdf">PDF &lt;draft&gt; &amp; notes</a>', text)
        self.assertNotIn("https://example.com/view", text)
        self.assertFalse(
            any(
                button.callback_data and button.callback_data.startswith("send_pdf:")
                for row in keyboard.inline_keyboard
                for button in row
            )
        )

    async def test_day_and_week_open_original_schedule_for_viewed_group_with_or_without_pdf(self):
        handler = object.__new__(ScheduleBotHandlers)
        user = {"user_id": 1, "group_name": "СН-21", "language": "uk"}
        pdf = {
            "is_pdf": True,
            "name": "Saturday schedule",
            "url": "https://example.com/saturday.pdf",
            "viewer_url": "https://example.com/view",
        }
        for generate in (handler._generate_schedule_ui, handler._generate_week_schedule_ui):
            for schedule in ([], [pdf]):
                with (
                    self.subTest(view=generate.__name__, pdf=bool(schedule)),
                    patch.object(handlers.db, "get_user", new=AsyncMock(return_value=user)),
                    patch.object(handlers.scraper, "_get_schedule_for_date", new=AsyncMock(return_value=schedule)),
                ):
                    text, keyboard = await generate(1, 0, "СТс-21")
                    urls = [button.url for row in keyboard.inline_keyboard for button in row if button.url]
                    self.assertEqual(["https://tntu.edu.ua/?p=uk/schedule&s=-sts21"], urls)
                    self.assertFalse(
                        any(
                            button.callback_data and button.callback_data.startswith("send_pdf:")
                            for row in keyboard.inline_keyboard
                            for button in row
                        )
                    )
                    if schedule:
                        self.assertEqual(1, text.count('<a href="https://example.com/saturday.pdf">'))
