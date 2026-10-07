from datetime import timedelta

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from i18n.messages import get_msg, normalize_language
from infrastructure import database as db
from jobs.reminders import REMINDER_LESSON_TYPES, kyiv_now, muted_until_tomorrow
from jobs.scheduler import remove_user_jobs, schedule_daily_reminders, send_snoozed_reminder


class SettingsHandlerMixin:
    async def process_settings_subgroup(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_subgroup", language=language),
            parse_mode="HTML",
            reply_markup=self.get_subgroup_keyboard(language),
        )
        await callback.answer()

    async def process_set_subgroup(self, callback: CallbackQuery):
        value = callback.data.split(":", 1)[1]
        if value not in {"0", "1", "2"}:
            language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        await db.update_setting(callback.from_user.id, "subgroup", int(value) or None)
        await self._show_updated_settings(callback)
        scheduler = getattr(self, "scheduler", None)
        if scheduler is not None:
            remove_user_jobs(scheduler, callback.from_user.id)
            await schedule_daily_reminders(callback.bot, scheduler)

    async def process_show_settings(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
        )
        await callback.answer()

    async def process_settings_reminder(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_reminder", language=language),
            parse_mode="HTML",
            reply_markup=self.get_reminder_settings_keyboard(language),
        )
        await callback.answer()

    async def process_set_remind(self, callback: CallbackQuery):
        offset = int(callback.data.split(":")[1])
        user_id = callback.from_user.id
        if offset == 0:
            await db.update_setting(user_id, "notify_10_min", 0)
        else:
            await db.update_setting(user_id, "notify_10_min", 1)
            await db.update_setting(user_id, "reminder_offset", offset)
        await self._show_updated_settings(callback)

    async def process_settings_first_reminder(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_first_reminder", language=language),
            parse_mode="HTML",
            reply_markup=self.get_first_reminder_settings_keyboard(language),
        )
        await callback.answer()

    async def process_set_first_remind(self, callback: CallbackQuery):
        raw_value = callback.data.split(":", 1)[1]
        if raw_value not in {"default", "15", "30", "60", "90"}:
            language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        value = None if raw_value == "default" else int(raw_value)
        await db.update_setting(callback.from_user.id, "first_class_reminder_offset", value)
        await self._show_updated_settings(callback)

    async def process_settings_digest(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_digest", language=language),
            parse_mode="HTML",
            reply_markup=self.get_digest_settings_keyboard(language),
        )
        await callback.answer()

    async def process_set_digest(self, callback: CallbackQuery):
        raw_value = callback.data.split(":", 1)[1]
        if raw_value not in {"off", "6", "7", "8", "9"}:
            language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        if raw_value == "off":
            await db.update_setting(callback.from_user.id, "morning_digest", 0)
        else:
            await db.update_setting(callback.from_user.id, "morning_digest_hour", int(raw_value))
            await db.update_setting(callback.from_user.id, "morning_digest", 1)
        await self._show_updated_settings(callback)

    async def process_settings_lesson_types(self, callback: CallbackQuery):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_lesson_types", language=language),
            parse_mode="HTML",
            reply_markup=self.get_lesson_type_settings_keyboard(user, language),
        )
        await callback.answer()

    async def process_toggle_reminder_type(self, callback: CallbackQuery):
        category = callback.data.split(":", 1)[1]
        setting = REMINDER_LESSON_TYPES.get(category)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if setting is None or user is None:
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        await db.update_setting(callback.from_user.id, setting, 0 if user[setting] else 1)
        updated_user = await db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(
            reply_markup=self.get_lesson_type_settings_keyboard(updated_user, language)
        )
        await callback.answer(get_msg("settings.saved", language=language))

    async def process_settings_quiet_hours(self, callback: CallbackQuery):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_quiet_hours", language=language),
            parse_mode="HTML",
            reply_markup=self.get_quiet_hours_keyboard(user, language),
        )
        await callback.answer()

    async def process_toggle_quiet_hours(self, callback: CallbackQuery):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if user is None:
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        enabled = user["quiet_hours_start"] is not None and user["quiet_hours_end"] is not None
        await db.update_setting(callback.from_user.id, "quiet_hours_start", None if enabled else 22)
        await db.update_setting(callback.from_user.id, "quiet_hours_end", None if enabled else 7)
        updated_user = await db.get_user(callback.from_user.id)
        await callback.message.edit_reply_markup(reply_markup=self.get_quiet_hours_keyboard(updated_user, language))
        await callback.answer(get_msg("settings.saved", language=language))

    async def process_choose_quiet_hour(self, callback: CallbackQuery):
        boundary = callback.data.split(":", 1)[1]
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        if boundary not in {"start", "end"}:
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        await callback.message.edit_text(
            get_msg(f"settings.choose_quiet_{boundary}", language=language),
            parse_mode="HTML",
            reply_markup=self.get_quiet_hour_keyboard(boundary, language),
        )
        await callback.answer()

    async def process_set_quiet_hour(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        try:
            _, boundary, raw_hour = callback.data.split(":")
            hour = int(raw_hour)
        except (ValueError, TypeError):
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        if boundary not in {"start", "end"} or not 0 <= hour <= 23:
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return

        user = await db.get_user(callback.from_user.id)
        other_boundary = "end" if boundary == "start" else "start"
        other_value = user[f"quiet_hours_{other_boundary}"] if user is not None else None
        default_other = 7 if boundary == "start" else 22
        comparison_hour = int(other_value) if other_value is not None else default_other
        if comparison_hour == hour:
            await callback.answer(get_msg("settings.quiet_same_hour", language=language), show_alert=True)
            return

        await db.update_setting(callback.from_user.id, f"quiet_hours_{boundary}", hour)
        if other_value is None:
            await db.update_setting(callback.from_user.id, f"quiet_hours_{other_boundary}", default_other)
        updated_user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            get_msg("settings.choose_quiet_hours", language=language),
            parse_mode="HTML",
            reply_markup=self.get_quiet_hours_keyboard(updated_user, language),
        )
        await callback.answer(get_msg("settings.saved", language=language))

    async def process_settings_mute_today(self, callback: CallbackQuery):
        await db.update_setting(callback.from_user.id, "notifications_muted_until", muted_until_tomorrow())
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(user))
        await callback.answer(get_msg("reminders.muted_today", language=language), show_alert=True)

    async def process_settings_unmute_today(self, callback: CallbackQuery):
        await db.update_setting(callback.from_user.id, "notifications_muted_until", None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(user))
        await callback.answer(get_msg("reminders.unmuted_today", language=language))

    async def process_reminder_mute_today(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await db.update_setting(callback.from_user.id, "notifications_muted_until", muted_until_tomorrow())
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(get_msg("reminders.muted_today", language=language), show_alert=True)

    async def process_snooze_reminder(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        if self.scheduler is None:
            await callback.answer(get_msg("reminders.snooze_unavailable", language=language), show_alert=True)
            return
        html_text = callback.message.html_text or callback.message.text
        if not html_text:
            await callback.answer(get_msg("reminders.snooze_unavailable", language=language), show_alert=True)
            return
        self.scheduler.add_job(
            send_snoozed_reminder,
            "date",
            run_date=kyiv_now() + timedelta(minutes=5),
            args=[callback.bot, callback.from_user.id, html_text],
            id=f"snooze:{callback.from_user.id}:{callback.message.message_id}",
            replace_existing=True,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer(get_msg("reminders.snoozed", language=language))

    async def process_settings_language(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_language", language=language),
            parse_mode="HTML",
            reply_markup=self.get_language_keyboard(),
        )
        await callback.answer()

    async def process_set_language(self, callback: CallbackQuery):
        language = normalize_language(callback.data.split(":", 1)[1])
        await db.update_setting(callback.from_user.id, "language", language)
        user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
        )
        await callback.answer(get_msg("settings.language_changed", language=language))

    async def process_toggles(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        user = await db.get_user(user_id)
        if callback.data == "toggle_evening":
            await db.update_setting(user_id, "notify_evening", 0 if user["notify_evening"] else 1)
        elif callback.data == "toggle_pause":
            await db.update_setting(user_id, "is_paused", 0 if user["is_paused"] else 1)
        elif callback.data == "toggle_notify_schedule_update":
            await db.update_setting(user_id, "notify_schedule_update", 0 if user["notify_schedule_update"] else 1)
        updated_user = await db.get_user(user_id)
        language = self._user_language(updated_user, callback.from_user.language_code)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(updated_user))
        await callback.answer(get_msg("settings.updated", language=language))

    async def _show_updated_settings(self, callback: CallbackQuery):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
        )
        await callback.answer(get_msg("settings.saved", language=language))
