import calendar
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_calendar_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    """Генерує інлайн-календар з додатковими швидкими кнопками."""
    kb = []

    # Назви місяців українською
    month_names = ["", "Січень", "Лютий", "Березень", "Квітень", "Травень", "Червень",
                   "Липень", "Серпень", "Вересень", "Жовтень", "Листопад", "Грудень"]

    # Перший ряд: Кнопки перемикання місяців
    kb.append([
        InlineKeyboardButton(text="⬅️", callback_data=f"cal:prev:{year}:{month}"),
        InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data="cal:ignore"),
        InlineKeyboardButton(text="➡️", callback_data=f"cal:next:{year}:{month}")
    ])

    # Другий ряд: Дні тижня
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]
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

    # Швидкі кнопки (Пропозиція 3 інтегрована прямо сюди)
    kb.append([
        InlineKeyboardButton(text="🎯 Сьогодні", callback_data="cal:today"),
        InlineKeyboardButton(text="⏩ Завтра", callback_data="cal:tomorrow")
    ])

    # Кнопка назад до сьогоднішнього розкладу
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="nav_schedule:0")])

    return InlineKeyboardMarkup(inline_keyboard=kb)