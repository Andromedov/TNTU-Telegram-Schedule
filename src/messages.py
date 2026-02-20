import json
import os
import logging


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


def get_msg(key: str, default: str = None, **kwargs) -> str:
    """
    Повертає повідомлення по ключу (підтримує вкладені ключі через крапку, напр. 'bot.greeting')
    та форматує його, якщо передані аргументи.
    Якщо ключ не знайдено, повертає default (якщо передано), або повідомлення про помилку.
    """
    keys = key.split('.')
    msg = messages

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