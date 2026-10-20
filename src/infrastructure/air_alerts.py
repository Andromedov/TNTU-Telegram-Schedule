import asyncio
import json
import logging
from enum import StrEnum
from time import monotonic

from infrastructure.http_client import HttpClient, HttpRequestError

DISTRICT_SLUG = "ternopilskii-raion"
ALERT_URL = f"https://tryvoha.online/api/v1/alerts/{DISTRICT_SLUG}"
CACHE_SECONDS = 30


class AlertStatus(StrEnum):
    CLEAR = "clear"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


def parse_alert_status(payload: object) -> AlertStatus:
    if not isinstance(payload, dict):
        raise ValueError("Expected a region object")
    if payload.get("slug") != DISTRICT_SLUG or payload.get("type") != "district":
        raise ValueError("Expected Ternopil district")
    if "level" not in payload:
        raise ValueError("Missing alert level")
    active = payload.get("active")
    level = payload.get("level")
    if active is False and level is None:
        return AlertStatus.CLEAR
    if active is True and level in ("yellow", "red"):
        return AlertStatus(level)
    raise ValueError("Unsupported or inconsistent alert state")


class AirAlertService:
    def __init__(self, client: HttpClient):
        self._client = client
        self._lock = asyncio.Lock()
        self._status = AlertStatus.UNKNOWN
        self._expires_at = 0.0

    async def get_status(self) -> AlertStatus:
        async with self._lock:
            if monotonic() < self._expires_at:
                return self._status
            try:
                response = await self._client.request_text("GET", ALERT_URL)
                if response.status != 200:
                    raise ValueError(f"Alert API returned HTTP {response.status}")
                status = parse_alert_status(json.loads(response.text))
            except (HttpRequestError, ValueError) as error:
                logging.warning("Could not check Ternopil district air alert: %s", error)
                status = AlertStatus.UNKNOWN
            # Cache failures too, so a provider outage does not cause a request per user.
            self._status = status
            self._expires_at = monotonic() + CACHE_SECONDS
            return status

    async def close(self) -> None:
        await self._client.close()
        self._status = AlertStatus.UNKNOWN
        self._expires_at = 0.0


air_alert_service = AirAlertService(HttpClient(connect_timeout=2, read_timeout=3, total_timeout=4, max_attempts=1))
