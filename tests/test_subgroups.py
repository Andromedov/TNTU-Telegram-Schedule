import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import schedule as handlers, settings  # noqa: E402
from bot.router import ScheduleBotHandlers  # noqa: E402
from infrastructure import clock, database  # noqa: E402
from jobs import notifications  # noqa: E402
from schedule import service  # noqa: E402
from schedule.formatting import lesson_html  # noqa: E402
from schedule.subgroups import filter_schedule, user_subgroup  # noqa: E402

FIXTURE = (ROOT / "tests" / "fixtures" / "schedule_sn21.html").read_text(encoding="utf-8")
PROBABILITY = "Теорія імовірностей, імовірнісні процеси і математична статистика"
TECHNICAL = "Основи технічної творчості та наукових досліджень"


def callback(data):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=42, language_code="uk"),
        message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock(), answer_document=AsyncMock()),
        answer=AsyncMock(),
        bot=AsyncMock(),
    )


class SubgroupParsingTests(unittest.IsolatedAsyncioTestCase):
    async def day(self, date, subgroup):
        with patch.object(service, "fetch_schedule_html", new=AsyncMock(return_value=FIXTURE)):
            return filter_schedule(await service._get_schedule_for_date("СН-21", date), subgroup)

    async def test_monday_rotates_subgroups_and_retains_common_lecture(self):
        for day, expected in ((datetime(2026, 9, 7), PROBABILITY), (datetime(2026, 10, 5), PROBABILITY)):
            first = await self.day(day, 1)
            second = await self.day(day, 2)
            self.assertEqual([(TECHNICAL, None), (expected, 1)], [(x["subject"], x["subgroup"]) for x in first])
            self.assertEqual([(TECHNICAL, None), (TECHNICAL, 2)], [(x["subject"], x["subgroup"]) for x in second])
        first_week = await self.day(datetime(2026, 9, 14), 1)
        self.assertEqual(TECHNICAL, first_week[-1]["subject"])
        self.assertEqual("лабораторна", first_week[-1]["lesson_type"])

    async def test_mixed_day_widths_do_not_shift_tuesday_into_wednesday(self):
        tuesday = await self.day(datetime(2026, 10, 6), 1)
        wednesday = await self.day(datetime(2026, 10, 7), 1)
        thursday = await self.day(datetime(2026, 10, 8), 1)
        self.assertEqual(["Чисельні методи", "Об'єктно-орієнтоване програмування"], [x["subject"] for x in tuesday])
        self.assertEqual("практична", tuesday[0]["lesson_type"])
        self.assertEqual(["Основи національного спротиву"], [x["subject"] for x in wednesday])
        self.assertEqual(["Філософія"], [x["subject"] for x in thursday])

    async def test_friday_empty_subgroup_does_not_receive_other_subgroups_class(self):
        first = await self.day(datetime(2026, 10, 9), 1)
        second = await self.day(datetime(2026, 10, 9), 2)
        self.assertEqual([("8:00-9:20", 1)], [(x["time"], x["subgroup"]) for x in first])
        self.assertEqual([("9:30-10:50", 2)], [(x["time"], x["subgroup"]) for x in second])

    def test_whole_group_view_preserves_both_subgroups_and_deduplicates_common_classes(self):
        lessons = service._table_snapshot(BeautifulSoup(FIXTURE, "html.parser").table)
        monday = [x for x in lessons if x["weekday"] == 0 and x["week"] == 2]
        self.assertEqual([None, 1, 2], [x["subgroup"] for x in monday])
        self.assertIn("subgroup 1", lesson_html(monday[1], "en"))
        pdf = {"is_pdf": True, "name": "Document"}
        self.assertIn(pdf, filter_schedule([*monday, pdf], 1))

    def test_same_subject_in_two_subgroups_is_not_matched_as_a_moved_class(self):
        old = [{"week": 1, "weekday": 0, "time": "08:00", "subject": "Math", "subgroup": 1}]
        new = [{**old[0], "subgroup": 2}]
        self.assertEqual(["removed", "added"], [x["kind"] for x in service._compare_schedule_snapshots(old, new)])


class SubgroupIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migration_persists_subgroup_and_resets_it_when_group_changes(self):
        with (
            TemporaryDirectory() as directory,
            patch.object(database, "DB_PATH", str(Path(directory) / "users.sqlite3")),
        ):
            await database.init_db()
            await database.add_or_update_user(42, "СН-21")
            self.assertIsNone((await database.get_user(42))["subgroup"])
            await database.update_setting(42, "subgroup", 1)
            await database.add_or_update_user(42, "СН-21")
            self.assertEqual(1, (await database.get_user(42))["subgroup"])
            await database.add_or_update_user(42, "СН-22")
            self.assertIsNone((await database.get_user(42))["subgroup"])

    async def test_day_ui_and_calendar_export_filter_own_group(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler._get_user_language = AsyncMock(return_value="uk")
        user = {"user_id": 42, "group_name": "СН-21", "subgroup": 1, "language": "uk"}
        now = datetime(2026, 10, 9, 8, tzinfo=clock.KYIV_TZ)
        with (
            patch.object(handlers.db, "get_user", new=AsyncMock(return_value=user)),
            patch.object(service, "fetch_schedule_html", new=AsyncMock(return_value=FIXTURE)),
            patch.object(handlers, "kyiv_now", return_value=now),
        ):
            text, _ = await handler._generate_schedule_ui(42, 0)
            self.assertIn("09.10.2026", text)
            self.assertIn("Підгрупа 1", text)
            self.assertIn("course=4773", text)
            self.assertNotIn("Архітектура", text)
            request = callback("export_ics:0")
            with (
                patch.dict(handlers.ics_cooldown, {}, clear=True),
                patch.object(handlers, "generate_week_ics", return_value="BEGIN:VEVENT") as export,
            ):
                await handler.process_export_ics(request)
            self.assertTrue(
                all(item.get("subgroup") in (None, 1) for day in export.call_args.args[1].values() for item in day)
            )

    async def test_settings_save_subgroup_and_rebuild_pending_reminders(self):
        handler = object.__new__(ScheduleBotHandlers)
        handler.scheduler = Mock()
        handler._show_updated_settings = AsyncMock()
        request = callback("set_subgroup:1")
        with (
            patch.object(settings.db, "update_setting", new=AsyncMock()) as save,
            patch.object(settings, "remove_user_jobs") as remove,
            patch.object(settings, "schedule_daily_reminders", new=AsyncMock()) as rebuild,
        ):
            await handler.process_set_subgroup(request)
        save.assert_awaited_once_with(42, "subgroup", 1)
        remove.assert_called_once_with(handler.scheduler, 42)
        rebuild.assert_awaited_once_with(request.bot, handler.scheduler)

    async def test_first_class_reminder_uses_first_class_of_selected_subgroup(self):
        user = {
            "user_id": 42,
            "group_name": "СН-21",
            "subgroup": 1,
            "notify_10_min": 1,
            "reminder_offset": 10,
            "first_class_reminder_offset": 60,
        }
        classes = [
            {"time": "08:00-09:20", "name": "Other", "subgroup": 2},
            {"time": "09:30-10:50", "name": "Own", "subgroup": 1},
            {"time": "11:10-12:30", "name": "Common", "subgroup": None},
        ]
        scheduler = Mock()
        now = datetime(2026, 10, 7, 6, tzinfo=clock.KYIV_TZ)
        with (
            patch.object(notifications, "is_automatic_study_day", new=AsyncMock(return_value=True)),
            patch.object(notifications.db, "get_active_users", new=AsyncMock(return_value=[user])),
            patch.object(notifications.scraper, "parse_schedule_for_today", new=AsyncMock(return_value=classes)),
        ):
            await notifications.schedule_daily_reminders(AsyncMock(), scheduler, now_provider=lambda: now)
        self.assertEqual(
            ["Own", "Common"], [call.kwargs["args"][2]["name"] for call in scheduler.add_job.call_args_list]
        )
        self.assertEqual([60, 10], [call.kwargs["args"][-1] for call in scheduler.add_job.call_args_list])

    async def test_pending_reminder_rechecks_subgroup_before_delivery(self):
        user = {"user_id": 42, "group_name": "СН-21", "subgroup": 1, "notify_10_min": 1}
        bot = AsyncMock()
        with patch.object(notifications.db, "get_user", new=AsyncMock(return_value=user)):
            await notifications.send_class_reminder(bot, 42, {"name": "Other", "subgroup": 2}, "СН-21", 10)
        bot.send_message.assert_not_awaited()

    async def test_whole_group_reminders_do_not_overwrite_identical_subgroup_subjects(self):
        user = {"user_id": 42, "group_name": "СН-21", "notify_10_min": 1}
        classes = [{"time": "08:00-09:20", "name": "Math", "subgroup": number} for number in (1, 2)]
        scheduler = Mock()
        now = datetime(2026, 10, 7, 6, tzinfo=clock.KYIV_TZ)
        with (
            patch.object(notifications, "is_automatic_study_day", new=AsyncMock(return_value=True)),
            patch.object(notifications.db, "get_active_users", new=AsyncMock(return_value=[user])),
            patch.object(service, "parse_schedule_for_today", new=AsyncMock(return_value=classes)),
        ):
            await notifications.schedule_daily_reminders(AsyncMock(), scheduler, now_provider=lambda: now)
        self.assertEqual(2, len({call.kwargs["id"] for call in scheduler.add_job.call_args_list}))

    async def test_digest_and_evening_schedule_filter_each_users_subgroup(self):
        users = [
            {
                "user_id": number,
                "group_name": "СН-21",
                "subgroup": number,
                "language": "en",
                "notify_evening": 1,
                "morning_digest": 1,
                "morning_digest_hour": 7,
            }
            for number in (1, 2)
        ]
        classes = [
            {"time": "08:00", "subject": "First", "name": "First", "subgroup": 1},
            {"time": "08:00", "subject": "Second", "name": "Second", "subgroup": 2},
            {"time": "09:30", "subject": "Common", "name": "Common"},
        ]
        bot = AsyncMock()
        now = datetime(2026, 10, 7, 7, tzinfo=clock.KYIV_TZ)
        with (
            patch.object(notifications.db, "get_active_users", new=AsyncMock(return_value=users)),
            patch.object(notifications, "is_automatic_study_day", new=AsyncMock(return_value=True)),
            patch.object(service, "parse_schedule_for_today", new=AsyncMock(return_value=classes)),
            patch.object(service, "parse_schedule_for_tomorrow", new=AsyncMock(return_value=classes)),
        ):
            await notifications.send_morning_digest(bot, 7, now_provider=lambda: now)
            await notifications.send_evening_schedule(bot, now_provider=lambda: now)
        self.assertEqual(4, bot.send_message.await_count)
        for call in bot.send_message.await_args_list:
            user_id, text = call.args
            self.assertIn("Common", text)
            self.assertIn("First" if user_id == 1 else "Second", text)
            self.assertNotIn("Second" if user_id == 1 else "First", text)

    async def test_share_filters_other_subgroup_and_preserves_common_classes(self):
        handler = object.__new__(ScheduleBotHandlers)
        user = {"user_id": 42, "group_name": "СН-21", "subgroup": 1, "language": "uk"}
        request = callback("share_day:0")
        classes = [
            {"name": "Own", "subject": "Own", "subgroup": 1},
            {"name": "Other", "subject": "Other", "subgroup": 2},
            {"name": "Common", "subject": "Common"},
        ]
        with (
            patch.object(handlers.db, "get_user", new=AsyncMock(return_value=user)),
            patch.object(service, "_get_schedule_for_date", new=AsyncMock(return_value=classes)),
        ):
            await handler.process_share_day(request)
        text = request.message.answer.await_args.args[0]
        self.assertIn("Own", text)
        self.assertIn("Common", text)
        self.assertNotIn("Other", text)

    def test_own_subgroup_is_not_applied_to_another_group(self):
        user = {"group_name": "СН-21", "subgroup": 1}
        self.assertEqual(1, user_subgroup(user, "СН-21"))
        self.assertIsNone(user_subgroup(user, "СТ-11"))


class KyivDateTests(unittest.IsolatedAsyncioTestCase):
    async def test_today_and_tomorrow_use_kyiv_date_after_utc_midnight_boundary(self):
        utc = datetime(2026, 10, 6, 21, 30, tzinfo=timezone.utc)
        with patch.object(clock, "datetime") as fake_datetime:
            fake_datetime.now.return_value = utc.astimezone(clock.KYIV_TZ)
            now = clock.kyiv_now()
            fake_datetime.now.assert_called_once_with(clock.KYIV_TZ)
        with (
            patch.object(service, "kyiv_now", return_value=now),
            patch.object(service, "_get_schedule_for_date", new=AsyncMock(return_value=[])) as get,
        ):
            await service.parse_schedule_for_today("СН-21")
            await service.parse_schedule_for_tomorrow("СН-21")
        self.assertEqual([7, 8], [call.args[1].day for call in get.await_args_list])

    async def test_aware_utc_target_is_converted_to_kyiv_before_weekday_selection(self):
        with patch.object(service, "fetch_schedule_html", new=AsyncMock(return_value=FIXTURE)):
            classes = await service._get_schedule_for_date("СН-21", datetime(2026, 10, 6, 21, 30, tzinfo=timezone.utc))
        self.assertEqual(["Основи національного спротиву"], [x["subject"] for x in classes])
