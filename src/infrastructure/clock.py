from datetime import datetime
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")


def kyiv_now() -> datetime:
    return datetime.now(KYIV_TZ)
