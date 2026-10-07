from html import escape
from urllib.parse import urlparse, urlunparse

from i18n.messages import get_msg


def normalize_atutor_url(value: str | None) -> str | None:
    """Переводить офіційні ATutor-посилання на HTTPS."""
    normalized = normalize_http_url(value)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if parsed.hostname == "dl.tntu.edu.ua" and parsed.scheme in {"http", "https"}:
        return urlunparse(parsed._replace(scheme="https"))
    return normalized


def normalize_http_url(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    parsed = urlparse(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else None


def html_link(label: object, url: str | None) -> str:
    """Створює безпечне HTML-посилання або повертає лише екранований підпис."""
    safe_label = escape(str(label))
    normalized_url = normalize_http_url(url)
    if not normalized_url:
        return safe_label
    return f'<a href="{escape(normalized_url, quote=True)}">{safe_label}</a>'


def lesson_plain_text(item: dict, language: str = "uk") -> str:
    """Формує назву пари без Telegram HTML."""
    subject = str(item.get("subject") or item.get("name") or "")
    if not item.get("subject"):
        return subject

    details = [str(value) for value in (item.get("lesson_type"), item.get("location")) if value]
    if item.get("subgroup"):
        details.append(get_msg("schedule.subgroup_label", language=language, number=item["subgroup"]))
    result = subject + (f" ({', '.join(details)})" if details else "")
    if item.get("notes"):
        result += f" ❗️{item['notes']}"
    return result


def lesson_html(item: dict, language: str = "uk") -> str:
    """Формує безпечний HTML; клікабельна лише назва дисципліни."""
    subject = str(item.get("subject") or item.get("name") or "")
    escaped_subject = escape(subject)
    atutor_url = normalize_atutor_url(item.get("atutor_url"))
    if atutor_url:
        escaped_subject = f'<a href="{escape(atutor_url, quote=True)}">{escaped_subject}</a>'

    if not item.get("subject"):
        return escaped_subject

    details = [escape(str(value)) for value in (item.get("lesson_type"), item.get("location")) if value]
    if item.get("subgroup"):
        details.append(get_msg("schedule.subgroup_label", language=language, number=item["subgroup"]))
    result = escaped_subject + (f" ({', '.join(details)})" if details else "")
    if item.get("notes"):
        result += f" ❗️{escape(str(item['notes']))}"
    return result
