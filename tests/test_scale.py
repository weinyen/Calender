import tempfile
import time
import unittest
from pathlib import Path

from github_calendar.generate import generate


EVENT_COUNT = 500
GROUP_COUNT = 20


def scale_issue(number: int) -> dict:
    first_group = number % GROUP_COUNT
    second_group = (number + 1) % GROUP_COUNT
    body = """### 開始

2026-09-01 10:00

### 終了

2026-09-01 11:00

### タイムゾーン

Asia/Tokyo

### 終日予定

- [ ] 終日予定として登録する

### 場所

個人用テスト

### 関連URL

_No response_

### 説明

個人利用の想定上限を確認する性能テスト
"""
    return {
        "number": number,
        "title": f"[予定] 性能テスト {number}",
        "body": body,
        "labels": [
            {"name": "calendar:event"},
            {"name": "calendar:schema-v1"},
            {"name": f"group:group-{first_group:02d}"},
            {"name": f"group:group-{second_group:02d}"},
        ],
        "updated_at": "2026-08-25T00:00:00Z",
    }


class PersonalScaleTests(unittest.TestCase):
    def test_personal_scale_baseline(self):
        issues = [scale_issue(number) for number in range(1, EVENT_COUNT + 1)]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            started = time.monotonic()
            result = generate(issues, "owner/repository", output)
            elapsed = time.monotonic() - started

            calendars = sorted(output.rglob("*.ics"))
            overall = (output / "calendar.ics").read_bytes()
            group_calendars = [
                path for path in calendars if path.parent.name == "calendars"
            ]

            self.assertEqual(result.published_events, EVENT_COUNT)
            self.assertEqual(len(result.calendars), GROUP_COUNT + 1)
            self.assertEqual(len(calendars), GROUP_COUNT + 1)
            self.assertEqual(overall.count(b"BEGIN:VEVENT"), EVENT_COUNT)
            self.assertTrue(
                all(calendar.read_bytes().count(b"BEGIN:VEVENT") == 50 for calendar in group_calendars)
            )
            self.assertLess(elapsed, 15.0)
            self.assertLess(len(overall), 2_000_000)
            self.assertLess((output / "index.html").stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
