import hashlib
from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from i18n.messages import get_html_msg, get_msg, trusted_html
from schedule.formatting import lesson_html


def get_dismiss_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_msg("keyboard.dismiss", language=language),
                    callback_data="delete_msg",
                )
            ]
        ]
    )


def get_reminder_keyboard(language: str = "uk") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=get_msg("reminders.snooze_button", language=language), callback_data="snooze_reminder"
                )
            ],
            [
                InlineKeyboardButton(
                    text=get_msg("reminders.mute_today_button", language=language), callback_data="reminder_mute_today"
                )
            ],
            [InlineKeyboardButton(text=get_msg("keyboard.dismiss", language=language), callback_data="delete_msg")],
        ]
    )


def format_reminder_offset(offset: int, language: str) -> str:
    if offset >= 60:
        hours, minutes = divmod(offset, 60)
        if minutes:
            return get_msg("reminders.hours_minutes", language=language, hours=hours, minutes=minutes)
        return get_msg("reminders.hours", language=language, value=hours)
    return get_msg("reminders.minutes", language=language, value=offset)


def reminder_job_id(user_id: int, group_name: str, class_time: datetime, subject_name: str) -> str:
    subject_hash = hashlib.sha256(subject_name.encode("utf-8")).hexdigest()[:12]
    return f"class-reminder:{user_id}:{group_name}:{class_time.strftime('%Y%m%d%H%M')}:{subject_hash}"


def format_schedule_changes(changes: list, language: str) -> str:
    weekdays = get_msg("schedule.weekdays", language=language).split("|")
    lines = [get_msg("schedule.changed", language=language), ""]
    truncated = False
    for change in changes:
        lesson = change["lesson"]
        weekday_index = int(lesson.get("weekday", 0))
        weekday = weekdays[weekday_index] if 0 <= weekday_index < len(weekdays) else str(weekday_index + 1)
        context = get_msg(
            "schedule.change_context",
            language=language,
            weekday=weekday,
            week=lesson.get("week", "?"),
            time=lesson.get("time", "?"),
        )
        key = {
            "added": "schedule.change_added",
            "removed": "schedule.change_removed",
            "changed": "schedule.change_updated",
        }.get(change.get("kind"), "schedule.change_updated")
        block = [
            get_html_msg(key, language=language, subject=trusted_html(lesson_html(lesson))),
            f"<i>{escape(context)}</i>",
        ]
        for field, values in change.get("fields", {}).items():
            label = get_msg(f"schedule.change_fields.{field}", default=field, language=language)
            empty = get_msg("schedule.change_none", language=language)
            block.append(
                "  • "
                + get_msg(
                    "schedule.change_value",
                    language=language,
                    field=escape(label),
                    old=escape(str(values.get("old") or empty)),
                    new=escape(str(values.get("new") or empty)),
                )
            )
        if len("\n".join([*lines, *block, ""])) > 3800:
            truncated = True
            break
        lines.extend([*block, ""])
    if truncated:
        lines.append(f"<i>{escape(get_msg('schedule.change_truncated', language=language))}</i>")
    return "\n".join(lines).rstrip()
