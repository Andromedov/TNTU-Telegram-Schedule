from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")

REMINDER_LESSON_TYPES = {
    "lecture": "notify_lectures",
    "laboratory": "notify_laboratories",
    "practical": "notify_practicals",
}


def kyiv_now() -> datetime:
    return datetime.now(KYIV_TZ)


def muted_until_tomorrow(now: datetime | None = None) -> str:
    current = now or kyiv_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=KYIV_TZ)
    tomorrow = (current.astimezone(KYIV_TZ) + timedelta(days=1)).date()
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=KYIV_TZ)
    return midnight.astimezone(timezone.utc).isoformat()


def temporary_notifications_are_muted(user_data: Mapping[str, Any], now: datetime | None = None) -> bool:
    muted_until = user_data.get("notifications_muted_until")
    if not muted_until:
        return False

    try:
        until = datetime.fromisoformat(str(muted_until))
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) < until.astimezone(timezone.utc)


def notifications_are_muted(user_data: Mapping[str, Any], now: datetime | None = None) -> bool:
    return (
        bool(user_data.get("is_paused"))
        or temporary_notifications_are_muted(user_data, now)
        or quiet_hours_are_active(user_data, now)
    )


def quiet_hours_are_active(user_data: Mapping[str, Any], now: datetime | None = None) -> bool:
    start = user_data.get("quiet_hours_start")
    end = user_data.get("quiet_hours_end")
    if start is None or end is None:
        return False

    try:
        start_hour, end_hour = int(start), int(end)
    except (TypeError, ValueError):
        return False
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23) or start_hour == end_hour:
        return False

    current = now or kyiv_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=KYIV_TZ)
    hour = current.astimezone(KYIV_TZ).hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def lesson_type_category(value: Any) -> str | None:
    """Зводить назви типів із сайту до категорій налаштувань нагадувань."""
    normalized = str(value or "").strip().casefold()
    if normalized.startswith(("лекц", "lecture")):
        return "lecture"
    if normalized.startswith(("лаб", "laborator")):
        return "laboratory"
    if normalized.startswith(("практ", "practical")):
        return "practical"
    return None


def reminder_enabled_for_lesson(user_data: Mapping[str, Any], lesson: Any) -> bool:
    """Перевіряє фільтр типів; невідомі типи пропускає задля надійності."""
    lesson_type = lesson.get("lesson_type") if isinstance(lesson, Mapping) else None
    category = lesson_type_category(lesson_type)
    if category is None:
        return True
    return bool(user_data.get(REMINDER_LESSON_TYPES[category], 1))
