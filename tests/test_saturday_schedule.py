import os
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from schedule import service as scraper  # noqa: E402
from schedule.saturday import (  # noqa: E402
    SATURDAY_SCHEDULE_SOURCE_URL,
    get_saturday_source,
    get_saturday_substitution,
    has_saturday_schedule_date,
)

FIXTURE = (ROOT / "tests" / "fixtures" / "schedule_week.html").read_text(encoding="utf-8")


class SaturdayCalendarTests(unittest.TestCase):
    def test_calendar_contains_official_dates_and_weekday_mappings(self):
        first = get_saturday_substitution("СТс-21", date(2026, 9, 26))
        second_week = get_saturday_substitution("КН-31", date(2026, 11, 7))

        self.assertEqual((0, 1), (first.source_weekday, first.source_week))
        self.assertEqual((1, 2), (second_week.source_weekday, second_week.source_week))
        self.assertTrue(has_saturday_schedule_date(date(2026, 12, 5)))
        self.assertFalse(has_saturday_schedule_date(date(2026, 12, 12)))

    def test_group_exceptions_and_audiences_follow_official_document(self):
        self.assertIsNone(get_saturday_substitution("МА-11", date(2026, 9, 26)))
        self.assertIsNone(get_saturday_substitution("MA11", date(2026, 9, 26)))
        self.assertIsNotNone(get_saturday_substitution("КН-51", date(2026, 10, 24)))
        self.assertIsNone(get_saturday_substitution("КН-51", date(2026, 10, 31)))
        self.assertIsNotNone(get_saturday_substitution("МAс-21", date(2026, 12, 5)))
        self.assertIsNone(get_saturday_substitution("СТс-21", date(2026, 12, 5)))


class SaturdayScheduleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_saturday_reuses_configured_weekday_and_week(self):
        group = "СТ-11"
        group_fixture = FIXTURE.replace("Тестова група", group)

        with patch.object(scraper, "fetch_schedule_html", new=AsyncMock(return_value=group_fixture)):
            schedule = await scraper._get_schedule_for_date(group, datetime(2026, 10, 3))

        classes = [item for item in schedule if not item.get("is_pdf")]
        pdfs = [item for item in schedule if item.get("is_pdf")]
        self.assertEqual(["Алгоритми"], [item["subject"] for item in classes])
        self.assertEqual((1, 1), get_saturday_source(classes))
        self.assertIn(SATURDAY_SCHEDULE_SOURCE_URL, [item["url"] for item in pdfs])

    async def test_excluded_group_does_not_receive_saturday_source_document(self):
        group = "МА-11"
        group_fixture = FIXTURE.replace("Тестова група", group)

        with patch.object(scraper, "fetch_schedule_html", new=AsyncMock(return_value=group_fixture)):
            schedule = await scraper._get_schedule_for_date(group, datetime(2026, 9, 26))

        self.assertNotIn(
            SATURDAY_SCHEDULE_SOURCE_URL,
            [item["url"] for item in schedule if item.get("is_pdf")],
        )
