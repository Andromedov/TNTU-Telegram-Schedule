"""Compatibility facade and router composition for Telegram handlers."""

from aiogram import F, Router
from aiogram.filters import Command
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# These module exports remain available for integrations and older tests.
import database as db
import scraper
from bot_handlers.admin import AdminHandlerMixin
from bot_handlers.base import HandlerBaseMixin
from bot_handlers.campus import CampusHandlerMixin
from bot_handlers.common import (
    UserState,
    get_pdf_key as _get_pdf_key,
    ics_cooldown as _ics_cooldown,
    pdf_cache as _pdf_cache,
)
from bot_handlers.keyboards import KeyboardMixin
from bot_handlers.lifecycle import LifecycleHandlerMixin
from bot_handlers.schedule import ScheduleHandlerMixin
from bot_handlers.settings import SettingsHandlerMixin


class ScheduleBotHandlers(
        LifecycleHandlerMixin,
        ScheduleHandlerMixin,
        CampusHandlerMixin,
        SettingsHandlerMixin,
        AdminHandlerMixin,
        KeyboardMixin,
        HandlerBaseMixin,
):
    def __init__(self, router: Router, scheduler: AsyncIOScheduler | None = None):
        self.router = router
        self.scheduler = scheduler
        self._register_handlers()

    def _register_handlers(self):
        """Зв'язує feature handlers з єдиним aiogram router."""
        self.router.message.register(self.cmd_start, Command("start"))
        self.router.message.register(self.cmd_settings, Command("settings"))
        self.router.message.register(self.cmd_campus, Command("campus"))
        self.router.message.register(self.cmd_admin, Command("admin"))

        self.router.message.register(self.process_group_name_fsm, UserState.waiting_for_group)

        self.router.callback_query.register(
            self.process_nav_schedule, F.data.startswith("nav_schedule:")
        )
        self.router.callback_query.register(self.process_nav_week, F.data.startswith("nav_week:"))
        self.router.callback_query.register(
            self.process_calendar_selection, F.data.startswith("cal:")
        )
        self.router.callback_query.register(
            self.process_ask_custom_date, F.data == "ask_custom_date"
        )
        self.router.callback_query.register(
            self.process_export_ics, F.data.startswith("export_ics:")
        )
        self.router.callback_query.register(
            self.process_share_day, F.data.startswith("share_day:")
        )
        self.router.callback_query.register(
            self.process_share_week, F.data.startswith("share_week:")
        )
        self.router.callback_query.register(self.process_show_campus, F.data == "show_campus")
        self.router.callback_query.register(
            self.process_campus_building, F.data.startswith("campus_building:")
        )

        self.router.callback_query.register(self.process_show_settings, F.data == "show_settings")
        self.router.callback_query.register(
            self.process_settings_language, F.data == "settings_language"
        )
        self.router.callback_query.register(
            self.process_set_language, F.data.startswith("set_language:")
        )
        self.router.callback_query.register(
            self.process_settings_reminder, F.data == "settings_reminder"
        )
        self.router.callback_query.register(
            self.process_set_remind, F.data.startswith("set_remind:")
        )
        self.router.callback_query.register(
            self.process_settings_first_reminder, F.data == "settings_first_reminder"
        )
        self.router.callback_query.register(
            self.process_set_first_remind, F.data.startswith("set_first_remind:")
        )
        self.router.callback_query.register(
            self.process_settings_lesson_types, F.data == "settings_lesson_types"
        )
        self.router.callback_query.register(
            self.process_toggle_reminder_type, F.data.startswith("toggle_reminder_type:")
        )
        self.router.callback_query.register(
            self.process_settings_quiet_hours, F.data == "settings_quiet_hours"
        )
        self.router.callback_query.register(
            self.process_toggle_quiet_hours, F.data == "toggle_quiet_hours"
        )
        self.router.callback_query.register(
            self.process_choose_quiet_hour, F.data.startswith("choose_quiet_hour:")
        )
        self.router.callback_query.register(
            self.process_set_quiet_hour, F.data.startswith("set_quiet_hour:")
        )
        self.router.callback_query.register(
            self.process_settings_digest, F.data == "settings_digest"
        )
        self.router.callback_query.register(
            self.process_set_digest, F.data.startswith("set_digest:")
        )
        self.router.callback_query.register(
            self.process_settings_mute_today, F.data == "settings_mute_today"
        )
        self.router.callback_query.register(
            self.process_settings_unmute_today, F.data == "settings_unmute_today"
        )
        self.router.callback_query.register(
            self.process_reminder_mute_today, F.data == "reminder_mute_today"
        )
        self.router.callback_query.register(
            self.process_snooze_reminder, F.data == "snooze_reminder"
        )

        self.router.callback_query.register(self.process_change_group, F.data == "change_group")
        self.router.callback_query.register(self.process_back_to_main, F.data == "back_to_main")
        self.router.callback_query.register(self.process_toggles, F.data.startswith("toggle_"))
        self.router.callback_query.register(
            self.process_send_pdf, F.data.startswith("send_pdf:")
        )
        self.router.callback_query.register(self.process_delete_msg, F.data == "delete_msg")

        self.router.callback_query.register(self.process_admin_stats, F.data == "admin_stats")
        self.router.callback_query.register(
            self.process_admin_test_evening, F.data == "admin_test_evening"
        )
        self.router.callback_query.register(
            self.process_admin_test_update, F.data == "admin_test_update"
        )
        self.router.callback_query.register(
            self.process_admin_test_reminder, F.data == "admin_test_reminder"
        )
        self.router.callback_query.register(
            self.process_admin_test_promote, F.data == "admin_test_promote"
        )
        self.router.message.register(self.process_any_text, F.text)
