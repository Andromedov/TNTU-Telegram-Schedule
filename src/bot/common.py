import hashlib
from datetime import datetime

from aiogram.fsm.state import State, StatesGroup

from i18n.messages import normalize_language

pdf_cache: dict[str, str] = {}
ics_cooldown: dict[int, datetime] = {}


def get_pdf_key(url: str) -> str:
    """Генерує короткий ключ для збереження довгих URL у callback_data."""
    key = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
    pdf_cache[key] = url
    if len(pdf_cache) > 500:
        first_key = next(iter(pdf_cache), None)
        if first_key:
            pdf_cache.pop(first_key, None)
    return key


def user_language(user_data, telegram_language: str | None = None) -> str:
    if user_data:
        language = dict(user_data).get("language")
        if language:
            return normalize_language(language)
    return normalize_language(telegram_language)


def normalize_group_input(value: str | None) -> str:
    return str(value or "").upper().strip()


def is_valid_group_input(value: str) -> bool:
    clean_name = value.replace("-", "").replace(" ", "")
    return (
        len(clean_name) >= 3
        and any(char.isalpha() for char in clean_name)
        and any(char.isdigit() for char in clean_name)
    )


class UserState(StatesGroup):
    waiting_for_group = State()
    waiting_for_view_group = State()


class AdminState(StatesGroup):
    waiting_for_user_identifier = State()
