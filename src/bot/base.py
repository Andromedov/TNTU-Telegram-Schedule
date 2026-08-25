from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.common import user_language
from i18n.messages import get_msg
from infrastructure import database as db


class HandlerBaseMixin:
    @staticmethod
    def _user_language(user_data, telegram_language: str | None = None) -> str:
        return user_language(user_data, telegram_language)

    async def _get_user_language(self, user_id: int, telegram_language: str | None = None) -> str:
        return self._user_language(await db.get_user(user_id), telegram_language)

    async def _cleanup_old_ui(self, message: Message, state: FSMContext):
        """Закриває попереднє повідомлення меню, щоб запобігти спаму."""
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
                    reply_markup=None,
                )
            except Exception:
                pass
