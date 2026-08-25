from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, \
    BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime, timedelta
import logging
import asyncio
import hashlib

import database as db
import scraper
from scheduler import promote_groups_dry_run, send_snoozed_reminder
from messages import get_msg, normalize_language
from config import SENIOR_ID
from calendar_ui import get_calendar_keyboard
from ics_generator import generate_week_ics
from schedule_sharing import build_day_share, build_week_share, get_share_message_keyboard
from schedule_formatting import lesson_html
from reminder_utils import (
    REMINDER_LESSON_TYPES,
    kyiv_now,
    muted_until_tomorrow,
    temporary_notifications_are_muted,
)
from campus import BUILDINGS, CAMPUS_MAP_URL, building_card, schedule_buildings

# ==========================================
#          КЕШ ТА СТАТИСТИКА
# ==========================================
_pdf_cache: dict[str, str] = {}  # Зберігає {uuid: url}
_ics_cooldown: dict[int, datetime] = {}  # Rate-limit для експорту ICS


def _get_pdf_key(url: str) -> str:
    """Генерує короткий ключ для збереження довгих URL в callback_data."""
    key = hashlib.md5(url.encode('utf-8')).hexdigest()[:10]
    _pdf_cache[key] = url
    if len(_pdf_cache) > 500:
        first_key = next(iter(_pdf_cache), None)
        if first_key:
            _pdf_cache.pop(first_key, None)
    return key


class UserState(StatesGroup):
    waiting_for_group = State()


