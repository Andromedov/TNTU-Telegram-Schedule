import hashlib
from datetime import datetime

from aiogram.fsm.state import State, StatesGroup

from messages import normalize_language


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


class UserState(StatesGroup):
    waiting_for_group = State()
