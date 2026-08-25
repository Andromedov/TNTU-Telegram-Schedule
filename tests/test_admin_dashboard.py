import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from bot import admin  # noqa: E402
from bot.keyboards import KeyboardMixin  # noqa: E402
from bot.middleware import UserActivityMiddleware  # noqa: E402
from jobs.monitoring import JobMonitor, job_monitor  # noqa: E402


class DashboardHandlers(admin.AdminHandlerMixin, KeyboardMixin):
    def __init__(self):
        self.started_at = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
        self.scheduler = SimpleNamespace(running=True, get_jobs=lambda: [object(), object()])


def make_callback(user_id: int = 1):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(edit_text=AsyncMock()),
        answer=AsyncMock(),
    )


def statistics_fixture():
    return {
        "total": 10,
        "notifications_enabled": 8,
        "notifications_paused": 2,
        "users_with_group": 9,
        "distinct_groups": 4,
        "reminders_enabled": 7,
        "evening_enabled": 6,
        "schedule_updates_enabled": 5,
        "digest_enabled": 3,
        "quiet_hours_enabled": 2,
        "temporarily_muted": 1,
        "language_uk": 7,
        "language_en": 3,
        "language_other": 0,
        "active_24h": 4,
        "active_7d": 8,
        "new_24h": 1,
        "new_7d": 2,
        "top_groups": [{"group_name": "КН-21", "count": 3}],
    }


class AdminDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_uses_real_activity_and_clear_notification_label(self):
        handlers = DashboardHandlers()
        callback = make_callback()

        with (
            patch.object(admin, "SENIOR_ID", 1),
            patch.object(admin.db, "get_statistics", new=AsyncMock(return_value=statistics_fixture())),
        ):
            await handlers.process_admin_stats(callback)

        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Активні за 24 год", text)
        self.assertIn("Сповіщення увімкнено", text)
        self.assertNotIn("🟢 Активних:", text)
        callback.answer.assert_awaited_once()

    async def test_users_page_escapes_group_name(self):
        handlers = DashboardHandlers()
        callback = make_callback()
        stats = statistics_fixture()
        stats["top_groups"] = [{"group_name": "A < B", "count": 2}]

        with (
            patch.object(admin, "SENIOR_ID", 1),
            patch.object(admin.db, "get_statistics", new=AsyncMock(return_value=stats)),
        ):
            await handlers.process_admin_users(callback)

        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("A &lt; B", text)
        self.assertNotIn("A < B", text)

    async def test_non_admin_cannot_open_dashboard(self):
        handlers = DashboardHandlers()
        callback = make_callback(user_id=2)

        with patch.object(admin, "SENIOR_ID", 1):
            await handlers.process_admin_stats(callback)

        callback.message.edit_text.assert_not_awaited()

    async def test_notifications_page_reports_settings_without_calling_them_active_users(self):
        handlers = DashboardHandlers()
        callback = make_callback()

        with (
            patch.object(admin, "SENIOR_ID", 1),
            patch.object(admin.db, "get_statistics", new=AsyncMock(return_value=statistics_fixture())),
        ):
            await handlers.process_admin_notifications(callback)

        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("Глобально увімкнено", text)
        self.assertIn("Ранковий дайджест", text)
        self.assertNotIn("Активних", text)

    async def test_system_page_shows_version_scheduler_and_database_size(self):
        handlers = DashboardHandlers()
        callback = make_callback()
        job_monitor.clear()

        with (
            patch.object(admin, "SENIOR_ID", 1),
            patch.object(admin, "APP_VERSION", "v1.4.0"),
            patch.object(admin.db, "database_size_bytes", return_value=2048),
        ):
            await handlers.process_admin_system(callback)

        text = callback.message.edit_text.await_args.args[0]
        self.assertIn("v1.4.0", text)
        self.assertIn("Scheduler: <b>працює</b>", text)
        self.assertIn("2.0 КБ", text)
        self.assertIn("Ще немає даних", text)

    def test_admin_keyboard_separates_dashboard_and_test_actions(self):
        main_callbacks = [
            button.callback_data for row in KeyboardMixin.get_admin_keyboard().inline_keyboard for button in row
        ]
        test_callbacks = [
            button.callback_data for row in KeyboardMixin.get_admin_tests_keyboard().inline_keyboard for button in row
        ]

        self.assertIn("admin_system", main_callbacks)
        self.assertIn("admin_tests", main_callbacks)
        self.assertNotIn("admin_test_evening", main_callbacks)
        self.assertIn("admin_test_evening", test_callbacks)


class ActivityMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_user_and_continues_to_handler(self):
        middleware = UserActivityMiddleware()
        handler = AsyncMock(return_value="handled")
        event = SimpleNamespace()
        data = {"event_from_user": SimpleNamespace(id=42, language_code="en-US")}

        with patch("bot.middleware.db.record_user_activity", new=AsyncMock()) as record_activity:
            result = await middleware(handler, event, data)

        self.assertEqual("handled", result)
        record_activity.assert_awaited_once_with(42, "en")
        handler.assert_awaited_once_with(event, data)

    async def test_tracking_failure_does_not_block_update(self):
        middleware = UserActivityMiddleware()
        handler = AsyncMock(return_value="handled")
        data = {"event_from_user": SimpleNamespace(id=42, language_code="uk")}

        with patch(
            "bot.middleware.db.record_user_activity",
            new=AsyncMock(side_effect=RuntimeError("database busy")),
        ):
            result = await middleware(handler, SimpleNamespace(), data)

        self.assertEqual("handled", result)
        handler.assert_awaited_once()


class JobMonitorTests(unittest.TestCase):
    def test_tracks_success_and_failure_without_storing_traceback(self):
        monitor = JobMonitor()
        monitor.handle_event(SimpleNamespace(job_id="schedule_updates", exception=None))
        monitor.handle_event(SimpleNamespace(job_id="evening_schedule", exception=TimeoutError("secret details")))

        self.assertTrue(monitor.get("schedule_updates").succeeded)
        failure = monitor.get("evening_schedule")
        self.assertFalse(failure.succeeded)
        self.assertEqual("TimeoutError", failure.error_type)
        self.assertFalse(hasattr(failure, "traceback"))
