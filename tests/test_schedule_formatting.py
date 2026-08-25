import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("BOT_TOKEN", "test-token")

from schedule_formatting import lesson_html, lesson_plain_text, normalize_atutor_url  # noqa: E402


class ScheduleFormattingTests(unittest.TestCase):
    def test_links_only_subject_and_uses_https_for_atutor(self):
        lesson = {
            "subject": "A < B",
            "lesson_type": "лекція",
            "location": "К2-63 & 64",
            "notes": "важливо > всі",
            "atutor_url": "http://dl.tntu.edu.ua/bounce.php?course=101&x=1",
        }

        result = lesson_html(lesson)

        self.assertEqual(
            '<a href="https://dl.tntu.edu.ua/bounce.php?course=101&amp;x=1">A &lt; B</a> '
            '(лекція, К2-63 &amp; 64) ❗️важливо &gt; всі',
            result,
        )
        self.assertEqual("A < B (лекція, К2-63 & 64) ❗️важливо > всі", lesson_plain_text(lesson))

    def test_rejects_non_http_links_and_escapes_legacy_name(self):
        self.assertIsNone(normalize_atutor_url("javascript:alert(1)"))
        self.assertEqual("Legacy &amp; lesson", lesson_html({
            "name": "Legacy & lesson",
            "atutor_url": "javascript:alert(1)",
        }))
