from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, \
    BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
import logging
import asyncio
import hashlib

import database as db
import scraper
from scheduler import promote_groups_dry_run
from messages import get_msg
from config import SENIOR_ID
from calendar_ui import get_calendar_keyboard
from ics_generator import generate_week_ics

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
    def __init__(self, router: Router):
        self.router = router
        self._register_handlers()

    async def _cleanup_old_ui(self, message: Message, state: FSMContext):
        """Редагує попереднє повідомлення меню, закриваючи його, щоб запобігти спаму."""
        data = await state.get_data()
        old_msg_id = data.get("last_ui_msg_id")
        if old_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=old_msg_id,
                    text="<i>Дякую за використання бота! Меню закрито.</i> 🤖",
                    parse_mode="HTML",
                    reply_markup=None
                )
            except Exception:
                pass

    @staticmethod
    def get_bot_commands() -> list[BotCommand]:
        """Повертає список команд для автоматичного встановлення меню бота в Telegram."""
        return [
            BotCommand(command="start", description=get_msg('commands.start', "Головне меню бота")),
            BotCommand(command="settings", description=get_msg('commands.settings', "Налаштування сповіщень"))
        ]

    # ==========================================
    #               КЛАВІАТУРИ
    # ==========================================

    @staticmethod
    def get_main_keyboard() -> InlineKeyboardMarkup:
        """Головне меню бота."""
        kb = [
            [
                InlineKeyboardButton(text=get_msg('keyboard.show_schedule', "📅 На день"), callback_data="nav_schedule:0"),
                InlineKeyboardButton(text=get_msg('keyboard.show_week', "🗓 На тиждень"), callback_data="nav_week:0")
            ],
            [InlineKeyboardButton(text=get_msg('keyboard.settings', "⚙️ Налаштування"), callback_data="show_settings")],
            [InlineKeyboardButton(text=get_msg('keyboard.change_group', "🔄 Змінити групу"),
                                  callback_data="change_group")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_settings_keyboard(user_data: dict) -> InlineKeyboardMarkup:
        """Меню налаштувань."""
        notify_enabled = user_data.get('notify_10_min', 1)
        offset = user_data.get('reminder_offset', 10)

        if not notify_enabled:
            remind_text = "❌ Нагадування (Вимкнено)"
        elif offset == 60:
            remind_text = "✅ Нагадування (1 год)"
        elif offset == 90:
            remind_text = "✅ Нагадування (1.5 год)"
        else:
            remind_text = f"✅ Нагадування ({offset} хв)"

        kb = [
            [InlineKeyboardButton(text=remind_text, callback_data="settings_reminder")],
            [InlineKeyboardButton(
                text=f"{'✅' if user_data['notify_evening'] else '❌'} {get_msg('keyboard.evening', 'Розклад ввечері')}",
                callback_data="toggle_evening"
            )],
            [InlineKeyboardButton(
                text=f"{'⏸' if user_data['is_paused'] else '▶️'} {get_msg('keyboard.pause', 'Пауза сповіщень')}",
                callback_data="toggle_pause"
            )],
            [InlineKeyboardButton(
                text=f"{'✅' if user_data['notify_schedule_update'] else '❌'} {get_msg('keyboard.schedule_update', 'Сповіщення про оновлення розкладу')}",
                callback_data="toggle_notify_schedule_update"
            )],
            [InlineKeyboardButton(text=get_msg('keyboard.back', "🔙 Назад до меню"), callback_data="back_to_main")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_reminder_settings_keyboard() -> InlineKeyboardMarkup:
        """Підменю вибору часу нагадування."""
        kb = [
            [InlineKeyboardButton(text="Вимкнути повністю ❌", callback_data="set_remind:0")],
            [InlineKeyboardButton(text="10 хв", callback_data="set_remind:10"),
             InlineKeyboardButton(text="15 хв", callback_data="set_remind:15"),
             InlineKeyboardButton(text="30 хв", callback_data="set_remind:30")],
            [InlineKeyboardButton(text="1 година", callback_data="set_remind:60"),
             InlineKeyboardButton(text="1.5 години", callback_data="set_remind:90")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="show_settings")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_schedule_nav_keyboard(offset: int, extra_buttons: list = None) -> InlineKeyboardMarkup:
        """Клавіатура для навігації по днях."""
        kb = [
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"nav_schedule:{offset - 1}"),
                InlineKeyboardButton(text="🔄 Оновити", callback_data=f"nav_schedule:{offset}"),
                InlineKeyboardButton(text="➡️", callback_data=f"nav_schedule:{offset + 1}")
            ],
            [
                InlineKeyboardButton(text=get_msg('keyboard.custom_date', "📅 Обрати дату"), callback_data="ask_custom_date")
            ]
        ]

        if extra_buttons:
            kb.extend(extra_buttons)

        kb.append([InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_main")])
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_week_nav_keyboard(offset: int, extra_buttons: list = None) -> InlineKeyboardMarkup:
        kb = [
            [
                InlineKeyboardButton(text="⬅️ Тиждень", callback_data=f"nav_week:{offset - 1}"),
                InlineKeyboardButton(text="🔄", callback_data=f"nav_week:{offset}"),
                InlineKeyboardButton(text="Тиждень ➡️", callback_data=f"nav_week:{offset + 1}")
            ],
            [InlineKeyboardButton(text="📍 Поточний тиждень", callback_data="nav_week:0")],
            [InlineKeyboardButton(text="📲 Експорт в календар (.ics)", callback_data=f"export_ics:{offset}")]
        ]
        if extra_buttons: kb.extend(extra_buttons)
        kb.append([InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_main")])
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

    async def _get_next_class_text(self, group_name: str) -> str:
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
                    return f"\n\nНаступна пара: ⏰ <b>{start_time_str}</b> - {item['name']}"
            except Exception:
                pass
        return "\n\nНаступна пара: Сьогодні більше пар немає 🎉"

    async def _generate_schedule_ui(self, user_id: int, offset: int) -> tuple[str, InlineKeyboardMarkup]:
        """Генерує текст розкладу та клавіатуру для заданого offset (зміщення в днях)."""
        user = await db.get_user(user_id)
        if not user or not user['group_name']:
            return get_msg("group.need_group", "Спочатку вкажіть групу!"), self.get_main_keyboard()

        target_date = datetime.now() + timedelta(days=offset)
        schedule = await scraper._get_schedule_for_date(user['group_name'], target_date)

        weekdays = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]
        day_name = weekdays[target_date.weekday()]
        date_str = target_date.strftime("%d.%m.%Y")

        if offset == 0:
            relative_day = " (Сьогодні)"
        elif offset == 1:
            relative_day = " (Завтра)"
        elif offset == -1:
            relative_day = " (Вчора)"
        else:
            relative_day = ""

        text = f"📅 <b>Розклад на {day_name}{relative_day}</b>\n🗓 Дата: {date_str}\n🎓 Група: <b>{user['group_name']}</b>\n\n"
        pdf_buttons = []

        if not schedule:
            text += get_msg("schedule.no_classes_today", "🏖 <b>На цей день пар немає</b> (або розклад не знайдено).")
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
                        InlineKeyboardButton(text="👀 Відкрити (Web)", url=item['viewer_url']),
                        InlineKeyboardButton(text="📩 Отримати файлом", callback_data=f"send_pdf:{pdf_key}")
                    ])
                else:
                    text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"

        return text, self.get_schedule_nav_keyboard(offset, pdf_buttons)

    async def _generate_week_schedule_ui(self, user_id: int, offset_weeks: int) -> tuple[str, InlineKeyboardMarkup]:
        """Генерує розклад на весь тиждень (Пн-Нд)."""
        user = await db.get_user(user_id)
        if not user or not user['group_name']:
            return get_msg("group.need_group", "Спочатку вкажіть групу!"), self.get_main_keyboard()

        now = datetime.now()
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)
        sunday = monday + timedelta(days=6)

        text = f"🗓 <b>Розклад на тиждень ({monday.strftime('%d.%m')} - {sunday.strftime('%d.%m')})</b>\n🎓 Група: <b>{user['group_name']}</b>\n\n"

        weekdays = ["Понеділок", "Вівторок", "Середа", "Четвер", "П'ятниця", "Субота", "Неділя"]

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
                    text += f"  ⏰ <b>{item['time']}</b> - {item['name']}\n"
                text += "\n"

        if not has_any_classes:
            text += "🏖 <b>На цей тиждень пар немає.</b>\n\n"

        pdf_buttons = []
        if all_pdfs:
            text += f"<s>{'—' * 25}</s>\n\n"
            for pdf in all_pdfs.values():
                text += f"📄 <b>{pdf['name']}</b>\n"
                pdf_key = _get_pdf_key(pdf['url'])
                pdf_buttons.append([
                    InlineKeyboardButton(text="👀 Відкрити (Web)", url=pdf['viewer_url']),
                    InlineKeyboardButton(text="📩 Отримати", callback_data=f"send_pdf:{pdf_key}")
                ])

        if len(text) > 3900:
            cut_idx = text.rfind('\n', 0, 3900)
            if cut_idx != -1:
                text = text[:cut_idx] + "\n\n<i>... (частину розкладу приховано через ліміт Telegram)</i>"

        return text, self.get_week_nav_keyboard(offset_weeks, pdf_buttons)

    # ==========================================
    #            ОБРОБНИКИ
    # ==========================================

    async def cmd_start(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        if not user or not user['group_name']:
            await db.add_or_update_user(message.from_user.id)
            msg = await message.answer(get_msg("start.greeting_new", "👋 Привіт! Я бот..."))
            await state.set_state(UserState.waiting_for_group)
            await state.update_data(prompt_msg_id=msg.message_id, last_ui_msg_id=msg.message_id)
        else:
            next_class = await self._get_next_class_text(user['group_name'])
            msg = await message.answer(
                get_msg("start.greeting_existing", "👋 Вітаю, {name}!\nТвоя група: <b>{group}</b>{next_class}",
                        name=message.from_user.first_name, group=user['group_name'],
                        next_class=next_class),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard()
            )
            await state.update_data(last_ui_msg_id=msg.message_id)

    async def cmd_settings(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        if not user or not user['group_name']:
            await message.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"))
            return

        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        msg = await message.answer(get_msg("settings.title", "⚙️ <b>Налаштування сповіщень:</b>"),
                                   parse_mode="HTML",
                                   reply_markup=self.get_settings_keyboard(user))
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

        try:
            await message.delete()
        except Exception:
            pass

        data = await state.get_data()
        prompt_msg_id = data.get("prompt_msg_id")

        clean_name = group_name.replace("-", "").replace(" ", "")
        if len(clean_name) < 3 or not any(c.isalpha() for c in clean_name) or not any(c.isdigit() for c in clean_name):
            error_text = "❌ <b>Некоректний формат!</b> Назва групи має містити літери та цифри (наприклад: СТс-21, ЕМ-31).\n\nСпробуйте ще раз:"
            new_msg = await message.answer(error_text, parse_mode="HTML")
            await state.update_data(prompt_msg_id=new_msg.message_id, last_ui_msg_id=new_msg.message_id)
            return

        checking_text = get_msg("group.checking", "⏳ Перевіряю чи існує група <b>{group}</b>...", group=group_name)
        processing_msg = await message.answer(checking_text, parse_mode="HTML")

        is_valid = await scraper.check_group_exists(group_name)

        if is_valid:
            await db.add_or_update_user(message.from_user.id, group_name)
            await processing_msg.edit_text(get_msg("group.saved", "✅ Групу успішно збережено!", group_name=group_name),
                                           parse_mode="HTML",
                                           reply_markup=self.get_main_keyboard())
            await state.set_state(None)
            await state.update_data(last_ui_msg_id=processing_msg.message_id)
        else:
            await processing_msg.edit_text(get_msg("group.not_found", "❌ Групу не знайдено...", group=group_name),
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
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])

        try:
            await callback.message.edit_text(get_msg("schedule.loading", "⏳ Завантажую розклад..."))
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
            await callback.answer("🔄 Оновлено!")
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                await callback.answer("✅ Розклад актуальний.")
            else:
                await callback.answer()

    async def process_nav_week(self, callback: CallbackQuery, state: FSMContext):
        """Обробник розкладу на тиждень."""
        await state.set_state(None)

        user = await db.get_user(callback.from_user.id)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"), show_alert=True)
            return

        offset = int(callback.data.split(":")[1])

        try:
            await callback.message.edit_text(get_msg("schedule.loading", "⏳ Формую розклад..."))
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
            await callback.answer("🔄 Оновлено!")
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                await callback.answer("✅ Актуально.")
            else:
                await callback.answer()

    async def process_export_ics(self, callback: CallbackQuery):
        """Обробник експорту розкладу у файл .ics."""
        user_id = callback.from_user.id
        now = datetime.now()

        last_time = _ics_cooldown.get(user_id)
        if last_time and (now - last_time).total_seconds() < 45:
            await callback.answer("⏳ Зачекайте 45 сек перед наступним експортом", show_alert=True)
            return
        _ics_cooldown[user_id] = now

        await callback.answer("⏳ Генерую файл...", show_alert=False)
        user = await db.get_user(user_id)
        if not user or not user['group_name']: return

        offset_weeks = int(callback.data.split(":")[1])
        monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset_weeks)

        tasks = [scraper._get_schedule_for_date(user['group_name'], monday + timedelta(days=i)) for i in range(7)]
        week_schedules = await asyncio.gather(*tasks)

        schedule_data = {monday + timedelta(days=i): schedule for i, schedule in enumerate(week_schedules) if schedule}
        ics_content = generate_week_ics(user['group_name'], schedule_data)

        if not ics_content.strip() or "BEGIN:VEVENT" not in ics_content:
            await callback.message.answer("❌ На цей тиждень немає пар для експорту.")
            return

        file = BufferedInputFile(ics_content.encode('utf-8'),
                                 filename=f"Schedule_{user['group_name']}_{monday.strftime('%d_%m')}.ics")

        caption_text = (
            "📲 <b>Ваш файл розкладу готовий!</b>\n\n"
            "💡 <i>Як додати в календар:</i>\n"
            "1. Завантажте цей файл.\n"
            "2. Відкрийте його на своєму пристрої.\n"
            "3. Натисніть «Додати всі події» (або аналогічну кнопку)."
        )

        try:
            await callback.message.answer_document(document=file, caption=caption_text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Помилка відправки ICS файлу: {e}")
            await callback.message.answer("❌ Сталася помилка при створенні файлу.")

    async def process_ask_custom_date(self, callback: CallbackQuery, state: FSMContext):
        """Відображає інлайн-календар для вибору дати."""
        now = datetime.now()
        await callback.message.edit_text(get_msg("schedule.ask_date", "📅 Оберіть дату:"), parse_mode="HTML",
                                         reply_markup=get_calendar_keyboard(now.year, now.month))
        await callback.answer()

    async def process_calendar_selection(self, callback: CallbackQuery):
        """Обробляє натискання кнопок на календарі."""
        data = callback.data.split(":")
        action = data[1]

        if action == "ignore":
            await callback.answer()
            return

        user = await db.get_user(callback.from_user.id)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"), show_alert=True)
            return

        # Гортання місяців
        if action in ["prev", "next"]:
            try:
                year, month = int(data[2]), int(data[3])
            except (IndexError, ValueError):
                await callback.answer("❌ Помилка навігації по календарю.", show_alert=True)
                return

            month += -1 if action == "prev" else 1
            if month == 0: month, year = 12, year - 1
            if month == 13: month, year = 1, year + 1
            try:
                await callback.message.edit_reply_markup(reply_markup=get_calendar_keyboard(year, month))
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
            await callback.answer("❌ Помилка вибору дати.", show_alert=True)
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
        await callback.message.edit_text(get_msg("settings.title", "⚙️ Налаштування:"),
                                         parse_mode="HTML",
                                         reply_markup=self.get_settings_keyboard(user))
        await callback.answer()

    async def process_settings_reminder(self, callback: CallbackQuery):
        """Відкриває підменю вибору часу нагадування."""
        await callback.message.edit_text(
            "⏳ <b>Оберіть час нагадування перед початком пари:</b>",
            parse_mode="HTML",
            reply_markup=self.get_reminder_settings_keyboard()
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
        await callback.message.edit_text(
            get_msg("settings.title", "⚙️ <b>Налаштування сповіщень:</b>"),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(updated_user)
        )
        await callback.answer("Налаштування збережено!")

    async def process_change_group(self, callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text(get_msg("group.ask_new", "Введіть нову групу:"))
        await state.set_state(UserState.waiting_for_group)
        await state.update_data(prompt_msg_id=callback.message.message_id, last_ui_msg_id=callback.message.message_id)
        await callback.answer()

    async def process_back_to_main(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        if not user or not user['group_name']:
            await callback.message.edit_text(get_msg("group.need_group", "Спочатку вкажіть групу!"),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard()
            )
            return
        next_class = await self._get_next_class_text(user['group_name'])
        await callback.message.edit_text(
            get_msg("start.main_menu_title",
                    "🏠 Головне меню\nТвоя група: <b>{group}</b>{next_class}",
                    group=user['group_name'],
                    next_class=next_class),
            parse_mode="HTML",
            reply_markup=self.get_main_keyboard())
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
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(updated_user))
        await callback.answer(get_msg("settings.updated", "Налаштування оновлено!"))

    async def process_send_pdf(self, callback: CallbackQuery):
        key = callback.data.split(":", 1)[1]
        url = _pdf_cache.get(key)
        if not url:
            await callback.answer("❌ Посилання застаріло. Оновіть розклад.", show_alert=True)
            return
        await callback.answer("⏳ Завантажую файл...", show_alert=False)
        try:
            await callback.message.answer_document(
                document=url,
                caption="📄 <b>Ваш розклад</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Помилка відправки PDF документу: {e}")
            await callback.message.answer("❌ Не вдалося завантажити файл. Натисніть '👀 Відкрити (Web)'.")

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
                    text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"
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
        offset = user.get('reminder_offset', 10)
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
        self.router.message.register(self.cmd_admin, Command("admin"))

        # FSM (Очікування уводу)
        self.router.message.register(self.process_group_name_fsm, UserState.waiting_for_group)

        # Колбеки (кнопки)
        self.router.callback_query.register(self.process_nav_schedule, F.data.startswith("nav_schedule:"))
        self.router.callback_query.register(self.process_nav_week, F.data.startswith("nav_week:"))
        self.router.callback_query.register(self.process_calendar_selection, F.data.startswith("cal:"))
        self.router.callback_query.register(self.process_ask_custom_date, F.data == "ask_custom_date")
        self.router.callback_query.register(self.process_export_ics, F.data.startswith("export_ics:"))

        self.router.callback_query.register(self.process_show_settings, F.data == "show_settings")

        # Раути для кастомного нагадування
        self.router.callback_query.register(self.process_settings_reminder, F.data == "settings_reminder")
        self.router.callback_query.register(self.process_set_remind, F.data.startswith("set_remind:"))

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

        # Текстові повідомлення (fallback)
        self.router.callback_query.register(self.process_admin_test_reminder, F.data == "admin_test_reminder")
        self.router.callback_query.register(self.process_admin_test_promote, F.data == "admin_test_promote")
        self.router.message.register(self.process_any_text, F.text)
