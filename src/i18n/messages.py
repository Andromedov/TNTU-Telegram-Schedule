import json
import logging
import os
from html import escape


def load_messages():
    file_path = os.path.join(os.path.dirname(__file__), 'messages.json')
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.warning("Файл messages.json не знайдено, будуть використовуватись значення за замовчуванням.")
        return {}
    except json.JSONDecodeError as e:
        logging.error(f"Помилка читання messages.json: {e}")
        return {}


messages = load_messages()
SUPPORTED_LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"


class TrustedHtml(str):
    """Розмітка, яку вже сформовано та екрановано всередині застосунку."""


def trusted_html(value: str) -> TrustedHtml:
    return TrustedHtml(value)


def normalize_language(language: str | None) -> str:
    """Нормалізує Telegram/БД locale до підтримуваної мови."""
    if not language:
        return DEFAULT_LANGUAGE
    normalized = language.lower().split("-", 1)[0].split("_", 1)[0]
    return normalized if normalized in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def get_msg(key: str, default: str = None, *, language: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Повертає повідомлення по ключу (підтримує вкладені ключі через крапку, напр. 'bot.greeting')
    та форматує його, якщо передані аргументи.
    Якщо ключ не знайдено, повертає default (якщо передано), або повідомлення про помилку.
    """
    keys = key.split('.')
    language = normalize_language(language)
    msg = messages.get(language, messages)

    for k in keys:
        if isinstance(msg, dict) and k in msg:
            msg = msg[k]
        else:
            msg = None
            break

    if msg is None and language != DEFAULT_LANGUAGE:
        msg = messages.get(DEFAULT_LANGUAGE, {})
        for k in keys:
            if isinstance(msg, dict) and k in msg:
                msg = msg[k]
            else:
                msg = None
                break

    if msg is None:
        msg = default if default is not None else f"Missing message: {key}"

    if kwargs and isinstance(msg, str):
        try:
            return msg.format(**kwargs)
        except KeyError as e:
            logging.error(f"Помилка форматування повідомлення '{key}': бракує аргументу {e}")
            return msg

    return str(msg)


def get_html_msg(key: str, default: str = None, *, language: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """Форматує HTML-шаблон, автоматично екрануючи всі недовірені значення."""
    safe_values = {
        name: str(value) if isinstance(value, TrustedHtml) else escape(str(value), quote=True)
        for name, value in kwargs.items()
    }
    return get_msg(key, default, language=language, **safe_values)
