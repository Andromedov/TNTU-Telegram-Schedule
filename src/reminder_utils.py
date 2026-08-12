from datetime import datetime, timedelta, timezone
from typing import Mapping, Any
from zoneinfo import ZoneInfo


KYIV_TZ = ZoneInfo("Europe/Kyiv")


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
    return bool(user_data.get("is_paused")) or temporary_notifications_are_muted(user_data, now)
