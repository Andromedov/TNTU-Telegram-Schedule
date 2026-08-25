import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

import handlers  # noqa: E402
import scheduler  # noqa: E402
from handlers import ScheduleBotHandlers  # noqa: E402
from reminder_utils import (  # noqa: E402
    lesson_type_category,
    muted_until_tomorrow,
    notifications_are_muted,
    quiet_hours_are_active,
    reminder_enabled_for_lesson,
    temporary_notifications_are_muted,
)


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, function, trigger, **kwargs):
        self.jobs.append((function, trigger, kwargs))


class ReminderTimeTests(unittest.TestCase):
    def test_lesson_types_are_normalized_and_unknown_types_remain_enabled(self):
        self.assertEqual("lecture", lesson_type_category("Лекція"))
        self.assertEqual("laboratory", lesson_type_category("лабораторна"))
        self.assertEqual("practical", lesson_type_category("Practical"))
        self.assertIsNone(lesson_type_category("консультація"))

        user = {"notify_lectures": 0, "notify_laboratories": 1, "notify_practicals": 0}
        self.assertFalse(reminder_enabled_for_lesson(user, {"lesson_type": "лекція"}))
        self.assertTrue(reminder_enabled_for_lesson(user, {"lesson_type": "лабораторна"}))
        self.assertTrue(reminder_enabled_for_lesson(user, {"lesson_type": "консультація"}))

    def test_mute_ends_at_next_kyiv_midnight_in_utc(self):
        kyiv = ZoneInfo("Europe/Kyiv")
        now = datetime(2026, 3, 28, 23, 30, tzinfo=kyiv)

        result = datetime.fromisoformat(muted_until_tomorrow(now))

        # Перехід на літній час відбудеться пізніше цієї ночі, після опівночі.
        self.assertEqual(datetime(2026, 3, 28, 22, 0, tzinfo=timezone.utc), result)

    def test_temporary_and_global_mutes_are_distinguished(self):
        now = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
        temporary = {"is_paused": 0, "notifications_muted_until": "2026-08-12T11:00:00+00:00"}
        global_only = {"is_paused": 1, "notifications_muted_until": None}

        self.assertTrue(temporary_notifications_are_muted(temporary, now))
        self.assertTrue(notifications_are_muted(temporary, now))
        self.assertFalse(temporary_notifications_are_muted(global_only, now))
        self.assertTrue(notifications_are_muted(global_only, now))

    def test_quiet_hours_support_overnight_and_daytime_ranges(self):
        kyiv = ZoneInfo("Europe/Kyiv")
        overnight = {"quiet_hours_start": 22, "quiet_hours_end": 7}
        daytime = {"quiet_hours_start": 9, "quiet_hours_end": 12}

        self.assertTrue(quiet_hours_are_active(overnight, datetime(2026, 8, 12, 23, 0, tzinfo=kyiv)))
        self.assertTrue(quiet_hours_are_active(overnight, datetime(2026, 8, 12, 6, 59, tzinfo=kyiv)))
        self.assertFalse(quiet_hours_are_active(overnight, datetime(2026, 8, 12, 7, 0, tzinfo=kyiv)))
        self.assertTrue(quiet_hours_are_active(daytime, datetime(2026, 8, 12, 10, 0, tzinfo=kyiv)))
        self.assertFalse(quiet_hours_are_active(daytime, datetime(2026, 8, 12, 13, 0, tzinfo=kyiv)))
        self.assertFalse(quiet_hours_are_active({"quiet_hours_start": None, "quiet_hours_end": None}))


class ReminderSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_lesson_types_are_scheduled(self):
        users = [{
            "user_id": 5, "group_name": "СТс-21", "notify_10_min": 1,
            "reminder_offset": 10, "first_class_reminder_offset": 60,
            "notify_lectures": 0, "notify_laboratories": 1, "notify_practicals": 0,
        }]
        schedule = [
            {"time": "08:00-09:20", "name": "Math", "lesson_type": "лекція", "is_pdf": False},
            {"time": "10:00-11:20", "name": "Physics", "lesson_type": "лабораторна", "is_pdf": False},
            {"time": "12:00-13:20", "name": "English", "lesson_type": "практична", "is_pdf": False},
        ]
        fake_scheduler = FakeScheduler()
        fixed_now = datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Europe/Kyiv"))

        with (
            patch.object(scheduler.db, "get_active_users", new=AsyncMock(return_value=users)),
            patch.object(scheduler.scraper, "parse_schedule_for_today", new=AsyncMock(return_value=schedule)),
            patch.object(scheduler, "kyiv_now", return_value=fixed_now),
        ):
            await scheduler.schedule_daily_reminders(AsyncMock(), fake_scheduler)

        self.assertEqual(1, len(fake_scheduler.jobs))
        self.assertEqual("Physics", fake_scheduler.jobs[0][2]["args"][2]["name"])
        self.assertEqual(10, fake_scheduler.jobs[0][2]["args"][-1])

    async def test_first_class_uses_own_offset_and_jobs_have_stable_ids(self):
        users = [{
            "user_id": 5,
            "group_name": "СТс-21",
            "notify_10_min": 1,
            "reminder_offset": 10,
            "first_class_reminder_offset": 60,
            "is_paused": 0,
            "notifications_muted_until": None,
        }]
        schedule = [
            {"time": "08:00-09:20", "name": "Math", "is_pdf": False},
            {"time": "10:00-11:20", "name": "Physics", "is_pdf": False},
        ]
        fake_scheduler = FakeScheduler()
        fixed_now = datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Europe/Kyiv"))

        with (
            patch.object(scheduler.db, "get_active_users", new=AsyncMock(return_value=users)),
            patch.object(scheduler.scraper, "parse_schedule_for_today", new=AsyncMock(return_value=schedule)),
            patch.object(scheduler, "kyiv_now", return_value=fixed_now),
        ):
            await scheduler.schedule_daily_reminders(AsyncMock(), fake_scheduler)

        self.assertEqual(2, len(fake_scheduler.jobs))
        first_job = fake_scheduler.jobs[0][2]
        second_job = fake_scheduler.jobs[1][2]
        self.assertEqual(60, first_job["args"][-1])
        self.assertEqual(10, second_job["args"][-1])
        self.assertTrue(first_job["replace_existing"])
        self.assertTrue(first_job["id"].startswith("class-reminder:5:СТс-21:"))

    async def test_temporarily_muted_user_is_still_scheduled_for_later_delivery_check(self):
        users = [{
            "user_id": 5,
            "group_name": "СТс-21",
            "notify_10_min": 1,
            "reminder_offset": 10,
            "first_class_reminder_offset": None,
            "is_paused": 0,
            "notifications_muted_until": "2099-01-01T00:00:00+00:00",
        }]
        schedule = [{"time": "08:00-09:20", "name": "Math", "is_pdf": False}]
        fake_scheduler = FakeScheduler()
        fixed_now = datetime(2026, 8, 12, 6, 0, tzinfo=ZoneInfo("Europe/Kyiv"))

        with (
            patch.object(scheduler.db, "get_active_users", new=AsyncMock(return_value=users)),
            patch.object(scheduler.scraper, "parse_schedule_for_today", new=AsyncMock(return_value=schedule)),
            patch.object(scheduler, "kyiv_now", return_value=fixed_now),
        ):
            await scheduler.schedule_daily_reminders(AsyncMock(), fake_scheduler)

        self.assertEqual(1, len(fake_scheduler.jobs))

    async def test_muted_user_does_not_receive_scheduled_or_snoozed_reminder(self):
        user = {
            "user_id": 5, "group_name": "СТс-21", "notify_10_min": 1, "is_paused": 0,
            "notifications_muted_until": "2099-01-01T00:00:00+00:00", "language": "uk",
        }
        bot = AsyncMock()

        with patch.object(scheduler.db, "get_user", new=AsyncMock(return_value=user)):
            await scheduler.send_class_reminder(bot, 5, "Math", "СТс-21", 10)
            await scheduler.send_snoozed_reminder(bot, 5, "Reminder")

        bot.send_message.assert_not_awaited()

    async def test_disabled_lesson_type_is_checked_again_before_delivery(self):
        user = {
            "user_id": 5, "group_name": "СТс-21", "notify_10_min": 1, "is_paused": 0,
            "notifications_muted_until": None, "language": "uk", "notify_lectures": 0,
        }
        bot = AsyncMock()

        with patch.object(scheduler.db, "get_user", new=AsyncMock(return_value=user)):
            await scheduler.send_class_reminder(
                bot, 5, {"name": "Math", "lesson_type": "лекція"}, "СТс-21", 10
            )

        bot.send_message.assert_not_awaited()


