from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
import database as db
from messages import get_msg

router = Router()


def get_settings_keyboard(user_data) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(
            text=f"{'✅' if user_data['notify_10_min'] else '❌'} {get_msg('kb_10_min')}",
            callback_data="toggle_10_min"
        )],
        [InlineKeyboardButton(
            text=f"{'✅' if user_data['notify_evening'] else '❌'} {get_msg('kb_evening')}",
            callback_data="toggle_evening"
        )],
        [InlineKeyboardButton(
            text=f"{'⏸' if user_data['is_paused'] else '▶️'} {get_msg('kb_pause')}",
            callback_data="toggle_pause"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("start"))
async def cmd_start(message: Message):
    await db.add_or_update_user(message.from_user.id)
    await message.answer(get_msg("start_greeting"))


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer(get_msg("need_start"))
        return

    await message.answer(get_msg("settings_title"), reply_markup=get_settings_keyboard(user))


@router.callback_query(F.data.startswith("toggle_"))
async def process_toggle(callback: CallbackQuery):
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

    # Оновлюємо дані і клавіатуру
    updated_user = await db.get_user(user_id)
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(updated_user))
    await callback.answer(get_msg("settings_updated"))


@router.message(F.text)
async def process_group_name(message: Message):
    group_name = message.text.upper().strip()
    await db.add_or_update_user(message.from_user.id, group_name)
    await message.answer(get_msg("group_saved", group_name=group_name))