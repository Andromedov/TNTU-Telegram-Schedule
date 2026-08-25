from html import escape

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.common import pdf_cache
from config import SENIOR_ID
from i18n.messages import get_html_msg, get_msg
from infrastructure import database as db
from jobs.scheduler import promote_groups_dry_run
from schedule import service as scraper
from schedule.formatting import html_link, lesson_html


class AdminHandlerMixin:
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
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID:
            return
        stats = await db.get_statistics()
        text = (
            f"📊 <b>Статистика:</b>\n👥 Всього: <b>{escape(str(stats['total']))}</b>\n"
            f"🟢 Активних: <b>{escape(str(stats['active']))}</b>\n\n🏆 <b>Топ 5:</b>\n"
        )
        for index, group in enumerate(stats["top_groups"], 1):
            text += f"{index}. {escape(str(group['group_name']))} ({escape(str(group['count']))})\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=self.get_admin_keyboard())
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
