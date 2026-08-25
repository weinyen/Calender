import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from github_calendar.generate import EventError, fetch_issues, fold, generate, issue_to_event, parse_fields, render_calendar


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

    def test_empty_calendar_removes_stale_group_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            stale = output / "calendars/obsolete.ics"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")

            generate([], "owner/repo", output)

            self.assertFalse(stale.exists())
            self.assertEqual(list((output / "calendars").glob("*.ics")), [])
            calendar = (output / "calendar.ics").read_text(encoding="utf-8")
            self.assertIn("BEGIN:VCALENDAR", calendar)
            self.assertIn("END:VCALENDAR", calendar)
            self.assertNotIn("BEGIN:VEVENT", calendar)

    def test_render_escapes_special_characters_in_all_text_properties(self):
        special = issue(
            title="[予定] 設計,確認;会\\議",
            labels=[
                {"name": "calendar:event"},
                {"name": "group:development"},
                {"name": "type:review,urgent"},
            ],
        )
        special["body"] = special["body"].replace(
            "第1会議室", "A棟; 会議室, 1\\2"
        )

        calendar = render_calendar(
            [issue_to_event(special, "owner/repo")], "owner/repo", "全体,開発"
        )

        self.assertIn(r"X-WR-CALNAME:全体\,開発", calendar)
        self.assertIn(r"SUMMARY:設計\,確認\;会\\議", calendar)
        self.assertIn(r"LOCATION:A棟\; 会議室\, 1\\2", calendar)
        self.assertIn(r"CATEGORIES:development,review\,urgent", calendar)

    def test_rendered_long_japanese_lines_are_folded_at_utf8_boundaries(self):
        long_event = issue_to_event(
            issue(title="[予定] " + "日本語の長い予定名" * 20), "owner/repo"
        )

        calendar = render_calendar([long_event], "owner/repo", "全体")
        physical_lines = calendar.split("\r\n")

        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in physical_lines))
        self.assertTrue(any(line.startswith(" ") for line in physical_lines))
        calendar.encode("utf-8").decode("utf-8")

    def test_generate_reports_issue_number_for_invalid_api_data(self):
        invalid = issue(404)
        invalid["body"] = ""

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EventError, r"Issue #404: 開始・終了"):
                generate([invalid], "owner/repo", Path(directory))

    def test_fetch_issues_paginates_and_omits_pull_requests(self):
        first_page = [
            {"number": number, "title": f"Issue {number}"}
            for number in range(1, 100)
        ] + [{"number": 100, "pull_request": {"url": "https://example.com/pr/100"}}]
        second_page = [{"number": 101, "title": "Issue 101"}]
        responses = [_JsonResponse(first_page), _JsonResponse(second_page)]

        with patch("urllib.request.urlopen", side_effect=responses) as urlopen:
            issues = fetch_issues("owner/repo", "secret-token")

        self.assertEqual(len(issues), 100)
        self.assertEqual(issues[-1]["number"], 101)
        self.assertEqual(urlopen.call_count, 2)
        first_request, second_request = (call.args[0] for call in urlopen.call_args_list)
        self.assertIn("page=1", first_request.full_url)
        self.assertIn("page=2", second_request.full_url)
        self.assertEqual(first_request.get_header("Authorization"), "Bearer secret-token")


class _JsonResponse:
    def __init__(self, value):
        self._content = io.BytesIO(json.dumps(value).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return self._content.read(size)


if __name__ == "__main__":
    unittest.main()
