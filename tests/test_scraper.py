import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from schedule import service as scraper  # noqa: E402
from infrastructure.http_client import HttpTextResponse  # noqa: E402


FIXTURE = (ROOT / "tests" / "fixtures" / "schedule_week.html").read_text(encoding="utf-8")


class ScheduleParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_uses_shared_http_client(self):
        scraper._html_cache.clear()
        request = AsyncMock(return_value=HttpTextResponse(200, FIXTURE))

        with patch.object(scraper.http_client, "request_text", new=request):
            html = await scraper.fetch_schedule_html("Тестова група")

        self.assertEqual(FIXTURE, html)
        request.assert_awaited_once_with(
            "POST", scraper.TNTU_SCHEDULE_URL,
            params={"p": "uk/schedule"}, data={"group": "Тестова група"},
        )

    def test_builds_real_group_slug_and_rejects_another_groups_table(self):
        group = chr(0x421) + chr(0x422) + "-11"
        self.assertEqual("st11", scraper._transliterate_for_url(group))

        soup = BeautifulSoup(FIXTURE, "html.parser")
        self.assertTrue(scraper._is_valid_schedule_page(soup, "ТЕСТОВА ГРУПА"))
        self.assertFalse(scraper._is_valid_schedule_page(soup, "ІНША ГРУПА"))

    def test_extracts_structured_lesson_details(self):
        soup = BeautifulSoup(FIXTURE, "html.parser")
        table = soup.find("table", id="ScheduleWeek")

        lessons = scraper._table_snapshot(table)

        first_week = next(item for item in lessons if item["week"] == 1)
        second_week = next(item for item in lessons if item["week"] == 2)
        self.assertEqual("Алгоритми", first_week["subject"])
        self.assertEqual("лекція", first_week["lesson_type"])
        self.assertEqual("ATutor", first_week["location"])
        self.assertEqual("https://dl.tntu.edu.ua/bounce.php?course=101", first_week["atutor_url"])
        self.assertIsNone(first_week["building"])
        self.assertEqual("К2", second_week["building"])
        self.assertEqual("63", second_week["room"])
        self.assertEqual("взяти ноутбук", second_week["notes"])

    async def test_uses_semester_start_to_select_alternating_week(self):
        with patch.object(scraper, "fetch_schedule_html", new=AsyncMock(return_value=FIXTURE)):
            first_week = await scraper._get_schedule_for_date("Тестова група", datetime(2026, 9, 1))
            second_week = await scraper._get_schedule_for_date("Тестова група", datetime(2026, 9, 8))

        self.assertEqual(["Алгоритми"], [item["subject"] for item in first_week])
        self.assertEqual(["Бази даних"], [item["subject"] for item in second_week])


class ScheduleDiffTests(unittest.TestCase):
    def test_reports_time_room_type_and_atutor_changes(self):
        old = [{
            "week": 1, "weekday": 0, "time": "8:00-9:20", "subject": "Algorithms",
            "lesson_type": "lecture", "building": "К2", "room": "63", "location": "К2-63",
            "atutor_url": "https://old", "notes": None,
        }]
        new = [{
            "week": 1, "weekday": 0, "time": "9:30-10:50", "subject": "Algorithms",
            "lesson_type": "laboratory", "building": "К1", "room": "703", "location": "К1-703",
            "atutor_url": "https://new", "notes": None,
        }]

        changes = scraper._compare_schedule_snapshots(old, new)

        self.assertEqual(1, len(changes))
        self.assertEqual("changed", changes[0]["kind"])
        self.assertEqual(
            {"time", "lesson_type", "building", "room", "atutor_url"},
            set(changes[0]["fields"]),
        )

    def test_reports_added_removed_and_renamed_lessons(self):
        base = {"week": 1, "weekday": 0, "lesson_type": "lecture"}
        old = [
            {**base, "time": "8:00-9:20", "subject": "Old name"},
            {**base, "time": "9:30-10:50", "subject": "Cancelled"},
        ]
        new = [
            {**base, "time": "8:00-9:20", "subject": "New name"},
            {**base, "time": "11:10-12:30", "subject": "Added"},
        ]

        changes = scraper._compare_schedule_snapshots(old, new)

        self.assertEqual(["changed", "removed", "added"], [item["kind"] for item in changes])
        self.assertIn("subject", changes[0]["fields"])


class ScheduleSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_snapshot_is_silent_and_next_change_is_reported(self):
        changed_fixture = FIXTURE.replace("К2-63", "К2-64")
        with TemporaryDirectory() as directory:
            snapshot_file = str(Path(directory) / "snapshots.json")
            with (
                patch.object(scraper, "SNAPSHOTS_FILE", snapshot_file),
                patch.object(
                    scraper,
                    "fetch_schedule_html",
                    new=AsyncMock(side_effect=[FIXTURE, changed_fixture]),
                ),
            ):
                first = await scraper.get_schedule_changes("Тестова група")
                second = await scraper.get_schedule_changes("Тестова група")

        self.assertEqual([], first)
        self.assertEqual(1, len(second))
        self.assertEqual({"room"}, set(second[0]["fields"]))
        self.assertEqual({"old": "63", "new": "64"}, second[0]["fields"]["room"])
