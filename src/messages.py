import json
import os

def load_messages():
    file_path = os.path.join(os.path.dirname(__file__), 'messages.json')
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

messages = load_messages()

def get_msg(key: str, **kwargs) -> str:
    """Повертає повідомлення по ключу та форматує його, якщо передані аргументи."""
    msg = messages.get(key, f"Missing message: {key}")
    if kwargs:
        return msg.format(**kwargs)
    return msg