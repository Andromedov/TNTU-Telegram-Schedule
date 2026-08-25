from datetime import datetime, timedelta
from html import escape

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from i18n.messages import get_msg
from schedule.formatting import lesson_html, lesson_plain_text


TELEGRAM_MESSAGE_LIMIT = 4096
COPY_TEXT_LIMIT = 256


def _actual_classes(schedule: list) -> list:
    return [item for item in schedule if not item.get("is_pdf")]


def _append_with_limit(html_parts: list[str], plain_parts: list[str], html_line: str, plain_line: str) -> bool:
    candidate = "\n".join([*html_parts, html_line])
    if len(candidate) > 3800:
        return False
    html_parts.append(html_line)
    plain_parts.append(plain_line)
    return True


def build_day_share(
        group_name: str,
        target_date: datetime,
        schedule: list,
        language: str = "uk",
) -> tuple[str, str] | None:
    classes = _actual_classes(schedule)
    if not classes:
        return None

    weekday = get_msg("schedule.weekdays", language=language).split("|")[target_date.weekday()]
    date_value = target_date.strftime("%d.%m.%Y")
    title = get_msg("share.day_title", language=language, day=weekday, date=date_value)
    group_line = get_msg("share.group", language=language, group=group_name)
    html_parts = [f"<b>{escape(title)}</b>", escape(group_line), ""]
    plain_parts = [title, group_line, ""]

    truncated = False
    for item in classes:
        plain_line = f"⏰ {item.get('time', '')} — {lesson_plain_text(item)}"
        html_line = f"⏰ <b>{escape(str(item.get('time', '')))}</b> — {lesson_html(item)}"
        if not _append_with_limit(html_parts, plain_parts, html_line, plain_line):
            truncated = True
            break

    if truncated:
        notice = get_msg("share.truncated", language=language)
        html_parts.extend(["", f"<i>{escape(notice)}</i>"])
        plain_parts.extend(["", notice])

    footer = get_msg("share.footer", language=language)
    html_parts.extend(["", escape(footer)])
    plain_parts.extend(["", footer])
    return "\n".join(html_parts), "\n".join(plain_parts)


def build_week_share(
        group_name: str,
        monday: datetime,
        week_schedules: list[list],
        language: str = "uk",
) -> tuple[str, str] | None:
    if not any(_actual_classes(schedule) for schedule in week_schedules):
        return None

    sunday = monday + timedelta(days=6)
    title = get_msg(
        "share.week_title",
        language=language,
        start=monday.strftime("%d.%m"),
        end=sunday.strftime("%d.%m.%Y"),
    )
    group_line = get_msg("share.group", language=language, group=group_name)
    weekdays = get_msg("schedule.weekdays", language=language).split("|")
    html_parts = [f"<b>{escape(title)}</b>", escape(group_line)]
    plain_parts = [title, group_line]
    truncated = False

    for day_offset, schedule in enumerate(week_schedules):
        classes = _actual_classes(schedule)
        if not classes:
            continue
        current_date = monday + timedelta(days=day_offset)
        day_title = f"{weekdays[day_offset]}, {current_date.strftime('%d.%m')}"
        if not _append_with_limit(html_parts, plain_parts, f"\n<b>{escape(day_title)}</b>", f"\n{day_title}"):
            truncated = True
            break
        for item in classes:
            plain_line = f"⏰ {item.get('time', '')} — {lesson_plain_text(item)}"
            html_line = f"⏰ <b>{escape(str(item.get('time', '')))}</b> — {lesson_html(item)}"
            if not _append_with_limit(html_parts, plain_parts, html_line, plain_line):
                truncated = True
                break
        if truncated:
            break

    if truncated:
        notice = get_msg("share.truncated", language=language)
        html_parts.extend(["", f"<i>{escape(notice)}</i>"])
        plain_parts.extend(["", notice])

    footer = get_msg("share.footer", language=language)
    html_parts.extend(["", escape(footer)])
    plain_parts.extend(["", footer])
    return "\n".join(html_parts), "\n".join(plain_parts)


def get_share_message_keyboard(plain_text: str, language: str = "uk") -> InlineKeyboardMarkup:
    keyboard = []
    if len(plain_text) <= COPY_TEXT_LIMIT:
        keyboard.append([InlineKeyboardButton(
            text=get_msg("share.copy", language=language),
            copy_text=CopyTextButton(text=plain_text),
        )])
    keyboard.append([InlineKeyboardButton(
        text=get_msg("share.close", language=language),
        callback_data="delete_msg",
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
