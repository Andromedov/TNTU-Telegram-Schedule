import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from schedule.ics import generate_week_ics  # noqa: E402


class IcsGeneratorTests(unittest.TestCase):
    def test_escapes_text_and_folds_utf8_lines(self):
        subject = "Дуже довга назва дисципліни, лабораторна; корпус \\ " * 3
        content = generate_week_ics(
            "СТс-21, тест; \\",
            {datetime(2026, 8, 10): [{"time": "08:00-09:20", "name": subject}]},
        )

        self.assertIn(r"\,", content)
        self.assertIn(r"\;", content)
        self.assertIn(r"\\", content)
        self.assertIn(r"\nЗгенеровано", content.replace("\r\n ", ""))

        lines = content.split("\r\n")
        self.assertTrue(any(line.startswith(" ") for line in lines))
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in lines))

    def test_uses_crlf_and_utc_dates(self):
        content = generate_week_ics(
            "СТс-21",
            {datetime(2026, 1, 12): [{"time": "08:00-09:20", "name": "Математика"}]},
        )

        self.assertIn("DTSTART:20260112T060000Z\r\n", content)
        self.assertNotIn("\n", content.replace("\r\n", ""))
        self.assertTrue(content.endswith("END:VCALENDAR\r\n"))
