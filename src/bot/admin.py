from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.common import pdf_cache
from config import APP_VERSION, SENIOR_ID
from i18n.messages import get_html_msg, get_msg
from infrastructure import database as db
from jobs.monitoring import job_monitor
from jobs.scheduler import promote_groups_dry_run
from schedule import service as scraper
from schedule.formatting import html_link, lesson_html

KYIV_TZ = ZoneInfo("Europe/Kyiv")
MONITORED_JOBS = (
    ("evening_schedule", "Вечірній розклад"),
    ("daily_reminders", "Планування нагадувань"),
    ("schedule_updates", "Перевірка змін"),
    ("group_promotion", "Переведення груп"),
)


def format_duration(total_seconds: float) -> str:
    total_minutes = max(0, int(total_seconds // 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} год")
    if minutes or not parts:
        parts.append(f"{minutes} хв")
    return " ".join(parts)


def format_size(size_bytes: int) -> str:
    size = float(max(0, size_bytes))
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return "0 Б"


def admin_updated_at() -> str:
    return datetime.now(KYIV_TZ).strftime("%d.%m.%Y %H:%M:%S")


class AdminHandlerMixin:
    @staticmethod
    def _is_admin(user_id: int) -> bool:
        return bool(SENIOR_ID and user_id == SENIOR_ID)

    async def process_send_pdf(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        key = callback.data.split(":", 1)[1]
        url = pdf_cache.get(key)
        if not url:
            await callback.answer(get_msg("pdf.expired", language=language), show_alert=True)
            return
        await callback.answer(get_msg("pdf.loading", language=language), show_alert=False)
        try:
            await callback.message.answer_document(
                document=url,
                caption=get_msg("pdf.caption", language=language),
                parse_mode="HTML",
            )
        except Exception as error:
            import logging

            logging.error("Помилка відправки PDF документу: %s", error)
            await callback.message.answer(get_msg("pdf.error", language=language))

    async def process_delete_msg(self, callback: CallbackQuery):
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()

    async def process_admin_stats(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        stats = await db.get_statistics()
        text = (
            "📊 <b>Огляд бота</b>\n\n"
            f"👥 Усього користувачів: <b>{stats['total']}</b>\n"
            f"🟢 Активні за 24 год: <b>{stats['active_24h']}</b>\n"
            f"📅 Активні за 7 днів: <b>{stats['active_7d']}</b>\n"
            f"🆕 Нові за 24 год / 7 днів: <b>{stats['new_24h']} / {stats['new_7d']}</b>\n"
            f"🔔 Сповіщення увімкнено: <b>{stats['notifications_enabled']}</b>\n"
            f"🎓 Зареєстрованих груп: <b>{stats['distinct_groups']}</b>\n\n"
            "<i>Активність і нові реєстрації враховуються з моменту встановлення цієї версії.</i>\n\n"
            f"🕒 Оновлено: {admin_updated_at()}"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=self.get_admin_section_keyboard("admin_stats"),
        )
        await callback.answer()

    async def process_admin_users(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        stats = await db.get_statistics()
        without_group = stats["total"] - stats["users_with_group"]
        text = (
            "👥 <b>Користувачі</b>\n\n"
            f"✅ Із групою: <b>{stats['users_with_group']}</b>\n"
            f"➖ Без групи: <b>{without_group}</b>\n"
            f"🎓 Унікальних груп: <b>{stats['distinct_groups']}</b>\n\n"
            "🌐 <b>Мови</b>\n"
            f"🇺🇦 Українська: <b>{stats['language_uk']}</b>\n"
            f"🇬🇧 Англійська: <b>{stats['language_en']}</b>\n"
            f"❔ Інші/невідомі: <b>{stats['language_other']}</b>\n\n"
            "🏆 <b>Топ 5 груп</b>\n"
        )
        for index, group in enumerate(stats["top_groups"], 1):
            text += f"{index}. {escape(str(group['group_name']))} — {group['count']}\n"
        if not stats["top_groups"]:
            text += "—\n"
        text += f"\n🕒 Оновлено: {admin_updated_at()}"
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=self.get_admin_section_keyboard("admin_users"),
        )
        await callback.answer()

    async def process_admin_notifications(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        stats = await db.get_statistics()
        text = (
            "🔔 <b>Сповіщення</b>\n\n"
            f"✅ Глобально увімкнено: <b>{stats['notifications_enabled']}</b>\n"
            f"⏸ Призупинено: <b>{stats['notifications_paused']}</b>\n"
            f"🔕 Вимкнено до завтра: <b>{stats['temporarily_muted']}</b>\n\n"
            f"⏳ Нагадування про пари: <b>{stats['reminders_enabled']}</b>\n"
            f"🌙 Вечірній розклад: <b>{stats['evening_enabled']}</b>\n"
            f"⚠️ Зміни розкладу: <b>{stats['schedule_updates_enabled']}</b>\n"
            f"☀️ Ранковий дайджест: <b>{stats['digest_enabled']}</b>\n"
            f"🌘 Тихі години: <b>{stats['quiet_hours_enabled']}</b>\n\n"
            f"🕒 Оновлено: {admin_updated_at()}"
        )
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=self.get_admin_section_keyboard("admin_notifications"),
        )
        await callback.answer()

    async def process_admin_system(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        now = datetime.now(timezone.utc)
        scheduler_running = bool(self.scheduler and self.scheduler.running)
        jobs_count = len(self.scheduler.get_jobs()) if self.scheduler is not None else 0
        text = (
            "🩺 <b>Стан системи</b>\n\n"
            f"🏷 Версія: <b>{escape(APP_VERSION)}</b>\n"
            f"⏱ Uptime: <b>{format_duration((now - self.started_at).total_seconds())}</b>\n"
            f"🗄 База даних: <b>{format_size(db.database_size_bytes())}</b>\n"
            f"⚙️ Scheduler: <b>{'працює' if scheduler_running else 'зупинений'}</b>\n"
            f"📌 Запланованих jobs: <b>{jobs_count}</b>\n\n"
            "🕓 <b>Останні фонові запуски</b>\n"
        )
        has_statuses = False
        for job_id, label in MONITORED_JOBS:
            status = job_monitor.get(job_id)
            if status is None:
                continue
            has_statuses = True
            icon = "✅" if status.succeeded else "❌"
            finished_at = status.finished_at.astimezone(KYIV_TZ).strftime("%d.%m %H:%M")
            suffix = "" if status.succeeded else f" ({escape(status.error_type or 'Error')})"
            text += f"{icon} {label}: {finished_at}{suffix}\n"
        if not has_statuses:
            text += "Ще немає даних після запуску бота.\n"
        text += f"\n🕒 Оновлено: {admin_updated_at()}"
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=self.get_admin_section_keyboard("admin_system"),
        )
        await callback.answer()

    async def process_admin_tests(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        await callback.message.edit_text(
            "🧪 <b>Тестові дії</b>\nОберіть перевірку:",
            parse_mode="HTML",
            reply_markup=self.get_admin_tests_keyboard(),
        )
        await callback.answer()

    async def process_admin_home(self, callback: CallbackQuery):
        if not self._is_admin(callback.from_user.id):
            return
        await callback.message.edit_text(
            "👑 <b>Адмін Панель</b>\nОберіть розділ нижче:",
            parse_mode="HTML",
            reply_markup=self.get_admin_keyboard(),
        )
        await callback.answer()

    async def process_admin_test_evening(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID:
            return
        user = await db.get_user(SENIOR_ID)
        if not user or not user["group_name"]:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return

        await callback.answer("Формування тестового розкладу...", show_alert=False)
        schedule = await scraper.parse_schedule_for_tomorrow(user["group_name"])
        text = get_msg("schedule.evening_title", "🌙 <b>[ТЕСТ] Розклад на завтра:</b>") + "\n"
        if schedule:
            has_pdf = False
            for item in schedule:
                if item.get("is_pdf"):
                    if not has_pdf:
                        text += f"\n<s>{'—' * 25}</s>\n\n"
                        has_pdf = True
                    text += f"📄 {html_link(item['name'], item.get('viewer_url'))}\n"
                else:
                    text += f"⏰ <b>{escape(str(item['time']))}</b> - {lesson_html(item)}\n"
            await callback.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await callback.message.answer("Пар на завтра немає (результат тесту).")

    async def process_admin_test_update(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID:
            return
        user = await db.get_user(SENIOR_ID)
        if not user or not user["group_name"]:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return
        await callback.answer("Перевірка змін розкладу...", show_alert=False)
        has_changes = await scraper.check_schedule_changes(user["group_name"])
        await callback.message.answer(
            "⚠️ Зміни розкладу знайдено! (Симуляція спрацювала)" if has_changes else "✅ Змін розкладу не виявлено."
        )

    async def process_admin_test_reminder(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID:
            return
        user = await db.get_user(SENIOR_ID)
        if not user or not user["group_name"]:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return
        offset = dict(user).get("reminder_offset", 10)
        text = get_html_msg(
            "reminders.class_starts",
            "⏳ За {time_str} почнеться пара:\n<b>{subject_name}</b>",
            time_str=f"{offset} хв",
            subject_name="[ТЕСТ] Основи програмування",
        )
        await callback.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="✅ Прочитано", callback_data="delete_msg")]]
            ),
        )
        await callback.answer("Відправлено тестове нагадування.")

    async def process_admin_test_promote(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID:
            return
        await callback.answer("Запускаю аналіз груп...", show_alert=False)
        report = await promote_groups_dry_run(callback.bot)
        if len(report) > 3000:
            report = report[:3000] + "\n... (обрізано)"
        await callback.message.answer(
            f"🧪 <b>Dry-Run переведення:</b>\n<pre>{escape(str(report))}</pre>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Закрити", callback_data="delete_msg")]]
            ),
        )
