from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import database as db
import scraper
from messages import get_msg

router = Router()


class UserState(StatesGroup):
    waiting_for_group = State()


def get_main_keyboard() -> InlineKeyboardMarkup:
    """Головне меню бота."""
    kb = [
        [InlineKeyboardButton(text=get_msg('kb_show_today', "📅 Розклад на сьогодні"), callback_data="show_today")],
        [InlineKeyboardButton(text=get_msg('kb_settings', "⚙️ Налаштування"), callback_data="show_settings")],
        [InlineKeyboardButton(text=get_msg('kb_change_group', "🔄 Змінити групу"), callback_data="change_group")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


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
        )],
        [InlineKeyboardButton(text=get_msg('kb_back', "🔙 Назад до меню"), callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user = await db.get_user(message.from_user.id)

    if not user or not user['group_name']:
        await db.add_or_update_user(message.from_user.id)
        await message.answer(get_msg("start_greeting_new",
                                     "👋 Привіт! Я бот, який допоможе тобі слідкувати за розкладом ТНТУ.\n\nБудь ласка, напиши назву своєї групи (наприклад, СТс-21):"))
        await state.set_state(UserState.waiting_for_group)
    else:
        await message.answer(get_msg("start_greeting_existing", "👋 Вітаю, {name}!\nТвоя група: <b>{group}</b>",
                                     name=message.from_user.first_name, group=user['group_name']),
                             parse_mode="HTML",
                             reply_markup=get_main_keyboard())


@router.message(UserState.waiting_for_group)
async def process_group_name_fsm(message: Message, state: FSMContext):
    group_name = message.text.upper().strip()

    processing_msg = await message.answer(
        get_msg("checking_group", "⏳ Перевіряю чи існує група <b>{group}</b>...", group=group_name), parse_mode="HTML")

    is_valid = await scraper.check_group_exists(group_name)

    if is_valid:
        await db.add_or_update_user(message.from_user.id, group_name)
        await processing_msg.edit_text(
            get_msg("group_saved", "✅ Групу <b>{group_name}</b> успішно збережено!", group_name=group_name),
            parse_mode="HTML",
            reply_markup=get_main_keyboard())
        await state.clear()
    else:
        await processing_msg.edit_text(get_msg("group_not_found",
                                               "❌ Групу <b>{group}</b> не знайдено на сайті ТНТУ або розклад для неї відсутній.\n\nПеревір правильність написання (наприклад: СТс-21, КН-31) і спробуй ще раз:",
                                               group=group_name), parse_mode="HTML")


@router.message(F.text)
async def process_any_text(message: Message):
    """Обробник для будь-якого тексту, якщо користувач не в стані зміни групи."""
    await message.answer(get_msg("use_menu",
                                 "Для взаємодії використовуйте меню нижче. Якщо хочете змінити групу, натисніть 'Змінити групу'."),
                         reply_markup=get_main_keyboard())


@router.callback_query(F.data == "show_today")
async def process_show_today(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    if not user or not user['group_name']:
        await callback.answer(get_msg("group.need_group", "Спочатку вкажіть групу!"), show_alert=True)
        return

    await callback.message.edit_text(get_msg("schedule.loading", "⏳ Завантажую розклад на сьогодні..."))

    schedule = await scraper.parse_schedule_for_today(user['group_name'])

    if not schedule:
        text = get_msg("schedule.no_classes_today", "🏖 <b>На сьогодні пар немає</b> (або розклад не знайдено).")
    else:
        text = get_msg("schedule.today_title", "📅 <b>Розклад на сьогодні ({group}):</b>\n\n", group=user['group_name'])
        has_pdf = False
        for item in schedule:
            if item.get('is_pdf'):
                if not has_pdf:
                    text += "\n" + f"<s>{"—" * 25}</s>" + "\n\n"
                    has_pdf = True
                text += f"<b>{item['time']}</b> - {item['name']}\n"
            else:
                text += f"⏰ <b>{item['time']}</b> - {item['name']}\n"

    await callback.message.edit_text(text, parse_mode="HTML", disable_web_page_preview=True,
                                     reply_markup=get_main_keyboard())
    await callback.answer()


@router.callback_query(F.data == "show_settings")
async def process_show_settings(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text(get_msg("settings_title", "⚙️ <b>Налаштування сповіщень:</b>"), parse_mode="HTML",
                                     reply_markup=get_settings_keyboard(user))
    await callback.answer()


@router.callback_query(F.data == "change_group")
async def process_change_group(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(get_msg("ask_new_group", "Введіть нову назву групи (наприклад, СТс-21):"))
    await state.set_state(UserState.waiting_for_group)
    await callback.answer()


@router.callback_query(F.data == "back_to_main")
async def process_back_to_main(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text(
        get_msg("main_menu_title", "🏠 Головне меню\nТвоя група: <b>{group}</b>", group=user['group_name']),
        parse_mode="HTML",
        reply_markup=get_main_keyboard())
    await callback.answer()


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

    updated_user = await db.get_user(user_id)
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(updated_user))
    await callback.answer(get_msg("settings_updated", "Налаштування оновлено!"))