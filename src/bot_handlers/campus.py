from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from campus import building_card
from messages import get_msg


class CampusHandlerMixin:
    async def process_show_campus(self, callback: CallbackQuery, state: FSMContext):
        await state.set_state(None)
        language = await self._get_user_language(
            callback.from_user.id, callback.from_user.language_code
        )
        await callback.message.edit_text(
            get_msg("campus.title", language=language),
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=self.get_campus_keyboard(language),
        )
        await callback.answer()

    async def process_campus_building(self, callback: CallbackQuery):
        language = await self._get_user_language(
            callback.from_user.id, callback.from_user.language_code
        )
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