class ScheduleBotHandlers:
    def __init__(self, router: Router, scheduler: AsyncIOScheduler | None = None):
        self.router = router
        self.scheduler = scheduler
        self._register_handlers()

    @staticmethod
    def _user_language(user_data, telegram_language: str | None = None) -> str:
        if user_data:
            language = dict(user_data).get("language")
            if language:
                return normalize_language(language)
        return normalize_language(telegram_language)

    async def _get_user_language(self, user_id: int, telegram_language: str | None = None) -> str:
        return self._user_language(await db.get_user(user_id), telegram_language)

    @staticmethod
    def get_language_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🇺🇦 Українська", callback_data="set_language:uk")],
            [InlineKeyboardButton(text="🇬🇧 English", callback_data="set_language:en")],
        ])

    async def _cleanup_old_ui(self, message: Message, state: FSMContext):
        """Редагує попереднє повідомлення меню, закриваючи його, щоб запобігти спаму."""
        data = await state.get_data()
        old_msg_id = data.get("last_ui_msg_id")
        if old_msg_id:
            language = await self._get_user_language(message.from_user.id, message.from_user.language_code)
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=old_msg_id,
                    text=get_msg("start.menu_closed", language=language),
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception:
                pass

    @staticmethod
    def get_bot_commands(language: str = "uk") -> list[BotCommand]:
        """Повертає список команд для автоматичного встановлення меню бота в Telegram."""
        return [
            BotCommand(command="start", description=get_msg('commands.start', language=language)),
            BotCommand(command="settings", description=get_msg('commands.settings', language=language)),
            BotCommand(command="campus", description=get_msg('commands.campus', language=language)),
        ]

    # ==========================================
    #               КЛАВІАТУРИ
    # ==========================================

    @staticmethod
    def get_main_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        """Головне меню бота."""
        kb = [
            [
                InlineKeyboardButton(text=get_msg('keyboard.show_schedule', language=language), callback_data="nav_schedule:0"),
                InlineKeyboardButton(text=get_msg('keyboard.show_week', language=language), callback_data="nav_week:0")
            ],
            [InlineKeyboardButton(text=get_msg('keyboard.campus', language=language), callback_data="show_campus")],
            [InlineKeyboardButton(text=get_msg('keyboard.settings', language=language), callback_data="show_settings")],
            [InlineKeyboardButton(text=get_msg('keyboard.change_group', language=language),
                                  callback_data="change_group")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_campus_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton(text=f"🏫 К{number}", callback_data=f"campus_building:{number}")
            for number in BUILDINGS
        ]
        rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
        rows.extend([
            [InlineKeyboardButton(text=get_msg("campus.map", language=language), url=CAMPUS_MAP_URL)],
            [InlineKeyboardButton(text=get_msg("keyboard.back", language=language), callback_data="back_to_main")],
        ])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_building_keyboard(language: str = "uk", back_callback: str = "show_campus") -> InlineKeyboardMarkup:
        back_text = get_msg(
            "campus.all" if back_callback == "show_campus" else "calendar.back",
            language=language,
        )
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_msg("campus.map", language=language), url=CAMPUS_MAP_URL)],
            [InlineKeyboardButton(text=back_text, callback_data=back_callback)],
            [InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")],
        ])

    @staticmethod
    def get_building_shortcuts(
            buildings: list[int], back_callback: str | None = None
    ) -> list[list[InlineKeyboardButton]]:
        buttons = [
            InlineKeyboardButton(
                text=f"🏫 К{number}",
                callback_data=f"campus_building:{number}" + (f":{back_callback}" if back_callback else ""),
            )
            for number in buildings
        ]
        return [buttons[index:index + 3] for index in range(0, len(buttons), 3)]

    @staticmethod
    def get_settings_keyboard(user_data) -> InlineKeyboardMarkup:
        """Меню налаштувань."""
        user_dict = dict(user_data)
        language = ScheduleBotHandlers._user_language(user_dict)
        notify_enabled = user_dict.get('notify_10_min', 1)
        offset = user_dict.get('reminder_offset', 10)
        first_offset = user_dict.get('first_class_reminder_offset')
        digest_enabled = user_dict.get('morning_digest', 0)
        digest_hour = int(user_dict.get('morning_digest_hour') or 7)
        quiet_start = user_dict.get('quiet_hours_start')
        quiet_end = user_dict.get('quiet_hours_end')
        enabled_types = sum(
            bool(user_dict.get(setting, 1)) for setting in REMINDER_LESSON_TYPES.values()
        )

        if not notify_enabled:
            remind_text = get_msg("settings.reminder_off", language=language)
        elif offset == 60:
            remind_text = get_msg("settings.reminder_hour", language=language)
        elif offset == 90:
            remind_text = get_msg("settings.reminder_hour_half", language=language)
        else:
            remind_text = get_msg("settings.reminder_minutes", language=language, minutes=offset)

        if first_offset is None:
            first_remind_text = get_msg("settings.first_reminder_default", language=language)
        else:
            first_remind_text = get_msg(
                "settings.first_reminder_minutes", language=language, minutes=first_offset
            )
        digest_text = get_msg(
            "settings.digest_enabled" if digest_enabled else "settings.digest_disabled",
            language=language,
            hour=digest_hour,
        )
        mute_text = get_msg(
            "settings.unmute_today" if temporary_notifications_are_muted(user_dict) else "settings.mute_today",
            language=language,
        )
        mute_callback = "settings_unmute_today" if temporary_notifications_are_muted(user_dict) \
            else "settings_mute_today"
        if enabled_types == len(REMINDER_LESSON_TYPES):
            lesson_types_text = get_msg("settings.lesson_types_all", language=language)
        elif enabled_types == 0:
            lesson_types_text = get_msg("settings.lesson_types_none", language=language)
        else:
            lesson_types_text = get_msg(
                "settings.lesson_types_selected", language=language,
                enabled=enabled_types, total=len(REMINDER_LESSON_TYPES),
            )
        quiet_text = get_msg(
            "settings.quiet_hours_range" if quiet_start is not None and quiet_end is not None
            else "settings.quiet_hours_off",
            language=language,
            start=f"{int(quiet_start or 0):02d}", end=f"{int(quiet_end or 0):02d}",
        )

        kb = [
            [InlineKeyboardButton(text=remind_text, callback_data="settings_reminder")],
            [InlineKeyboardButton(text=first_remind_text, callback_data="settings_first_reminder")],
            [InlineKeyboardButton(text=lesson_types_text, callback_data="settings_lesson_types")],
            [InlineKeyboardButton(text=digest_text, callback_data="settings_digest")],
            [InlineKeyboardButton(text=quiet_text, callback_data="settings_quiet_hours")],
            [InlineKeyboardButton(text=mute_text, callback_data=mute_callback)],
            [InlineKeyboardButton(
                text=f"{'✅' if user_dict.get('notify_evening', 1) else '❌'} {get_msg('keyboard.evening', language=language)}",
                callback_data="toggle_evening"
            )],
            [InlineKeyboardButton(
                text=f"{'⏸' if user_dict.get('is_paused', 0) else '▶️'} {get_msg('keyboard.pause', language=language)}",
                callback_data="toggle_pause"
            )],
            [InlineKeyboardButton(
                text=f"{'✅' if user_dict.get('notify_schedule_update', 1) else '❌'} {get_msg('keyboard.schedule_update', language=language)}",
                callback_data="toggle_notify_schedule_update"
            )],
            [InlineKeyboardButton(text=get_msg('keyboard.language', language=language), callback_data="settings_language")],
            [InlineKeyboardButton(text=get_msg('keyboard.back', language=language), callback_data="back_to_main")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_reminder_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        """Підменю вибору часу нагадування."""
        kb = [
            [InlineKeyboardButton(text=get_msg("settings.disable", language=language), callback_data="set_remind:0")],
            [InlineKeyboardButton(text=get_msg("settings.minutes", language=language, minutes=10), callback_data="set_remind:10"),
             InlineKeyboardButton(text=get_msg("settings.minutes", language=language, minutes=15), callback_data="set_remind:15"),
             InlineKeyboardButton(text=get_msg("settings.minutes", language=language, minutes=30), callback_data="set_remind:30")],
            [InlineKeyboardButton(text=get_msg("settings.one_hour", language=language), callback_data="set_remind:60"),
             InlineKeyboardButton(text=get_msg("settings.hour_half", language=language), callback_data="set_remind:90")],
            [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_first_reminder_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        kb = [
            [InlineKeyboardButton(text=get_msg("settings.same_as_other", language=language),
                                  callback_data="set_first_remind:default")],
            [InlineKeyboardButton(text=get_msg("settings.minutes", language=language, minutes=15),
                                  callback_data="set_first_remind:15"),
             InlineKeyboardButton(text=get_msg("settings.minutes", language=language, minutes=30),
                                  callback_data="set_first_remind:30")],
            [InlineKeyboardButton(text=get_msg("settings.one_hour", language=language),
                                  callback_data="set_first_remind:60"),
             InlineKeyboardButton(text=get_msg("settings.hour_half", language=language),
                                  callback_data="set_first_remind:90")],
            [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_digest_settings_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
        kb = [
            [InlineKeyboardButton(text=get_msg("settings.digest_off", language=language),
                                  callback_data="set_digest:off")],
            [InlineKeyboardButton(text="06:00", callback_data="set_digest:6"),
             InlineKeyboardButton(text="07:00", callback_data="set_digest:7")],
            [InlineKeyboardButton(text="08:00", callback_data="set_digest:8"),
             InlineKeyboardButton(text="09:00", callback_data="set_digest:9")],
            [InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="show_settings")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_lesson_type_settings_keyboard(user_data, language: str = "uk") -> InlineKeyboardMarkup:
        user_dict = dict(user_data)
        rows = []
        for category, setting in REMINDER_LESSON_TYPES.items():
            enabled = bool(user_dict.get(setting, 1))
            label = get_msg(f"settings.lesson_type_{category}", language=language)
            rows.append([InlineKeyboardButton(
                text=f"{'✅' if enabled else '❌'} {label}",
                callback_data=f"toggle_reminder_type:{category}",
            )])
        rows.append([InlineKeyboardButton(
            text=get_msg("calendar.back", language=language), callback_data="show_settings"
        )])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_quiet_hours_keyboard(user_data, language: str = "uk") -> InlineKeyboardMarkup:
        user_dict = dict(user_data)
        start = user_dict.get("quiet_hours_start")
        end = user_dict.get("quiet_hours_end")
        enabled = start is not None and end is not None
        start = int(start) if start is not None else 22
        end = int(end) if end is not None else 7
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=get_msg(
                    "settings.quiet_disable" if enabled else "settings.quiet_enable",
                    language=language,
                ),
                callback_data="toggle_quiet_hours",
            )],
            [InlineKeyboardButton(
                text=get_msg("settings.quiet_start", language=language, hour=f"{start:02d}"),
                callback_data="choose_quiet_hour:start",
            )],
            [InlineKeyboardButton(
                text=get_msg("settings.quiet_end", language=language, hour=f"{end:02d}"),
                callback_data="choose_quiet_hour:end",
            )],
            [InlineKeyboardButton(
                text=get_msg("calendar.back", language=language), callback_data="show_settings"
            )],
        ])

    @staticmethod
    def get_quiet_hour_keyboard(boundary: str, language: str = "uk") -> InlineKeyboardMarkup:
        rows = []
        buttons = [
            InlineKeyboardButton(
                text=f"{hour:02d}:00", callback_data=f"set_quiet_hour:{boundary}:{hour}"
            )
            for hour in range(24)
        ]
        rows.extend(buttons[index:index + 4] for index in range(0, len(buttons), 4))
        rows.append([InlineKeyboardButton(
            text=get_msg("calendar.back", language=language), callback_data="settings_quiet_hours"
        )])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    @staticmethod
    def get_schedule_nav_keyboard(offset: int, extra_buttons: list = None, language: str = "uk") -> InlineKeyboardMarkup:
        """Клавіатура для навігації по днях."""
        kb = [
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"nav_schedule:{offset - 1}"),
                InlineKeyboardButton(text=get_msg("keyboard.refresh", language=language), callback_data=f"nav_schedule:{offset}"),
                InlineKeyboardButton(text="➡️", callback_data=f"nav_schedule:{offset + 1}")
            ],
            [
                InlineKeyboardButton(text=get_msg('keyboard.custom_date', language=language), callback_data="ask_custom_date")
            ],
            [InlineKeyboardButton(text=get_msg("share.day_button", language=language),
                                  callback_data=f"share_day:{offset}")]
        ]

        if extra_buttons:
            kb.extend(extra_buttons)

        kb.append([InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_week_nav_keyboard(offset: int, extra_buttons: list = None, language: str = "uk") -> InlineKeyboardMarkup:
        kb = [
            [
                InlineKeyboardButton(text=get_msg("keyboard.previous_week", language=language), callback_data=f"nav_week:{offset - 1}"),
                InlineKeyboardButton(text="🔄", callback_data=f"nav_week:{offset}"),
                InlineKeyboardButton(text=get_msg("keyboard.next_week", language=language), callback_data=f"nav_week:{offset + 1}")
            ],
            [InlineKeyboardButton(text=get_msg("keyboard.current_week", language=language), callback_data="nav_week:0")],
            [InlineKeyboardButton(text=get_msg("keyboard.export_ics", language=language), callback_data=f"export_ics:{offset}")],
            [InlineKeyboardButton(text=get_msg("share.week_button", language=language),
                                  callback_data=f"share_week:{offset}")]
        ]
        if extra_buttons: kb.extend(extra_buttons)
        kb.append([InlineKeyboardButton(text=get_msg("keyboard.menu", language=language), callback_data="back_to_main")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_admin_keyboard() -> InlineKeyboardMarkup:
        """Клавіатура адмін-панелі."""
        kb = [
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🧪 Тест: Вечірній розклад", callback_data="admin_test_evening")],
            [InlineKeyboardButton(text="🧪 Тест: Перевірка змін", callback_data="admin_test_update")],
            [InlineKeyboardButton(text="🧪 Тест: Нагадування", callback_data="admin_test_reminder")],
            [InlineKeyboardButton(text="🧪 Dry-run: Переведення груп", callback_data="admin_test_promote")],
            [InlineKeyboardButton(text="🔙 Закрити", callback_data="back_to_main")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    # ==========================================
    #            ГЕНЕРАЦІЯ РОЗКЛАДУ
    # ==========================================

    async def _get_next_class_text(self, group_name: str, language: str = "uk") -> str:
        """Повертає рядок з наступною парою для головного меню."""
        schedule = await scraper.parse_schedule_for_today(group_name)
        now = datetime.now()
        for item in schedule:
            if item.get('is_pdf'): continue
            try:
                start_time_str = item['time'].split('-')[0].strip()
                h, m = map(int, start_time_str.split(':'))
                class_time = now.replace(hour=h, minute=m, second=0)
                if class_time > now:
                    return get_msg("start.next_class", language=language, time=start_time_str,
                                   subject=lesson_html(item))
            except Exception:
                pass
        return get_msg("start.no_more_classes", language=language)

    async def _generate_schedule_ui(self, user_id: int, offset: int) -> tuple[str, InlineKeyboardMarkup]:
        """Генерує текст розкладу та клавіатуру для заданого offset (зміщення в днях)."""
        user = await db.get_user(user_id)
        language = self._user_language(user)
        if not user or not user['group_name']:
            return get_msg("group.need_group", language=language), self.get_main_keyboard(language)

        target_date = datetime.now() + timedelta(days=offset)
        schedule = await scraper._get_schedule_for_date(user['group_name'], target_date)

        weekdays = get_msg("schedule.weekdays", language=language).split("|")
        day_name = weekdays[target_date.weekday()]
        date_str = target_date.strftime("%d.%m.%Y")

        if offset == 0:
            relative_day = get_msg("schedule.today_relative", language=language)
        elif offset == 1:
            relative_day = get_msg("schedule.tomorrow_relative", language=language)
        elif offset == -1:
            relative_day = get_msg("schedule.yesterday_relative", language=language)
        else:
            relative_day = ""

        text = get_msg("schedule.day_header", language=language, day=day_name, relative=relative_day,
                       date=date_str, group=user['group_name'])
        pdf_buttons = []

        if not schedule:
            text += get_msg("schedule.no_classes_today", language=language)
        else:
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                        has_pdf = True
                    text += f"📄 <b>{item['name']}</b>\n"
                    pdf_key = _get_pdf_key(item['url'])
                    pdf_buttons.append([
                        InlineKeyboardButton(text=get_msg("keyboard.open_web", language=language), url=item['viewer_url']),
                        InlineKeyboardButton(text=get_msg("keyboard.get_file", language=language), callback_data=f"send_pdf:{pdf_key}")
                    ])
                else:
                    text += f"⏰ <b>{item['time']}</b> - {lesson_html(item)}\n"

        extra_buttons = self.get_building_shortcuts(
            schedule_buildings([schedule]), f"nav_schedule:{offset}"
        ) + pdf_buttons
        return text, self.get_schedule_nav_keyboard(offset, extra_buttons, language)

    async def _generate_week_schedule_ui(self, user_id: int, offset_weeks: int) -> tuple[str, InlineKeyboardMarkup]:
        """Генерує розклад на весь тиждень (Пн-Нд)."""
        user = await db.get_user(user_id)
        language = self._user_language(user)
        if not user or not user['group_name']:
            return get_msg("group.need_group", language=language), self.get_main_keyboard(language)

        now = datetime.now()
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)
        sunday = monday + timedelta(days=6)

        text = get_msg("schedule.week_header", language=language, start=monday.strftime('%d.%m'),
                       end=sunday.strftime('%d.%m'), group=user['group_name'])

        weekdays = get_msg("schedule.weekdays", language=language).split("|")

        all_pdfs = {}
        has_any_classes = False

        tasks = [scraper._get_schedule_for_date(user['group_name'], monday + timedelta(days=i)) for i in range(7)]
        week_schedules = await asyncio.gather(*tasks)

        for i, schedule in enumerate(week_schedules):
            current_date = monday + timedelta(days=i)
            day_classes = []
            for item in schedule:
                if item.get('is_pdf'):
                    all_pdfs[item['url']] = item
                else:
                    day_classes.append(item)

            if day_classes:
                has_any_classes = True
                text += f"🔹 <b>{weekdays[i]} ({current_date.strftime('%d.%m')}):</b>\n"
                for item in day_classes:
                    text += f"  ⏰ <b>{item['time']}</b> - {lesson_html(item)}\n"
                text += "\n"

        if not has_any_classes:
            text += get_msg("schedule.no_classes_week", language=language) + "\n\n"

        pdf_buttons = []
        if all_pdfs:
            text += f"<s>{'—' * 25}</s>\n\n"
            for pdf in all_pdfs.values():
                text += f"📄 <b>{pdf['name']}</b>\n"
                pdf_key = _get_pdf_key(pdf['url'])
                pdf_buttons.append([
                    InlineKeyboardButton(text=get_msg("keyboard.open_web", language=language), url=pdf['viewer_url']),
                    InlineKeyboardButton(text=get_msg("keyboard.get_file", language=language), callback_data=f"send_pdf:{pdf_key}")
                ])

        if len(text) > 3900:
            cut_idx = text.rfind('\n', 0, 3900)
            if cut_idx != -1:
                text = text[:cut_idx] + "\n\n" + get_msg("schedule.truncated", language=language)

        extra_buttons = self.get_building_shortcuts(
            schedule_buildings(week_schedules), f"nav_week:{offset_weeks}"
        ) + pdf_buttons
        return text, self.get_week_nav_keyboard(offset_weeks, extra_buttons, language)

    # ==========================================
    #            ОБРОБНИКИ
    # ==========================================

    async def cmd_start(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        if not user or not user['group_name']:
            await db.add_or_update_user(message.from_user.id, language=language)
            msg = await message.answer(get_msg("start.greeting_new", language=language))
            await state.set_state(UserState.waiting_for_group)
            await state.update_data(prompt_msg_id=msg.message_id, last_ui_msg_id=msg.message_id)
        else:
            next_class = await self._get_next_class_text(user['group_name'], language)
            msg = await message.answer(
                get_msg("start.greeting_existing", language=language,
                        name=message.from_user.first_name, group=user['group_name'],
                        next_class=next_class),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=self.get_main_keyboard(language)
            )
            await state.update_data(last_ui_msg_id=msg.message_id)

    async def cmd_settings(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)
        if not user or not user['group_name']:
            await message.answer(get_msg("group.need_group", language=language))
            return

        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        msg = await message.answer(get_msg("settings.title", language=language),
                                   parse_mode="HTML",
                                   reply_markup=self.get_settings_keyboard(user))
        await state.update_data(last_ui_msg_id=msg.message_id)

    async def cmd_campus(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass
        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)
        msg = await message.answer(
            get_msg("campus.title", language=language),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_campus_keyboard(language),
        )
        await state.update_data(last_ui_msg_id=msg.message_id)

    async def cmd_admin(self, message: Message, state: FSMContext):
        """Відкриває адмін-панель"""
        try:
            await message.delete()
        except Exception:
            pass
        if not SENIOR_ID or message.from_user.id != SENIOR_ID: return
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        msg = await message.answer("👑 <b>Адмін Панель</b>\nОберіть дію нижче:", parse_mode="HTML",
                                   reply_markup=self.get_admin_keyboard())
        await state.update_data(last_ui_msg_id=msg.message_id)

    # ==========================================
    #          ОБРОБНИКИ СТАНІВ (FSM)
    # ==========================================

    async def process_group_name_fsm(self, message: Message, state: FSMContext):
        group_name = message.text.upper().strip()
        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)

        try:
            await message.delete()
        except Exception:
            pass

        data = await state.get_data()
        prompt_msg_id = data.get("prompt_msg_id")

        clean_name = group_name.replace("-", "").replace(" ", "")
        if len(clean_name) < 3 or not any(c.isalpha() for c in clean_name) or not any(c.isdigit() for c in clean_name):
            error_text = get_msg("group.invalid", language=language)
            new_msg = await message.answer(error_text, parse_mode="HTML")
            await state.update_data(prompt_msg_id=new_msg.message_id, last_ui_msg_id=new_msg.message_id)
            return

        checking_text = get_msg("group.checking", language=language, group=group_name)
        processing_msg = await message.answer(checking_text, parse_mode="HTML")

        is_valid = await scraper.check_group_exists(group_name)

        if is_valid:
            await db.add_or_update_user(message.from_user.id, group_name, language)
            await processing_msg.edit_text(get_msg("group.saved", language=language, group_name=group_name),
                                           parse_mode="HTML",
                                           reply_markup=self.get_main_keyboard(language))
            await state.set_state(None)
            await state.update_data(last_ui_msg_id=processing_msg.message_id)
        else:
            await processing_msg.edit_text(get_msg("group.not_found", language=language, group=group_name),
                                           parse_mode="HTML")
            await state.update_data(last_ui_msg_id=processing_msg.message_id)

    async def process_any_text(self, message: Message):
        """Обробник для будь-якого тексту, якщо користувач не в стані зміни групи чи дати."""
        try:
            await message.delete()
        except Exception:
            pass

    # ==========================================
    #            ОБРОБНИКИ КОЛБЕКІВ
    # ==========================================

    async def process_nav_schedule(self, callback: CallbackQuery, state: FSMContext):
        """Обробник динамічного графіка розкладу з гортанням по днях."""
        await state.set_state(None)

        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])

        try:
            await callback.message.edit_text(get_msg("schedule.loading", language=language))
        except TelegramBadRequest:
            pass

        text, kb = await self._generate_schedule_ui(callback.from_user.id, offset)

        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=kb
            )
            await callback.answer(get_msg("schedule.updated", language=language))
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                await callback.answer(get_msg("schedule.current", language=language))
            else:
                await callback.answer()

    async def process_nav_week(self, callback: CallbackQuery, state: FSMContext):
        """Обробник розкладу на тиждень."""
        await state.set_state(None)

        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])

        try:
            await callback.message.edit_text(get_msg("schedule.forming", language=language))
        except TelegramBadRequest:
            pass

        text, kb = await self._generate_week_schedule_ui(callback.from_user.id, offset)

        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=kb
            )
            await callback.answer(get_msg("schedule.updated", language=language))
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                await callback.answer(get_msg("schedule.current", language=language))
            else:
                await callback.answer()

    async def process_share_day(self, callback: CallbackQuery):
        """Створює окреме повідомлення з денним розкладом для пересилання."""
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        try:
            offset = int(callback.data.split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer(get_msg("share.invalid", language=language), show_alert=True)
            return

        await callback.answer(get_msg("share.preparing", language=language))
        target_date = datetime.now() + timedelta(days=offset)
        schedule = await scraper._get_schedule_for_date(user['group_name'], target_date)
        shared = build_day_share(user['group_name'], target_date, schedule, language)
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

    async def process_share_week(self, callback: CallbackQuery):
        """Створює окреме повідомлення з тижневим розкладом для пересилання."""
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
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
        tasks = [
            scraper._get_schedule_for_date(user['group_name'], monday + timedelta(days=day_offset))
            for day_offset in range(7)
        ]
        week_schedules = await asyncio.gather(*tasks)
        shared = build_week_share(user['group_name'], monday, week_schedules, language)
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

    async def process_export_ics(self, callback: CallbackQuery):
        """Обробник експорту розкладу у файл .ics."""
        global _ics_cooldown
        user_id = callback.from_user.id
        now = datetime.now()
        language = await self._get_user_language(user_id, callback.from_user.language_code)

        keys_to_delete = [k for k, v in _ics_cooldown.items() if (now - v).total_seconds() > 300]
        for k in keys_to_delete:
            _ics_cooldown.pop(k, None)

        last_time = _ics_cooldown.get(user_id)
        if last_time and (now - last_time).total_seconds() < 45:
            await callback.answer(get_msg("export.cooldown", language=language), show_alert=True)
            return
        _ics_cooldown[user_id] = now

        await callback.answer(get_msg("export.generating", language=language), show_alert=False)
        user = await db.get_user(user_id)
        if not user or not user['group_name']: return

        offset_weeks = int(callback.data.split(":")[1])
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)

        tasks = [scraper._get_schedule_for_date(user['group_name'], monday + timedelta(days=i)) for i in range(7)]
        week_schedules = await asyncio.gather(*tasks)

        schedule_data = {monday + timedelta(days=i): schedule for i, schedule in enumerate(week_schedules) if schedule}
        ics_content = generate_week_ics(user['group_name'], schedule_data)

        if not ics_content.strip() or "BEGIN:VEVENT" not in ics_content:
            await callback.message.answer(get_msg("export.empty", language=language))
            return

        file = BufferedInputFile(ics_content.encode('utf-8'),
                                 filename=f"Schedule_{user['group_name']}_{monday.strftime('%d_%m')}.ics")

        caption_text = get_msg("export.caption", language=language)

        try:
            await callback.message.answer_document(document=file, caption=caption_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Помилка відправки ICS файлу: {e}")
            await callback.message.answer(get_msg("export.error", language=language))

    async def process_ask_custom_date(self, callback: CallbackQuery, state: FSMContext):
        """Відображає інлайн-календар для вибору дати."""
        now = datetime.now()
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(get_msg("schedule.ask_date", language=language), parse_mode="HTML",
                                         reply_markup=get_calendar_keyboard(now.year, now.month, language))
        await callback.answer()

    async def process_calendar_selection(self, callback: CallbackQuery):
        """Обробляє натискання кнопок на календарі."""
        data = callback.data.split(":")
        action = data[1]

        if action == "ignore":
            await callback.answer()
            return

        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", language=language), show_alert=True)
            return

        # Гортання місяців
        if action in ["prev", "next"]:
            try:
                year, month = int(data[2]), int(data[3])
            except (IndexError, ValueError):
                await callback.answer(get_msg("calendar.navigation_error", language=language), show_alert=True)
                return

            month += -1 if action == "prev" else 1
            if month == 0: month, year = 12, year - 1
            if month == 13: month, year = 1, year + 1
            try:
                await callback.message.edit_reply_markup(reply_markup=get_calendar_keyboard(year, month, language))
            except TelegramBadRequest:
                pass
            await callback.answer()
            return

        # Обробка вибору конкретної дати
        now = datetime.now()
        try:
            target_date = now if action == "today" else (
                now + timedelta(days=1) if action == "tomorrow" else datetime(int(data[2]), int(data[3]), int(data[4])))
        except (IndexError, ValueError):
            await callback.answer(get_msg("calendar.selection_error", language=language), show_alert=True)
            return

        offset = (target_date.date() - now.date()).days
        text, kb = await self._generate_schedule_ui(callback.from_user.id, offset)
        try:
            await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
        except TelegramBadRequest:
            pass
        await callback.answer()

    async def process_show_settings(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(get_msg("settings.title", language=language),
                                         parse_mode="HTML",
                                         reply_markup=self.get_settings_keyboard(user))
        await callback.answer()

    async def process_show_campus(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("campus.title", language=language),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_campus_keyboard(language),
        )
        await callback.answer()

    async def process_campus_building(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        try:
            parts = callback.data.split(":")
            number = int(parts[1])
        except (IndexError, ValueError):
            await callback.answer(get_msg("campus.unknown", language=language), show_alert=True)
            return

        text = building_card(number, language)
        if text is None:
            await callback.answer(get_msg("campus.unknown", language=language), show_alert=True)
            return
        back_callback = ":".join(parts[2:]) if len(parts) > 2 else "show_campus"
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_building_keyboard(language, back_callback),
        )
        await callback.answer()

    async def process_settings_reminder(self, callback: CallbackQuery):
        """Відкриває підменю вибору часу нагадування."""
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_reminder", language=language),
            parse_mode="HTML",
            reply_markup=self.get_reminder_settings_keyboard(language)
        )
        await callback.answer()

    async def process_set_remind(self, callback: CallbackQuery):
        """Зберігає обраний час нагадування в БД."""
        offset = int(callback.data.split(":")[1])
        user_id = callback.from_user.id

        if offset == 0:
            await db.update_setting(user_id, 'notify_10_min', 0)
        else:
            await db.update_setting(user_id, 'notify_10_min', 1)
            await db.update_setting(user_id, 'reminder_offset', offset)

        updated_user = await db.get_user(user_id)
        language = self._user_language(updated_user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(updated_user)
        )
        await callback.answer(get_msg("settings.saved", language=language))

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
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
        )
        await callback.answer(get_msg("settings.saved", language=language))

    async def process_settings_digest(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.choose_digest", language=language),
            parse_mode="HTML",
            reply_markup=self.get_digest_settings_keyboard(language),
        )
        await callback.answer()

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
        await callback.message.edit_reply_markup(
            reply_markup=self.get_quiet_hours_keyboard(updated_user, language)
        )
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
            await db.update_setting(
                callback.from_user.id, f"quiet_hours_{other_boundary}", default_other
            )
        updated_user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            get_msg("settings.choose_quiet_hours", language=language),
            parse_mode="HTML",
            reply_markup=self.get_quiet_hours_keyboard(updated_user, language),
        )
        await callback.answer(get_msg("settings.saved", language=language))

    async def process_set_digest(self, callback: CallbackQuery):
        raw_value = callback.data.split(":", 1)[1]
        if raw_value not in {"off", "6", "7", "8", "9"}:
            language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
            await callback.answer(get_msg("settings.invalid_value", language=language), show_alert=True)
            return
        if raw_value == "off":
            await db.update_setting(callback.from_user.id, "morning_digest", 0)
        else:
            hour = int(raw_value)
            await db.update_setting(callback.from_user.id, "morning_digest_hour", hour)
            await db.update_setting(callback.from_user.id, "morning_digest", 1)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        await callback.message.edit_text(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
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

        run_date = kyiv_now() + timedelta(minutes=5)
        message_id = callback.message.message_id
        self.scheduler.add_job(
            send_snoozed_reminder,
            "date",
            run_date=run_date,
            args=[callback.bot, callback.from_user.id, html_text],
            id=f"snooze:{callback.from_user.id}:{message_id}",
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

    async def process_change_group(self, callback: CallbackQuery, state: FSMContext):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        await callback.message.edit_text(get_msg("group.ask_new", language=language))
        await state.set_state(UserState.waiting_for_group)
        await state.update_data(prompt_msg_id=callback.message.message_id, last_ui_msg_id=callback.message.message_id)
        await callback.answer()

    async def process_back_to_main(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user['group_name']:
            await callback.message.edit_text(get_msg("group.need_group", language=language),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard(language)
            )
            return
        next_class = await self._get_next_class_text(user['group_name'], language)
        await callback.message.edit_text(
            get_msg("start.main_menu_title", language=language,
                    group=user['group_name'], next_class=next_class),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_main_keyboard(language))
        await callback.answer()

    async def process_toggles(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        user = await db.get_user(user_id)
        if callback.data == "toggle_evening":
            await db.update_setting(user_id, 'notify_evening', 0 if user['notify_evening'] else 1)
        elif callback.data == "toggle_pause":
            await db.update_setting(user_id, 'is_paused', 0 if user['is_paused'] else 1)
        elif callback.data == "toggle_notify_schedule_update":
            await db.update_setting(user_id, 'notify_schedule_update', 0 if user['notify_schedule_update'] else 1)

        updated_user = await db.get_user(user_id)
        language = self._user_language(updated_user, callback.from_user.language_code)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(updated_user))
        await callback.answer(get_msg("settings.updated", language=language))

    async def process_send_pdf(self, callback: CallbackQuery):
        language = await self._get_user_language(callback.from_user.id, callback.from_user.language_code)
        key = callback.data.split(":", 1)[1]
        url = _pdf_cache.get(key)
        if not url:
            await callback.answer(get_msg("pdf.expired", language=language), show_alert=True)
            return
        await callback.answer(get_msg("pdf.loading", language=language), show_alert=False)
        try:
            await callback.message.answer_document(
                document=url,
                caption=get_msg("pdf.caption", language=language),
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Помилка відправки PDF документу: {e}")
            await callback.message.answer(get_msg("pdf.error", language=language))

    async def process_delete_msg(self, callback: CallbackQuery):
        """Обробник для кнопки 'Прочитано', який просто видаляє повідомлення із чату."""
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()

    # ==========================================
    #          АДМІН ПАНЕЛЬ
    # ==========================================

    async def process_admin_stats(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        stats = await db.get_statistics()
        text = f"📊 <b>Статистика:</b>\n👥 Всього: <b>{stats['total']}</b>\n🟢 Активних: <b>{stats['active']}</b>\n\n🏆 <b>Топ 5:</b>\n"

        for idx, group in enumerate(stats['top_groups'], 1):
            text += f"{idx}. {group['group_name']} ({group['count']})\n"

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=self.get_admin_keyboard())
        await callback.answer()

    async def process_admin_test_evening(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        user = await db.get_user(SENIOR_ID)
        if not user or not user['group_name']:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return

        await callback.answer("Формування тестового розкладу...", show_alert=False)
        schedule = await scraper.parse_schedule_for_tomorrow(user['group_name'])

        text = get_msg("schedule.evening_title", "🌙 <b>[ТЕСТ] Розклад на завтра:</b>") + "\n"
        if schedule:
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                        has_pdf = True
                    text += f"📄 <a href='{item['viewer_url']}'>{item['name']}</a>\n"
                else:
                    text += f"⏰ <b>{item['time']}</b> - {lesson_html(item)}\n"
            await callback.message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await callback.message.answer("Пар на завтра немає (результат тесту).")

    async def process_admin_test_update(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        user = await db.get_user(SENIOR_ID)
        if not user or not user['group_name']:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return

        await callback.answer("Перевірка змін розкладу...", show_alert=False)
        has_changes = await scraper.check_schedule_changes(user['group_name'])
        if has_changes:
            await callback.message.answer("⚠️ Зміни розкладу знайдено! (Симуляція спрацювала)")
        else:
            await callback.message.answer("✅ Змін розкладу не виявлено.")

    async def process_admin_test_reminder(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        user = await db.get_user(SENIOR_ID)

        if not user or not user['group_name']:
            await callback.answer("Вкажіть групу для тесту!", show_alert=True)
            return

        user_dict = dict(user)
        offset = user_dict.get('reminder_offset', 10)
        time_str = f"{offset} хв"
        text = get_msg("reminders.class_starts", "⏳ За {time_str} почнеться пара:\n<b>{subject_name}</b>",
                       time_str=time_str, subject_name="[ТЕСТ] Основи програмування")
        await callback.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Прочитано", callback_data="delete_msg")]]))
        await callback.answer("Відправлено тестове нагадування.")

    async def process_admin_test_promote(self, callback: CallbackQuery):
        """Нова функція: Тестування переведення груп (dry-run)"""
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        await callback.answer("Запускаю аналіз груп...", show_alert=False)
        report = await promote_groups_dry_run(callback.bot)
        if len(report) > 3000: report = report[:3000] + "\n... (обрізано)"
        await callback.message.answer(f"🧪 <b>Dry-Run переведення:</b>\n<pre>{report}</pre>", parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="Закрити", callback_data="delete_msg")]]))

    # ==========================================
    #            РЕЄСТРАЦІЯ РОУТІВ
    # ==========================================

    def _register_handlers(self):
        """Метод, який зв'язує функції класу з роутером aiogram."""

        # Команди
        self.router.message.register(self.cmd_start, Command("start"))
        self.router.message.register(self.cmd_settings, Command("settings"))
        self.router.message.register(self.cmd_campus, Command("campus"))
        self.router.message.register(self.cmd_admin, Command("admin"))

        # FSM (Очікування уводу)
        self.router.message.register(self.process_group_name_fsm, UserState.waiting_for_group)

        # Колбеки (кнопки)
        self.router.callback_query.register(self.process_nav_schedule, F.data.startswith("nav_schedule:"))
        self.router.callback_query.register(self.process_nav_week, F.data.startswith("nav_week:"))
        self.router.callback_query.register(self.process_calendar_selection, F.data.startswith("cal:"))
        self.router.callback_query.register(self.process_ask_custom_date, F.data == "ask_custom_date")
        self.router.callback_query.register(self.process_export_ics, F.data.startswith("export_ics:"))
        self.router.callback_query.register(self.process_share_day, F.data.startswith("share_day:"))
        self.router.callback_query.register(self.process_share_week, F.data.startswith("share_week:"))
        self.router.callback_query.register(self.process_show_campus, F.data == "show_campus")
        self.router.callback_query.register(self.process_campus_building, F.data.startswith("campus_building:"))

        self.router.callback_query.register(self.process_show_settings, F.data == "show_settings")
        self.router.callback_query.register(self.process_settings_language, F.data == "settings_language")
        self.router.callback_query.register(self.process_set_language, F.data.startswith("set_language:"))

        # Раути для кастомного нагадування
        self.router.callback_query.register(self.process_settings_reminder, F.data == "settings_reminder")
        self.router.callback_query.register(self.process_set_remind, F.data.startswith("set_remind:"))
        self.router.callback_query.register(
            self.process_settings_first_reminder, F.data == "settings_first_reminder"
        )
        self.router.callback_query.register(self.process_set_first_remind, F.data.startswith("set_first_remind:"))
        self.router.callback_query.register(
            self.process_settings_lesson_types, F.data == "settings_lesson_types"
        )
        self.router.callback_query.register(
            self.process_toggle_reminder_type, F.data.startswith("toggle_reminder_type:")
        )
        self.router.callback_query.register(
            self.process_settings_quiet_hours, F.data == "settings_quiet_hours"
        )
        self.router.callback_query.register(self.process_toggle_quiet_hours, F.data == "toggle_quiet_hours")
        self.router.callback_query.register(
            self.process_choose_quiet_hour, F.data.startswith("choose_quiet_hour:")
        )
        self.router.callback_query.register(
            self.process_set_quiet_hour, F.data.startswith("set_quiet_hour:")
        )
        self.router.callback_query.register(self.process_settings_digest, F.data == "settings_digest")
        self.router.callback_query.register(self.process_set_digest, F.data.startswith("set_digest:"))
        self.router.callback_query.register(self.process_settings_mute_today, F.data == "settings_mute_today")
        self.router.callback_query.register(self.process_settings_unmute_today, F.data == "settings_unmute_today")
        self.router.callback_query.register(self.process_reminder_mute_today, F.data == "reminder_mute_today")
        self.router.callback_query.register(self.process_snooze_reminder, F.data == "snooze_reminder")

        self.router.callback_query.register(self.process_change_group, F.data == "change_group")
        self.router.callback_query.register(self.process_back_to_main, F.data == "back_to_main")
        self.router.callback_query.register(self.process_toggles, F.data.startswith("toggle_"))
        self.router.callback_query.register(self.process_send_pdf, F.data.startswith("send_pdf:"))

        # Реєстрація кнопки видалення повідомлення
        self.router.callback_query.register(self.process_delete_msg, F.data == "delete_msg")

        # Адмінські колбеки
        self.router.callback_query.register(self.process_admin_stats, F.data == "admin_stats")
        self.router.callback_query.register(self.process_admin_test_evening, F.data == "admin_test_evening")
        self.router.callback_query.register(self.process_admin_test_update, F.data == "admin_test_update")
        self.router.callback_query.register(self.process_admin_test_reminder, F.data == "admin_test_reminder")
        self.router.callback_query.register(self.process_admin_test_promote, F.data == "admin_test_promote")

        self.router.message.register(self.process_any_text, F.text)
