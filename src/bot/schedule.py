import asyncio
import logging
from datetime import datetime, timedelta
from html import escape

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.calendar import get_calendar_keyboard
from bot.common import get_pdf_key, ics_cooldown
from campus.directory import schedule_buildings
from i18n.messages import get_html_msg, get_msg, trusted_html
from infrastructure import database as db
from schedule import service as scraper
from schedule.formatting import lesson_html
from schedule.ics import generate_week_ics
from schedule.saturday import get_saturday_source
from schedule.sharing import build_day_share, build_week_share, get_share_message_keyboard


class ScheduleHandlerMixin:
    @staticmethod
    async def _resolve_schedule_group(user, state: FSMContext | None = None) -> str | None:
        if state is not None:
            state_data = await state.get_data()
            view_group = state_data.get("view_group")
            if view_group:
                return str(view_group)
        return str(user["group_name"]) if user and user["group_name"] else None

    async def _get_next_class_text(self, group_name: str, language: str = "uk") -> str:
        schedule = await scraper.parse_schedule_for_today(group_name)
        now = datetime.now()
        for item in schedule:
            if item.get("is_pdf"):
                continue
            try:
                start_time = item["time"].split("-")[0].strip()
                hour, minute = map(int, start_time.split(":"))
                class_time = now.replace(hour=hour, minute=minute, second=0)
                if class_time > now:
                    return get_html_msg(
                        "start.next_class",
                        language=language,
                        time=start_time,
                        subject=trusted_html(lesson_html(item)),
                    )
            except Exception:
                pass
        return get_msg("start.no_more_classes", language=language)

    async def _generate_schedule_ui(
        self,
        user_id: int,
        offset: int,
        group_name: str | None = None,
    ) -> tuple[str, InlineKeyboardMarkup]:
        user = await db.get_user(user_id)
        language = self._user_language(user)
        selected_group = group_name or (str(user["group_name"]) if user and user["group_name"] else None)
        if not selected_group:
            return get_msg("group.need_group", language=language), self.get_main_keyboard(language)

        target_date = datetime.now() + timedelta(days=offset)
        schedule = await scraper._get_schedule_for_date(selected_group, target_date)
        weekdays = get_msg("schedule.weekdays", language=language).split("|")

        relative_day = ""
        if offset == 0:
            relative_day = get_msg("schedule.today_relative", language=language)
        elif offset == 1:
            relative_day = get_msg("schedule.tomorrow_relative", language=language)
        elif offset == -1:
            relative_day = get_msg("schedule.yesterday_relative", language=language)

        text = get_html_msg(
            "schedule.day_header",
            language=language,
            day=weekdays[target_date.weekday()],
            relative=relative_day,
            date=target_date.strftime("%d.%m.%Y"),
            group=selected_group,
        )
        saturday_source = get_saturday_source(schedule)
        if saturday_source:
            source_weekday, source_week = saturday_source
            text += get_html_msg(
                "schedule.saturday_notice",
                language=language,
                weekday=weekdays[source_weekday],
                week=source_week,
            )
        pdf_buttons = []
        if not schedule:
            text += get_msg("schedule.no_classes_today", language=language)
        else:
            has_pdf = False
            for item in schedule:
                if item.get("is_pdf"):
                    if not has_pdf:
                        text += f"\n<s>{'—' * 25}</s>\n\n"
                        has_pdf = True
                    text += f"📄 <b>{escape(str(item['name']))}</b>\n"
                    pdf_key = get_pdf_key(item["url"])
                    pdf_buttons.append(
                        [
                            InlineKeyboardButton(
                                text=get_msg("keyboard.open_web", language=language),
                                url=item["viewer_url"],
                            ),
                            InlineKeyboardButton(
                                text=get_msg("keyboard.get_file", language=language),
                                callback_data=f"send_pdf:{pdf_key}",
                            ),
                        ]
                    )
                else:
                    text += f"⏰ <b>{escape(str(item['time']))}</b> - {lesson_html(item)}\n"

        extra_buttons = (
            self.get_building_shortcuts(schedule_buildings([schedule]), f"nav_schedule:{offset}") + pdf_buttons
        )
        return text, self.get_schedule_nav_keyboard(offset, extra_buttons, language)

    async def _generate_week_schedule_ui(
        self,
        user_id: int,
        offset_weeks: int,
        group_name: str | None = None,
    ) -> tuple[str, InlineKeyboardMarkup]:
        user = await db.get_user(user_id)
        language = self._user_language(user)
        selected_group = group_name or (str(user["group_name"]) if user and user["group_name"] else None)
        if not selected_group:
            return get_msg("group.need_group", language=language), self.get_main_keyboard(language)

        now = datetime.now()
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)
        sunday = monday + timedelta(days=6)
        text = get_html_msg(
            "schedule.week_header",
            language=language,
            start=monday.strftime("%d.%m"),
            end=sunday.strftime("%d.%m"),
            group=selected_group,
        )
        weekdays = get_msg("schedule.weekdays", language=language).split("|")
        tasks = [scraper._get_schedule_for_date(selected_group, monday + timedelta(days=index)) for index in range(7)]
        week_schedules = await asyncio.gather(*tasks)

        all_pdfs = {}
        has_any_classes = False
        for index, schedule in enumerate(week_schedules):
            current_date = monday + timedelta(days=index)
            day_classes = []
            for item in schedule:
                if item.get("is_pdf"):
                    all_pdfs[item["url"]] = item
                else:
                    day_classes.append(item)

            if day_classes:
                has_any_classes = True
                text += f"🔹 <b>{escape(weekdays[index])} ({current_date.strftime('%d.%m')}):</b>\n"
                saturday_source = get_saturday_source(day_classes)
                if saturday_source:
                    source_weekday, source_week = saturday_source
                    text += get_html_msg(
                        "schedule.saturday_notice",
                        language=language,
                        weekday=weekdays[source_weekday],
                        week=source_week,
                    )
                for item in day_classes:
                    text += f"  ⏰ <b>{escape(str(item['time']))}</b> - {lesson_html(item)}\n"
                text += "\n"

        if not has_any_classes:
            text += get_msg("schedule.no_classes_week", language=language) + "\n\n"

        pdf_buttons = []
        if all_pdfs:
            text += f"<s>{'—' * 25}</s>\n\n"
            for pdf in all_pdfs.values():
                text += f"📄 <b>{escape(str(pdf['name']))}</b>\n"
                pdf_key = get_pdf_key(pdf["url"])
                pdf_buttons.append(
                    [
                        InlineKeyboardButton(
                            text=get_msg("keyboard.open_web", language=language), url=pdf["viewer_url"]
                        ),
                        InlineKeyboardButton(
                            text=get_msg("keyboard.get_file", language=language),
                            callback_data=f"send_pdf:{pdf_key}",
                        ),
                    ]
                )

        if len(text) > 3900:
            cut_index = text.rfind("\n", 0, 3900)
            if cut_index != -1:
                text = text[:cut_index] + "\n\n" + get_msg("schedule.truncated", language=language)

        extra_buttons = (
            self.get_building_shortcuts(schedule_buildings(week_schedules), f"nav_week:{offset_weeks}") + pdf_buttons
        )
        return text, self.get_week_nav_keyboard(offset_weeks, extra_buttons, language)

    async def process_nav_schedule(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])
        try:
            await callback.message.edit_text(get_msg("schedule.loading", language=language))
        except TelegramBadRequest:
            pass
        text, keyboard = await self._generate_schedule_ui(callback.from_user.id, offset, group_name)
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard
            )
            await callback.answer(get_msg("schedule.updated", language=language))
        except TelegramBadRequest as error:
            if "not modified" in str(error).lower():
                await callback.answer(get_msg("schedule.current", language=language))
            else:
                await callback.answer()

    async def process_nav_week(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])
        try:
            await callback.message.edit_text(get_msg("schedule.forming", language=language))
        except TelegramBadRequest:
            pass
        text, keyboard = await self._generate_week_schedule_ui(callback.from_user.id, offset, group_name)
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard
            )
            await callback.answer(get_msg("schedule.updated", language=language))
        except TelegramBadRequest as error:
            if "not modified" in str(error).lower():
                await callback.answer(get_msg("schedule.current", language=language))
            else:
                await callback.answer()

    async def process_share_day(self, callback: CallbackQuery, state: FSMContext | None = None):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return
        try:
            offset = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(get_msg("share.invalid", language=language), show_alert=True)
            return

        await callback.answer(get_msg("share.preparing", language=language))
        target_date = datetime.now() + timedelta(days=offset)
        schedule = await scraper._get_schedule_for_date(group_name, target_date)
        shared = build_day_share(group_name, target_date, schedule, language)
        if not shared:
            await callback.message.answer(get_msg("share.empty_day", language=language))
            return
        html_text, plain_text = shared
        await callback.message.answer(
            html_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=get_share_message_keyboard(plain_text, language),
        )

    async def process_share_week(self, callback: CallbackQuery, state: FSMContext | None = None):
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return
        try:
            offset_weeks = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(get_msg("share.invalid", language=language), show_alert=True)
            return

        await callback.answer(get_msg("share.preparing", language=language))
        now = datetime.now()
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)
        week_schedules = await asyncio.gather(
            *[
                scraper._get_schedule_for_date(group_name, monday + timedelta(days=day_offset))
                for day_offset in range(7)
            ]
        )
        shared = build_week_share(group_name, monday, week_schedules, language)
        if not shared:
            await callback.message.answer(get_msg("share.empty_week", language=language))
            return
        html_text, plain_text = shared
        await callback.message.answer(
            html_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=get_share_message_keyboard(plain_text, language),
        )

    async def process_export_ics(self, callback: CallbackQuery, state: FSMContext | None = None):
        user_id = callback.from_user.id
        now = datetime.now()
        language = await self._get_user_language(user_id, callback.from_user.language_code)

        for key in [key for key, value in ics_cooldown.items() if (now - value).total_seconds() > 300]:
            ics_cooldown.pop(key, None)
        last_time = ics_cooldown.get(user_id)
        if last_time and (now - last_time).total_seconds() < 45:
            await callback.answer(get_msg("export.cooldown", language=language), show_alert=True)
            return
        ics_cooldown[user_id] = now

        await callback.answer(get_msg("export.generating", language=language), show_alert=False)
        user = await db.get_user(user_id)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            return
        offset_weeks = int(callback.data.split(":")[1])
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)
        week_schedules = await asyncio.gather(
            *[scraper._get_schedule_for_date(group_name, monday + timedelta(days=index)) for index in range(7)]
        )
        schedule_data = {
            monday + timedelta(days=index): schedule for index, schedule in enumerate(week_schedules) if schedule
        }
        ics_content = generate_week_ics(group_name, schedule_data)
        if not ics_content.strip() or "BEGIN:VEVENT" not in ics_content:
            await callback.message.answer(get_msg("export.empty", language=language))
            return

        file = BufferedInputFile(
            ics_content.encode("utf-8"),
            filename=f"Schedule_{group_name}_{monday.strftime('%d_%m')}.ics",
        )
        try:
            await callback.message.answer_document(
                document=file,
                caption=get_msg("export.caption", language=language),
                parse_mode="HTML",
            )
        except Exception as error:
            logging.error("Помилка відправки ICS файлу: %s", error)
            await callback.message.answer(get_msg("export.error", language=language))

    async def process_ask_custom_date(self, callback: CallbackQuery, state: FSMContext):
        now = datetime.now()
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("schedule.ask_date", language=language),
            parse_mode="HTML",
            reply_markup=get_calendar_keyboard(now.year, now.month, language),
        )
        await callback.answer()

    async def process_calendar_selection(self, callback: CallbackQuery, state: FSMContext | None = None):
        data = callback.data.split(":")
        action = data[1]
        if action == "ignore":
            await callback.answer()
            return

        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        group_name = await self._resolve_schedule_group(user, state)
        if not group_name:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        if action in {"prev", "next"}:
            try:
                year, month = int(data[2]), int(data[3])
            except (IndexError, ValueError):
                await callback.answer(get_msg("calendar.navigation_error", language=language), show_alert=True)
                return
            month += -1 if action == "prev" else 1
            if month == 0:
                month, year = 12, year - 1
            if month == 13:
                month, year = 1, year + 1
            try:
                await callback.message.edit_reply_markup(reply_markup=get_calendar_keyboard(year, month, language))
            except TelegramBadRequest:
                pass
            await callback.answer()
            return

        now = datetime.now()
        try:
            target_date = (
                now
                if action == "today"
                else (
                    now + timedelta(days=1)
                    if action == "tomorrow"
                    else datetime(int(data[2]), int(data[3]), int(data[4]))
                )
            )
        except (IndexError, ValueError):
            await callback.answer(get_msg("calendar.selection_error", language=language), show_alert=True)
            return
        offset = (target_date.date() - now.date()).days
        text, keyboard = await self._generate_schedule_ui(callback.from_user.id, offset, group_name)
        try:
            await callback.message.edit_text(
                text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
