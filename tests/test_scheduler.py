import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from jobs import scheduler  # noqa: E402


class GroupCandidateTests(unittest.TestCase):
    def test_increments_course_digit_and_keeps_subgroup(self):
        self.assertEqual("СТс-31", scheduler._next_group_candidate("СТс-21"))
        self.assertEqual("ЕМ-41", scheduler._next_group_candidate("ЕМ-31"))
        self.assertEqual("КН-6", scheduler._next_group_candidate("КН-5"))

    def test_marks_last_course_as_graduated(self):
        self.assertEqual("GRADUATED", scheduler._next_group_candidate("СТс-61"))


class ScheduleChangeFormattingTests(unittest.TestCase):
    def test_formats_localized_change_and_escapes_external_values(self):
        changes = [
            {
                "kind": "changed",
                "lesson": {
                    "week": 2,
                    "weekday": 1,
                    "time": "9:30-10:50",
                    "subject": "A < B",
                    "atutor_url": "http://dl.tntu.edu.ua/bounce.php?course=101",
                },
                "fields": {"room": {"old": "63", "new": "64 & 65"}},
            }
        ]

        text = scheduler._format_schedule_changes(changes, "en")

        self.assertIn("Schedule updated", text)
        self.assertIn("Tuesday, week 2", text)
        self.assertIn(
            '<a href="https://dl.tntu.edu.ua/bounce.php?course=101">A &lt; B</a>',
            text,
        )
        self.assertIn("Room: 63 → 64 &amp; 65", text)


class SchedulerConfigurationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recurring_jobs_have_stable_monitoring_ids(self):
        configured_scheduler = scheduler.setup_scheduler(AsyncMock())

        job_ids = {job.id for job in configured_scheduler.get_jobs()}

        self.assertEqual(
            {
                "evening_schedule",
                "daily_reminders",
                "morning_digest_6",
                "morning_digest_7",
                "morning_digest_8",
                "morning_digest_9",
                "schedule_updates",
                "group_promotion",
            },
            job_ids,
        )


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    async def test_lookup_failure_does_not_change_user(self):
        user = {"user_id": 1, "group_name": "СТс-21"}
        batches = AsyncMock(side_effect=[[user], [], [user], []])

        with (
            patch.object(scheduler.db, "get_users_batch", batches),
            patch.object(scheduler.db, "add_or_update_user", new=AsyncMock()) as update_group,
            patch.object(scheduler.db, "clear_user_group", new=AsyncMock()) as clear_group,
            patch.object(scheduler.scraper, "check_group_exists_status", new=AsyncMock(return_value=None)),
            patch.object(scheduler.asyncio, "sleep", new=AsyncMock()),
        ):
            await scheduler.process_promotion(AsyncMock())

        update_group.assert_not_awaited()
        clear_group.assert_not_awaited()

    async def test_graduate_is_cleared_even_when_notification_fails(self):
        user = {"user_id": 7, "group_name": "СТс-61"}
        batches = AsyncMock(side_effect=[[user], [], [user], []])
        bot = AsyncMock()
        bot.send_message.side_effect = RuntimeError("blocked")

        with (
            patch.object(scheduler.db, "get_users_batch", batches),
            patch.object(scheduler.db, "clear_user_group", new=AsyncMock()) as clear_group,
        ):
            await scheduler.process_promotion(bot)

        clear_group.assert_awaited_once_with(7)

    async def test_existing_next_group_is_applied(self):
        user = {"user_id": 9, "group_name": "СТс-21"}
        batches = AsyncMock(side_effect=[[user], [], [user], []])

        with (
            patch.object(scheduler.db, "get_users_batch", batches),
            patch.object(scheduler.db, "add_or_update_user", new=AsyncMock()) as update_group,
            patch.object(scheduler.scraper, "check_group_exists_status", new=AsyncMock(return_value=True)),
        ):
            await scheduler.process_promotion(AsyncMock())

        update_group.assert_awaited_once_with(9, "СТс-31")
