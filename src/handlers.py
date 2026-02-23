from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta

import database as db
import scraper
from messages import get_msg


class UserState(StatesGroup):
    waiting_for_group = State()


class ScheduleBotHandlers:
    def __init__(self, router: Router):
        self.router = router
        self._register_handlers()

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
            # Запускаємо навігацію з відступом 0 (сьогодні)
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
                InlineKeyboardButton(text="🏠 Меню", callback_data="back_to_main"),
                InlineKeyboardButton(text="➡️", callback_data=f"nav_schedule:{offset + 1}")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=kb)

    # ==========================================
    #            ОБРОБНИКИ КОМАНД
    # ==========================================

    async def cmd_start(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)

        if not user or not user['group_name']:
            await db.add_or_update_user(message.from_user.id)
            msg = await message.answer(get_msg("start.greeting_new",
                                               "👋 Привіт! Я бот, який допоможе тобі слідкувати за розкладом ТНТУ.\n\nБудь ласка, напиши назву своєї групи (наприклад, СТс-21):"))
            await state.set_state(UserState.waiting_for_group)
            await state.update_data(prompt_msg_id=msg.message_id)
        else:
            await message.answer(get_msg("start.greeting_existing", "👋 Вітаю, {name}!\nТвоя група: <b>{group}</b>",
                                         name=message.from_user.first_name, group=user['group_name']),
                                 parse_mode="HTML",
                                 reply_markup=self.get_main_keyboard())

    async def cmd_settings(self, message: Message):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        if not user or not user['group_name']:
            await message.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"))
            return
        await message.answer(get_msg("settings.title", "⚙️ <b>Налаштування сповіщень:</b>"),
                             parse_mode="HTML",
                             reply_markup=self.get_settings_keyboard(user))

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
            await processing_msg.edit_text(
                get_msg("group.saved", "✅ Групу <b>{group_name}</b> успішно збережено!", group_name=group_name),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard())
            await state.clear()
        else:
            await processing_msg.edit_text(get_msg("group.not_found",
                                                   "❌ Групу <b>{group}</b> не знайдено на сайті ТНТУ або розклад для неї відсутній.\n\nПеревір правильність написання (наприклад: СТс-21, КН-31) і спробуй ще раз:",
                                                   group=group_name), parse_mode="HTML")

    async def process_any_text(self, message: Message):
        """Обробник для будь-якого тексту, якщо користувач не в стані зміни групи."""
        try:
            await message.delete()
        except Exception:
            pass

        await message.answer(get_msg("start.use_menu",
                                     "Для взаємодії використовуйте меню нижче. Якщо хочете змінити групу, натисніть 'Змінити групу'."),
                             reply_markup=self.get_main_keyboard())

    # ==========================================
    #            ОБРОБНИКИ КОЛБЕКІВ
    # ==========================================

    async def process_nav_schedule(self, callback: CallbackQuery):
        """Обробник динамічного графіка розкладу з гортанням по днях."""
        user = await db.get_user(callback.from_user.id)
        if not user or not user['group_name']:
            await callback.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"), show_alert=True)
            return

        # Отримуємо зміщення у днях (offset)
        offset = int(callback.data.split(":")[1])
        target_date = datetime.now() + timedelta(days=offset)

        await callback.message.edit_text(get_msg("schedule.loading", "⏳ Завантажую розклад..."))

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

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_schedule_nav_keyboard(offset)
        )
        await callback.answer()

    async def process_show_settings(self, callback: CallbackQuery):
        user = await db.get_user(callback.from_user.id)
        await callback.message.edit_text(get_msg("settings.title", "⚙️ <b>Налаштування сповіщень:</b>"),
                                         parse_mode="HTML",
                                         reply_markup=self.get_settings_keyboard(user))
        await callback.answer()

    async def process_change_group(self, callback: CallbackQuery, state: FSMContext):
        await callback.message.edit_text(get_msg("group.ask_new", "Введіть нову назву групи (наприклад, СТс-21):"))
        await state.set_state(UserState.waiting_for_group)
        await state.update_data(prompt_msg_id=callback.message.message_id)
        await callback.answer()

    async def process_back_to_main(self, callback: CallbackQuery):
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
            new_val = 0 if user['notify_10_min'] else 1
            await db.update_setting(user_id, 'notify_10_min', new_val)
        elif action == "toggle_evening":
            new_val = 0 if user['notify_evening'] else 1
            await db.update_setting(user_id, 'notify_evening', new_val)
        elif action == "toggle_pause":
            new_val = 0 if user['is_paused'] else 1
            await db.update_setting(user_id, 'is_paused', new_val)
        elif action == "toggle_notify_schedule_update":
            new_val = 0 if user['notify_schedule_update'] else 1
            await db.update_setting(user_id, 'notify_schedule_update', new_val)

        updated_user = await db.get_user(user_id)
        await callback.message.edit_reply_markup(reply_markup=self.get_settings_keyboard(updated_user))
        await callback.answer(get_msg("settings.updated", "Налаштування оновлено!"))

    # ==========================================
    #            РЕЄСТРАЦІЯ РОУТІВ
    # ==========================================

    def _register_handlers(self):
        """Метод, який зв'язує функції класу з роутером aiogram."""

        # Команди
        self.router.message.register(self.cmd_start, Command("start"))
        self.router.message.register(self.cmd_settings, Command("settings"))

        # FSM (Очікування уводу)
        self.router.message.register(self.process_group_name_fsm, UserState.waiting_for_group)

        # Колбеки (кнопки)
        self.router.callback_query.register(self.process_nav_schedule, F.data.startswith("nav_schedule:"))
        self.router.callback_query.register(self.process_show_settings, F.data == "show_settings")
        self.router.callback_query.register(self.process_change_group, F.data == "change_group")
        self.router.callback_query.register(self.process_back_to_main, F.data == "back_to_main")
        self.router.callback_query.register(self.process_toggles, F.data.startswith("toggle_"))

        # Текстові повідомлення (fallback)
        self.router.message.register(self.process_any_text, F.text)