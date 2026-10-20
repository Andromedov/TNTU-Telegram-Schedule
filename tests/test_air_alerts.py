import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from i18n.messages import get_html_msg, get_msg  # noqa: E402
from infrastructure.air_alerts import (  # noqa: E402
    ALERT_URL,
    DISTRICT_SLUG,
    AirAlertService,
    AlertStatus,
    parse_alert_status,
)
from infrastructure.http_client import HttpRequestError, HttpTextResponse  # noqa: E402
from jobs import air_alerts, notifications  # noqa: E402


def payload(active=False, level=None, **kwargs):
    return {"slug": DISTRICT_SLUG, "type": "district", "active": active, "level": level, **kwargs}


def response(body, status=200):
    return HttpTextResponse(status=status, text=json.dumps(body))


class AlertParsingTests(unittest.TestCase):
    def test_clear_yellow_and_red(self):
        self.assertEqual(AlertStatus.CLEAR, parse_alert_status(payload()))
        self.assertEqual(AlertStatus.YELLOW, parse_alert_status(payload(True, "yellow")))
        self.assertEqual(AlertStatus.RED, parse_alert_status(payload(True, "red")))

    def test_other_districts_oblasts_and_malformed_states_are_rejected(self):
        for body in (
            [],
            None,
            {},
            {"slug": DISTRICT_SLUG, "type": "district", "active": False},
            payload(True, "red", slug="cortkivskii-raion"),
            payload(True, "red", slug="ternopilska-oblast", type="state"),
            payload("false", "red"),
            payload(1, "red"),
            payload(True, "orange"),
            payload(True, None),
            payload(False, "red"),
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_alert_status(body)


class AlertServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_simultaneous_reminders_share_one_request_and_cache_expires(self):
        client = AsyncMock()
        client.request_text.side_effect = [response(payload(True, "yellow")), response(payload(True, "red"))]
        service = AirAlertService(client)
        with patch("infrastructure.air_alerts.monotonic", return_value=100) as clock:
            statuses = await asyncio.gather(*(service.get_status() for _ in range(50)))
            self.assertEqual([AlertStatus.YELLOW] * 50, statuses)
            client.request_text.assert_awaited_once_with("GET", ALERT_URL)
            clock.return_value = 129.9
            self.assertEqual(AlertStatus.YELLOW, await service.get_status())
            clock.return_value = 130
            self.assertEqual(AlertStatus.RED, await service.get_status())
            self.assertEqual(2, client.request_text.await_count)

    async def test_failures_are_unknown_and_cached_instead_of_reusing_expired_status(self):
        for failure in (
            HttpRequestError("timeout"),
            response({}, 429),
            response({}, 503),
            HttpTextResponse(status=200, text="<html>not JSON</html>"),
            response(payload(True, "orange")),
        ):
            for initial in (payload(), payload(True, "red")):
                with self.subTest(failure=failure, initial=initial):
                    client = AsyncMock()
                    client.request_text.side_effect = [response(initial), failure, response(payload(True, "yellow"))]
                    service = AirAlertService(client)
                    with patch("infrastructure.air_alerts.monotonic", return_value=100) as clock:
                        await service.get_status()
                        clock.return_value = 131
                        self.assertEqual(AlertStatus.UNKNOWN, await service.get_status())
                        self.assertEqual(AlertStatus.UNKNOWN, await service.get_status())
                        self.assertEqual(2, client.request_text.await_count)
                        clock.return_value = 162
                        self.assertEqual(AlertStatus.YELLOW, await service.get_status())

    async def test_close_closes_client_and_invalidates_cache(self):
        client = AsyncMock()
        client.request_text.side_effect = [response(payload(True, "red")), response(payload())]
        service = AirAlertService(client)
        with patch("infrastructure.air_alerts.monotonic", return_value=100):
            self.assertEqual(AlertStatus.RED, await service.get_status())
            await service.close()
            self.assertEqual(AlertStatus.CLEAR, await service.get_status())
        client.close.assert_awaited_once()


class AlertReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = {"user_id": 5, "group_name": "СН-21", "notify_10_min": 1, "language": "uk"}
        self.bot = AsyncMock()

    async def test_class_reminders_include_localized_status_below_escaped_subject(self):
        for language in ("uk", "en"):
            self.user["language"] = language
            for status in AlertStatus:
                with (
                    self.subTest(language=language, status=status),
                    patch.object(notifications.db, "get_user", new=AsyncMock(return_value=self.user)),
                    patch.object(air_alerts.air_alert_service, "get_status", new=AsyncMock(return_value=status)),
                ):
                    await notifications.send_class_reminder(self.bot, 5, "A < B & C", "СН-21", 10)
                    text = self.bot.send_message.await_args.args[1]
                    self.assertIn("A &lt; B &amp; C", text)
                    self.assertEqual("HTML", self.bot.send_message.await_args.kwargs["parse_mode"])
                    if status == AlertStatus.CLEAR:
                        self.assertNotIn("Tryvoha.online", text)
                    else:
                        self.assertIn(get_msg(f"air_alerts.{status.value}", language=language), text)
                        self.assertLess(
                            text.index("A &lt; B"), text.index(get_msg("air_alerts.header", language=language))
                        )
                        self.assertIn('href="https://tryvoha.online/"', text)

    async def test_snoozed_reminders_replace_old_status_even_after_language_change(self):
        for old_language in ("uk", "en"):
            for previous in (AlertStatus.YELLOW, AlertStatus.RED, AlertStatus.UNKNOWN):
                with patch.object(air_alerts.air_alert_service, "get_status", new=AsyncMock(return_value=previous)):
                    original = await air_alerts.with_air_alert("<b>Math</b>", old_language)
                for current in AlertStatus:
                    with (
                        self.subTest(previous=previous, current=current, old_language=old_language),
                        patch.object(notifications.db, "get_user", new=AsyncMock(return_value=self.user)),
                        patch.object(air_alerts.air_alert_service, "get_status", new=AsyncMock(return_value=current)),
                    ):
                        await notifications.send_snoozed_reminder(self.bot, 5, original)
                        text = self.bot.send_message.await_args.args[1]
                        if current == AlertStatus.CLEAR:
                            self.assertEqual("<b>Math</b>", text)
                        else:
                            self.assertEqual(1, text.count("Tryvoha.online</a>"))
                            self.assertIn(get_html_msg(f"air_alerts.{current.value}"), text)
                            self.assertIn(get_html_msg("air_alerts.header"), text)

    async def test_snoozed_legacy_reminder_gets_current_status(self):
        with (
            patch.object(notifications.db, "get_user", new=AsyncMock(return_value=self.user)),
            patch.object(air_alerts.air_alert_service, "get_status", new=AsyncMock(return_value=AlertStatus.RED)),
        ):
            await notifications.send_snoozed_reminder(self.bot, 5, "<b>Math</b>")
        self.assertIn(get_msg("air_alerts.red"), self.bot.send_message.await_args.args[1])

    async def test_ineligible_users_do_not_trigger_api_requests(self):
        for changes in ({"is_paused": 1}, {"notify_10_min": 0}, {"group_name": "Other"}):
            with (
                self.subTest(changes=changes),
                patch.object(notifications.db, "get_user", new=AsyncMock(return_value={**self.user, **changes})),
                patch.object(air_alerts.air_alert_service, "get_status", new=AsyncMock()) as get_status,
            ):
                await notifications.send_class_reminder(self.bot, 5, "Math", "СН-21", 10)
                get_status.assert_not_awaited()
        self.bot.send_message.assert_not_awaited()
