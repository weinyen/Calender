import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from github_calendar.generate import EventError, fold, generate, issue_to_event, parse_fields, render_calendar


def issue(number=1, *, title="[予定] 開発定例会", start="2026-09-01 10:00", end="2026-09-01 11:00", timezone_name="Asia/Tokyo", all_day=False, labels=None):
    check = "- [x] 終日予定として登録する" if all_day else "- [ ] 終日予定として登録する"
    body = f"""### 開始

{start}

### 終了

{end}

### タイムゾーン

{timezone_name}

### 終日予定

{check}

### 場所

第1会議室

### 関連URL

https://example.com

### 説明

進捗確認, 質疑
次の行
"""
    return {"number": number, "title": title, "body": body, "labels": labels or [{"name": "calendar:event"}], "updated_at": "2026-08-25T00:00:00Z"}


class GenerateTests(unittest.TestCase):
    def test_parse_and_render_timed_event(self):
        event = issue_to_event(issue(labels=[{"name": "calendar:event"}, {"name": "group:development"}, {"name": "type:meeting"}]), "owner/repo")
        self.assertEqual(event.start.astimezone(timezone.utc).hour, 1)
        calendar = render_calendar([event], "owner/repo", "全体")
        self.assertIn("UID:github-issue-1@owner.repo", calendar)
        self.assertIn("DTSTART:20260901T010000Z", calendar)
        self.assertIn("DESCRIPTION:進捗確認\\, 質疑\\n次の行", calendar)
        self.assertIn("CATEGORIES:development,meeting", calendar)

    def test_all_day_end_is_exclusive(self):
        event = issue_to_event(issue(start="2026-09-01", end="2026-09-01", all_day=True), "owner/repo")
        self.assertEqual(event.start, date(2026, 9, 1))
        self.assertEqual(event.end, date(2026, 9, 2))
        calendar = render_calendar([event], "owner/repo", "全体")
        self.assertIn("DTEND;VALUE=DATE:20260902", calendar)

    def test_invalid_range_is_rejected(self):
        with self.assertRaisesRegex(EventError, "終了は開始より後"):
            issue_to_event(issue(end="2026-09-01 09:00"), "owner/repo")

    def test_invalid_url_is_rejected(self):
        invalid = issue()
        invalid["body"] = invalid["body"].replace("https://example.com", "javascript:alert(1)")
        with self.assertRaisesRegex(EventError, "有効な http"):
            issue_to_event(invalid, "owner/repo")

    def test_private_and_excluded_are_omitted_and_groups_created(self):
        issues = [
            issue(1, labels=[{"name": "calendar:event"}, {"name": "group:development"}]),
            issue(2, labels=[{"name": "calendar:event"}, {"name": "calendar:private"}]),
            issue(3, labels=[{"name": "calendar:event"}, {"name": "calendar:exclude"}]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            generate(issues, "owner/repo", output)
            all_calendar = (output / "calendar.ics").read_text()
            self.assertIn("github-issue-1", all_calendar)
            self.assertNotIn("github-issue-2", all_calendar)
            self.assertTrue((output / "calendars/development.ics").exists())

    def test_fold_limits_physical_lines_to_75_octets(self):
        folded = fold("DESCRIPTION:" + "日本語" * 40)
        self.assertTrue(all(len(line.encode()) <= 75 for line in folded.split("\r\n")))

    def test_parser_ignores_no_response(self):
        fields = parse_fields("### 場所\n\n_No response_\n\n### 説明\n\n内容")
        self.assertEqual(fields["場所"], "_No response_")


if __name__ == "__main__":
    unittest.main()
