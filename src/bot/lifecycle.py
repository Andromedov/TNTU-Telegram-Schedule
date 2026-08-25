from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from infrastructure import database as db
from schedule import service as scraper
from bot.common import UserState
from config import SENIOR_ID
from i18n.messages import get_html_msg, get_msg, trusted_html


class LifecycleHandlerMixin:
    async def cmd_start(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)

        if not user or not user["group_name"]:
            await db.add_or_update_user(message.from_user.id, language=language)
            msg = await message.answer(get_msg("start.greeting_new", language=language))
            await state.set_state(UserState.waiting_for_group)
            await state.update_data(prompt_msg_id=msg.message_id, last_ui_msg_id=msg.message_id)
        else:
            next_class = await self._get_next_class_text(user["group_name"], language)
            msg = await message.answer(
                get_html_msg(
                    "start.greeting_existing", language=language,
                    name=message.from_user.first_name, group=user["group_name"],
                    next_class=trusted_html(next_class),
                ),
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=self.get_main_keyboard(language),
            )
            await state.update_data(last_ui_msg_id=msg.message_id)

    async def cmd_settings(self, message: Message, state: FSMContext):
        try:
            await message.delete()
        except Exception:
            pass

        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)
        if not user or not user["group_name"]:
            await message.answer(get_msg("group.need_group", language=language))
            return

        await self._cleanup_old_ui(message, state)
        await state.set_state(None)
        msg = await message.answer(
            get_msg("settings.title", language=language),
            parse_mode="HTML",
            reply_markup=self.get_settings_keyboard(user),
        )
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
        try:
            await message.delete()
        except Exception:
            pass
        if not SENIOR_ID or message.from_user.id != SENIOR_ID:
            return
        await self._cleanup_old_ui(message, state)
        await state.set_state(None)
        msg = await message.answer(
            "👑 <b>Адмін Панель</b>\nОберіть дію нижче:",
            parse_mode="HTML",
            reply_markup=self.get_admin_keyboard(),
        )
        await state.update_data(last_ui_msg_id=msg.message_id)

    async def process_group_name_fsm(self, message: Message, state: FSMContext):
        group_name = message.text.upper().strip()
        user = await db.get_user(message.from_user.id)
        language = self._user_language(user, message.from_user.language_code)

        try:
            await message.delete()
        except Exception:
            pass

        clean_name = group_name.replace("-", "").replace(" ", "")
        if (len(clean_name) < 3 or not any(char.isalpha() for char in clean_name)
                or not any(char.isdigit() for char in clean_name)):
            new_msg = await message.answer(get_msg("group.invalid", language=language), parse_mode="HTML")
            await state.update_data(
                prompt_msg_id=new_msg.message_id, last_ui_msg_id=new_msg.message_id
            )
            return

        processing_msg = await message.answer(
            get_html_msg("group.checking", language=language, group=group_name),
            parse_mode="HTML",
        )
        is_valid = await scraper.check_group_exists(group_name)

        if is_valid:
            await db.add_or_update_user(message.from_user.id, group_name, language)
            await processing_msg.edit_text(
                get_html_msg("group.saved", language=language, group_name=group_name),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard(language),
            )
            await state.set_state(None)
        else:
            await processing_msg.edit_text(
                get_html_msg("group.not_found", language=language, group=group_name),
                parse_mode="HTML",
            )
        await state.update_data(last_ui_msg_id=processing_msg.message_id)

    async def process_any_text(self, message: Message):
        try:
            await message.delete()
        except Exception:
            pass

    async def process_change_group(self, callback: CallbackQuery, state: FSMContext):
        language = await self._get_user_language(
            callback.from_user.id, callback.from_user.language_code
        )
        await callback.message.edit_text(get_msg("group.ask_new", language=language))
        await state.set_state(UserState.waiting_for_group)
        await state.update_data(
            prompt_msg_id=callback.message.message_id,
            last_ui_msg_id=callback.message.message_id,
        )
        await callback.answer()

    async def process_back_to_main(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        user = await db.get_user(callback.from_user.id)
        language = self._user_language(user, callback.from_user.language_code)
        if not user or not user["group_name"]:
            await callback.message.edit_text(
                get_msg("group.need_group", language=language),
                parse_mode="HTML",
                reply_markup=self.get_main_keyboard(language),
            )
            return
        next_class = await self._get_next_class_text(user["group_name"], language)
        await callback.message.edit_text(
            get_html_msg(
                "start.main_menu_title", language=language,
                group=user["group_name"], next_class=trusted_html(next_class),
            ),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_main_keyboard(language),
        )
        await callback.answer()
