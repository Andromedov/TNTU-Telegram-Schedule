from html import escape
from urllib.parse import urlparse, urlunparse


def normalize_atutor_url(value: str | None) -> str | None:
    """Переводить офіційні ATutor-посилання на HTTPS."""
    if not value:
        return None
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.hostname == "dl.tntu.edu.ua" and parsed.scheme in {"http", "https"}:
        return urlunparse(parsed._replace(scheme="https"))
    return str(value).strip()


def lesson_plain_text(item: dict) -> str:
    """Формує назву пари без Telegram HTML."""
    subject = str(item.get("subject") or item.get("name") or "")
    if not item.get("subject"):
        return subject

    details = [str(value) for value in (item.get("lesson_type"), item.get("location")) if value]
    result = subject + (f" ({', '.join(details)})" if details else "")
    if item.get("notes"):
        result += f" ❗️{item['notes']}"
    return result


def lesson_html(item: dict) -> str:
    """Формує безпечний HTML; клікабельна лише назва дисципліни."""
    subject = str(item.get("subject") or item.get("name") or "")
    escaped_subject = escape(subject)
    atutor_url = normalize_atutor_url(item.get("atutor_url"))
    if atutor_url:
        escaped_subject = f'<a href="{escape(atutor_url, quote=True)}">{escaped_subject}</a>'

    if not item.get("subject"):
        return escaped_subject

    details = [escape(str(value)) for value in (item.get("lesson_type"), item.get("location")) if value]
    result = escaped_subject + (f" ({', '.join(details)})" if details else "")
    if item.get("notes"):
        result += f" ❗️{escape(str(item['notes']))}"
    return result
