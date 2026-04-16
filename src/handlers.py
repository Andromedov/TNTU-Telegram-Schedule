from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta

import database as db
import scraper
from messages import get_msg
from config import SENIOR_ID
from calendar_ui import get_calendar_keyboard


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
            [InlineKeyboardButton(text=get_msg('keyboard.show_schedule', "📅 Мій розклад"),
                                  callback_data="nav_schedule:0")],
            [InlineKeyboardButton(text=get_msg('keyboard.settings', "⚙️ Налаштування"), callback_data="show_settings")],
            [InlineKeyboardButton(text=get_msg('keyboard.change_group', "🔄 Змінити групу"),
                                  callback_data="change_group")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_settings_keyboard(user_data: dict) -> InlineKeyboardMarkup:
        """Меню налаштувань."""
        kb = [
            [InlineKeyboardButton(
                text=f"{'✅' if user_data['notify_10_min'] else '❌'} {get_msg('keyboard.10_min', 'Нагадування за 10 хв')}",
                callback_data="toggle_10_min"
            )],
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
    def get_schedule_nav_keyboard(offset: int) -> InlineKeyboardMarkup:
        """Клавіатура для навігації по днях."""
        kb = [
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"nav_schedule:{offset - 1}"),
                InlineKeyboardButton(text="🔄 Оновити", callback_data=f"nav_schedule:{offset}"),
                InlineKeyboardButton(text="➡️", callback_data=f"nav_schedule:{offset + 1}")
            ],
            [
                InlineKeyboardButton(text=get_msg('keyboard.custom_date', "📅 Обрати дату"), callback_data="ask_custom_date")
            ],
            [
                InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_main")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    @staticmethod
    def get_admin_keyboard() -> InlineKeyboardMarkup:
        """Клавіатура адмін-панелі."""
        kb = [
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="🧪 Тест: Вечірній розклад", callback_data="admin_test_evening")],
            [InlineKeyboardButton(text="🧪 Тест: Нагадування (10 хв)", callback_data="admin_test_reminder")],
            [InlineKeyboardButton(text="🧪 Тест: Перевірка змін", callback_data="admin_test_update")],
            [InlineKeyboardButton(text="🔙 Закрити", callback_data="back_to_main")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    # ==========================================
    #            ГЕНЕРАЦІЯ РОЗКЛАДУ
    # ==========================================

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

        relative_day = ""
        if offset == 0:
            relative_day = " (Сьогодні)"
        elif offset == 1:
            relative_day = " (Завтра)"
        elif offset == -1:
            relative_day = " (Вчора)"

        text = f"📅 <b>Розклад на {day_name}{relative_day}</b>\n🗓 Дата: {date_str}\n🎓 Група: <b>{user['group_name']}</b>\n\n"

        if not schedule:
            text += get_msg("schedule.no_classes_today", "🏖 <b>На цей день пар немає</b> (або розклад не знайдено).")
        else:
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf:
                        text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"
                        has_pdf = True
                    text += f"<b>{item['time']}</b> - {item['name']}\n"
                else:
                    text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"

        return text, self.get_schedule_nav_keyboard(offset)

    # ==========================================
    #            ОБРОБНИКИ КОМАНД
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
            msg = await message.answer(get_msg("start.greeting_new",
                                               "👋 Привіт! Я бот, який допоможе тобі слідкувати за розкладом ТНТУ.\n\nБудь ласка, напиши назву своєї групи (наприклад, СТс-21):"))
            await state.set_state(UserState.waiting_for_group)
            await state.update_data(prompt_msg_id=msg.message_id, last_ui_msg_id=msg.message_id)
        else:
            msg = await message.answer(
                get_msg("start.greeting_existing", "👋 Вітаю, {name}!\nТвоя група: <b>{group}</b>",
                        name=message.from_user.first_name, group=user['group_name']),
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

        if not SENIOR_ID or message.from_user.id != SENIOR_ID:
            return

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
            if prompt_msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=prompt_msg_id,
                        text=error_text,
                        parse_mode="HTML"
                    )
                    return
                except Exception as e:
                    if "message is not modified" in str(e).lower(): return
            new_msg = await message.answer(error_text, parse_mode="HTML")
            await state.update_data(prompt_msg_id=new_msg.message_id, last_ui_msg_id=new_msg.message_id)
            return

        checking_text = get_msg("group.checking", "⏳ Перевіряю чи існує група <b>{group}</b>...", group=group_name)

        if prompt_msg_id:
            try:
                processing_msg = await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prompt_msg_id,
                    text=checking_text,
                    parse_mode="HTML"
                )
            except Exception:
                processing_msg = await message.answer(checking_text, parse_mode="HTML")
        else:
            processing_msg = await message.answer(checking_text, parse_mode="HTML")

        is_valid = await scraper.check_group_exists(group_name)

        if is_valid:
            await db.add_or_update_user(message.from_user.id, group_name)
            try:
                await processing_msg.edit_text(
                    get_msg("group.saved", "✅ Групу <b>{group_name}</b> успішно збережено!", group_name=group_name),
                    parse_mode="HTML",
                    reply_markup=self.get_main_keyboard())
            except Exception:
                pass
            await state.set_state(None)
            await state.update_data(last_ui_msg_id=processing_msg.message_id)
        else:
            try:
                await processing_msg.edit_text(get_msg("group.not_found",
                                                       "❌ Групу <b>{group}</b> не знайдено на сайті ТНТУ або розклад для неї відсутній.\n\nПеревір правильність написання (наприклад: СТс-21, КН-31) і спробуй ще раз:",
                                                       group=group_name), parse_mode="HTML")
            except Exception:
                pass
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
            if "message is not modified" in str(e).lower():
                await callback.answer("✅ Розклад актуальний (змін немає).")
            else:
                await callback.answer()

    async def process_ask_custom_date(self, callback: CallbackQuery, state: FSMContext):
        """Відображає інлайн-календар для вибору дати."""
        now = datetime.now()
        kb = get_calendar_keyboard(now.year, now.month)
        text = get_msg("schedule.ask_date",
                       "📅 <b>Оберіть потрібну дату:</b>\nВикористовуйте календар нижче або швидкі кнопки.")

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()

    async def process_calendar_selection(self, callback: CallbackQuery):
        """Обробляє натискання кнопок на календарі."""
        data = callback.data.split(":")
        action = data[1]

        if action == "ignore":
            await callback.answer()
            return

        # Гортання місяців
        if action in ["prev", "next"]:
            year = int(data[2])
            month = int(data[3])

            if action == "prev":
                month -= 1
                if month == 0:
                    month = 12
                    year -= 1
            else:
                month += 1
                if month == 13:
                    month = 1
                    year += 1

            kb = get_calendar_keyboard(year, month)
            try:
                await callback.message.edit_reply_markup(reply_markup=kb)
            except TelegramBadRequest:
                pass
            await callback.answer()
            return

        # Обробка вибору конкретної дати
        now = datetime.now()
        target_date = None

        if action == "today":
            target_date = now
        elif action == "tomorrow":
            target_date = now + timedelta(days=1)
        elif action == "day":
            year = int(data[2])
            month = int(data[3])
            day = int(data[4])
            target_date = datetime(year, month, day)

        if target_date:
            offset = (target_date.date() - now.date()).days

            try:
                await callback.message.edit_text(get_msg("schedule.loading", "⏳ Завантажую розклад..."))
            except TelegramBadRequest:
                pass

            text, kb = await self._generate_schedule_ui(callback.from_user.id, offset)

            try:
                await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True,
                                                 reply_markup=kb)
            except TelegramBadRequest:
                pass

        await callback.answer()

    async def process_show_settings(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(get_msg("settings.title", "⚙️ <b>Налаштування сповіщень:</b>"),
                                         parse_mode="HTML",
                                         reply_markup=self.get_settings_keyboard(user))
        await callback.answer()

    async def process_change_group(self, callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text(get_msg("group.ask_new", "Введіть нову назву групи (наприклад, СТс-21):"))
        await state.set_state(UserState.waiting_for_group)
        await state.update_data(prompt_msg_id=callback.message.message_id, last_ui_msg_id=callback.message.message_id)
        await callback.answer()

    async def process_back_to_main(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(
            get_msg("start.main_menu_title", "🏠 Головне меню\nТвоя група: <b>{group}</b>", group=user['group_name']),
            parse_mode="HTML",
            reply_markup=self.get_main_keyboard())
        await callback.answer()

    async def process_toggles(self, callback: CallbackQuery):
        user_id = callback.from_user.id
        user = await db.get_user(user_id)

        action = callback.data
        if action == "toggle_10_min":
            await db.update_setting(user_id, 'notify_10_min', 0 if user['notify_10_min'] else 1)
        elif action == "toggle_evening":
            await db.update_setting(user_id, 'notify_evening', 0 if user['notify_evening'] else 1)
        elif action == "toggle_pause":
            await db.update_setting(user_id, 'is_paused', 0 if user['is_paused'] else 1)
        elif action == "toggle_notify_schedule_update":
            await db.update_setting(user_id, 'notify_schedule_update', 0 if user['notify_schedule_update'] else 1)

        updated_user = await db.get_user(user_id)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(updated_user))
        await callback.answer(get_msg("settings.updated", "Налаштування оновлено!"))

    async def process_delete_msg(self, callback: CallbackQuery):
        """Обробник для кнопки 'Прочитано', який просто видаляє повідомлення із чату."""
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()

    # ==========================================
    #          ОБРОБНИКИ АДМІН-ПАНЕЛІ
    # ==========================================

    async def process_admin_stats(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        stats = await db.get_statistics()

        text = "📊 <b>Статистика бота:</b>\n\n"
        text += f"👥 Всього користувачів: <b>{stats['total']}</b>\n"
        text += f"🟢 Активних (не на паузі): <b>{stats['active']}</b>\n\n"
        text += "🏆 <b>Топ 5 груп:</b>\n"

        for idx, group in enumerate(stats['top_groups'], 1):
            text += f"{idx}. {group['group_name']} ({group['count']} студ.)\n"

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=self.get_admin_keyboard())
        await callback.answer()

    async def process_admin_test_evening(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        user = await db.get_user(SENIOR_ID)

        if not user or not user['group_name']:
            await callback.answer("У вас не вказана група для тесту!", show_alert=True)
            return

        schedule = await scraper.parse_schedule_for_tomorrow(user['group_name'])
        if schedule:
            text = "🧪 <i>ТЕСТ Вечірнього розкладу</i>\n\n"
            has_pdf = False
            for item in schedule:
                if item.get('is_pdf'):
                    if not has_pdf: text += "\n" + f"<s>{'—' * 25}</s>" + "\n\n"; has_pdf = True
                    text += f"📄 {item['name']}\n"
                else:
                    text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"

            await callback.message.answer(text,
                                          parse_mode="HTML",
                                          disable_web_page_preview=True,
                                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Прочитано", callback_data="delete_msg")]]))
            await callback.answer("Успішно відправлено.")
        else:
            await callback.answer("На завтра пар немає (або помилка парсингу).", show_alert=True)

    async def process_admin_test_reminder(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return

        text = get_msg("reminders.10_min", "⏳ За 10 хвилин почнеться пара:\n<b>{subject_name}</b>",
                       subject_name="🧪 Тестовий Предмет (Лекція)")
        await callback.message.answer(f"🧪 <i>ТЕСТ Нагадування</i>\n\n{text}", parse_mode="HTML",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="✅ Прочитано", callback_data="delete_msg")]]))
        await callback.answer("Успішно відправлено.")

    async def process_admin_test_update(self, callback: CallbackQuery):
        if not SENIOR_ID or callback.from_user.id != SENIOR_ID: return
        user = await db.get_user(SENIOR_ID)

        if not user or not user['group_name']:
            await callback.answer("У вас не вказана група для тесту!", show_alert=True)
            return

        has_changes = await scraper.check_schedule_changes(user['group_name'])

        if has_changes:
            text = "🧪 <i>ТЕСТ Перевірки змін</i>\n\n⚠️ <b>Увага!</b> Розклад для вашої групи був змінений на сайті ТНТУ!"
        else:
            text = "🧪 <i>ТЕСТ Перевірки змін</i>\n\n✅ Змін на сайті не виявлено. Розклад актуальний."

        await callback.message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Прочитано", callback_data="delete_msg")]]))
        await callback.answer("Перевірка завершена.")

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
        self.router.callback_query.register(self.process_calendar_selection, F.data.startswith("cal:"))
        self.router.callback_query.register(self.process_ask_custom_date, F.data == "ask_custom_date")
        self.router.callback_query.register(self.process_show_settings, F.data == "show_settings")
        self.router.callback_query.register(self.process_change_group, F.data == "change_group")
        self.router.callback_query.register(self.process_back_to_main, F.data == "back_to_main")
        self.router.callback_query.register(self.process_toggles, F.data.startswith("toggle_"))

        # Реєстрація кнопки видалення повідомлення
        self.router.callback_query.register(self.process_delete_msg, F.data == "delete_msg")

        # Адмінські колбеки
        self.router.callback_query.register(self.process_admin_stats, F.data == "admin_stats")
        self.router.callback_query.register(self.process_admin_test_evening, F.data == "admin_test_evening")
        self.router.callback_query.register(self.process_admin_test_reminder, F.data == "admin_test_reminder")
        self.router.callback_query.register(self.process_admin_test_update, F.data == "admin_test_update")

        # Текстові повідомлення (fallback)
        self.router.message.register(self.process_any_text, F.text)