class MorningDigestTests(unittest.IsolatedAsyncioTestCase):
    async def test_digest_filters_hour_and_localizes_each_user(self):
        users = [
            {"user_id": 1, "group_name": "СТс-21", "morning_digest": 1,
             "morning_digest_hour": 7, "is_paused": 0, "notifications_muted_until": None, "language": "uk"},
            {"user_id": 2, "group_name": "СТс-21", "morning_digest": 1,
             "morning_digest_hour": 7, "is_paused": 0, "notifications_muted_until": None, "language": "en"},
            {"user_id": 3, "group_name": "СТс-21", "morning_digest": 1,
             "morning_digest_hour": 8, "is_paused": 0, "notifications_muted_until": None, "language": "en"},
        ]
        schedule = [{"time": "08:00", "name": "A < B", "is_pdf": False}]
        bot = AsyncMock()

        with (
            patch.object(scheduler.db, "get_active_users", new=AsyncMock(return_value=users)),
            patch.object(scheduler.scraper, "parse_schedule_for_today", new=AsyncMock(return_value=schedule)),
        ):
            await scheduler.send_morning_digest(bot, 7)

        self.assertEqual(2, bot.send_message.await_count)
        self.assertIn("Розклад на сьогодні", bot.send_message.await_args_list[0].args[1])
        self.assertIn("Today's schedule", bot.send_message.await_args_list[1].args[1])
        self.assertIn("A &lt; B", bot.send_message.await_args_list[1].args[1])


class ReminderHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_snooze_creates_replaceable_job_and_removes_buttons(self):
        fake_scheduler = FakeScheduler()
        handler = object.__new__(ScheduleBotHandlers)
        handler.scheduler = fake_scheduler
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=42, language_code="en"),
            message=SimpleNamespace(
                html_text="<b>Math</b>", text="Math", message_id=99,
                edit_reply_markup=AsyncMock(),
            ),
            bot=AsyncMock(),
            answer=AsyncMock(),
        )
        user = {"user_id": 42, "language": "en"}

        with patch.object(handlers.db, "get_user", new=AsyncMock(return_value=user)):
            await handler.process_snooze_reminder(callback)

        self.assertEqual(1, len(fake_scheduler.jobs))
        job = fake_scheduler.jobs[0][2]
        self.assertEqual("snooze:42:99", job["id"])
        self.assertTrue(job["replace_existing"])
        callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)
        callback.answer.assert_awaited_once_with("I will remind you in 5 minutes!")

    def test_settings_keyboard_exposes_all_smart_reminder_controls(self):
        keyboard = ScheduleBotHandlers.get_settings_keyboard({
            "language": "en", "notify_10_min": 1, "reminder_offset": 10,
            "first_class_reminder_offset": 60, "morning_digest": 1, "morning_digest_hour": 8,
            "notifications_muted_until": None, "notify_evening": 1, "is_paused": 0,
            "notify_schedule_update": 1, "notify_lectures": 1,
            "notify_laboratories": 0, "notify_practicals": 1,
            "quiet_hours_start": 22, "quiet_hours_end": 7,
        })
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("🌅 First class: 60 min early", labels)
        self.assertIn("☀️ Morning digest: 8:00", labels)
        self.assertIn("🔕 Mute notifications for today", labels)
        self.assertIn("📚 Class types: 2/3", labels)
        self.assertIn("🌙 Quiet hours: 22:00–07:00", labels)

    def test_lesson_type_keyboard_exposes_each_toggle(self):
        keyboard = ScheduleBotHandlers.get_lesson_type_settings_keyboard({
            "notify_lectures": 1, "notify_laboratories": 0, "notify_practicals": 1,
        }, "en")
        labels = [button.text for row in keyboard.inline_keyboard for button in row]

        self.assertIn("✅ Lectures", labels)
        self.assertIn("❌ Laboratories", labels)
        self.assertIn("✅ Practicals", labels)

    def test_quiet_hour_keyboard_offers_all_hours(self):
        keyboard = ScheduleBotHandlers.get_quiet_hour_keyboard("start", "en")
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        hour_buttons = [button for button in buttons if button.callback_data.startswith("set_quiet_hour:")]

        self.assertEqual(24, len(hour_buttons))
        self.assertEqual("set_quiet_hour:start:0", hour_buttons[0].callback_data)
        self.assertEqual("set_quiet_hour:start:23", hour_buttons[-1].callback_data)
