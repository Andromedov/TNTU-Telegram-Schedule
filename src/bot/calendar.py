import calendar
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from i18n.messages import get_msg


def get_calendar_keyboard(year: int, month: int, language: str = "uk") -> InlineKeyboardMarkup:
    """Генерує інлайн-календар з додатковими швидкими кнопками."""
    kb = []

    month_names = [""] + get_msg("calendar.months", language=language).split("|")

    # Перший ряд: Кнопки перемикання місяців
    kb.append([
        InlineKeyboardButton(text="⬅️", callback_data=f"cal:prev:{year}:{month}"),
        InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data="cal:ignore"),
        InlineKeyboardButton(text="➡️", callback_data=f"cal:next:{year}:{month}")
    ])

    # Другий ряд: Дні тижня
    weekdays = get_msg("calendar.weekdays_short", language=language).split("|")
    kb.append([InlineKeyboardButton(text=day, callback_data="cal:ignore") for day in weekdays])

    # Наступні ряди: Дні місяця
    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="cal:ignore"))
            else:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal:day:{year}:{month}:{day}"))
        kb.append(row)

    kb.append([
        InlineKeyboardButton(text=get_msg("calendar.today", language=language), callback_data="cal:today"),
        InlineKeyboardButton(text=get_msg("calendar.tomorrow", language=language), callback_data="cal:tomorrow")
    ])

    # Кнопка назад до сьогоднішнього розкладу
    kb.append([InlineKeyboardButton(text=get_msg("calendar.back", language=language), callback_data="nav_schedule:0")])

    return InlineKeyboardMarkup(inline_keyboard=kb)
