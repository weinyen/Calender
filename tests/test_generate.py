import io
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from github_calendar.generate import ApiError, EventError, fetch_issues, fold, generate, issue_to_event, parse_fields, render_calendar, render_index


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
    def test_render_index_lists_subscriptions_without_event_details(self):
        events = [
            issue_to_event(
                issue(
                    title="[予定] indexには表示しない予定",
                    labels=[
                        {"name": "calendar:event"},
                        {"name": "group:development"},
                        {"name": "group:company"},
                    ],
                ),
                "owner/repository",
            ),
            issue_to_event(
                issue(
                    2,
                    labels=[
                        {"name": "calendar:event"},
                        {"name": "group:development"},
                    ],
                ),
                "owner/repository",
            ),
        ]

        index = render_index(
            events,
            "owner/repository",
            datetime(2026, 8, 25, 12, 34, tzinfo=timezone.utc),
        )

        self.assertIn("2件</strong><span>公開予定", index)
        self.assertIn("2026-08-25 12:34 UTC", index)
        self.assertIn("https://owner.github.io/repository/calendar.ics", index)
        self.assertIn("webcal://owner.github.io/repository/calendar.ics", index)
        self.assertIn("calendars/company.ics", index)
        self.assertIn("1件の予定", index)
        self.assertIn("calendars/development.ics", index)
        self.assertIn("2件の予定", index)
        self.assertNotIn("indexには表示しない予定", index)

    def test_render_index_escapes_repository_name(self):
        index = render_index(
            [],
            "owner/repository<script>",
            datetime(2026, 8, 25, tzinfo=timezone.utc),
        )

        self.assertNotIn("repository<script>", index)
        self.assertIn("repository%3Cscript%3E/calendar.ics", index)
        self.assertIn("owner/repository&lt;script&gt;", index)

    def test_render_index_supports_user_pages_repository(self):
        index = render_index(
            [],
            "owner/owner.github.io",
            datetime(2026, 8, 25, tzinfo=timezone.utc),
        )

        self.assertIn("https://owner.github.io/calendar.ics", index)
        self.assertNotIn("owner.github.io/owner.github.io", index)

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

    def test_nonexistent_dst_start_time_is_rejected_with_issue_number(self):
        invalid = issue(
            406,
            start="2026-03-08 02:30",
            end="2026-03-08 04:00",
            timezone_name="America/New_York",
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                EventError,
                r"Issue #406: 開始.*2026-03-08 02:30.*America/New_York.*存在しません",
            ):
                generate([invalid], "owner/repo", Path(directory))

    def test_ambiguous_dst_end_time_is_rejected_with_issue_number(self):
        invalid = issue(
            407,
            start="2026-11-01 00:30",
            end="2026-11-01 01:30",
            timezone_name="America/New_York",
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                EventError,
                r"Issue #407: 終了.*2026-11-01 01:30.*America/New_York.*曖昧",
            ):
                generate([invalid], "owner/repo", Path(directory))

    def test_dst_validation_is_applied_to_both_start_and_end(self):
        cases = [
            (
                issue(
                    408,
                    start="2026-11-01 01:30",
                    end="2026-11-01 02:30",
                    timezone_name="America/New_York",
                ),
                r"Issue #408: 開始.*曖昧",
            ),
            (
                issue(
                    409,
                    start="2026-03-08 01:30",
                    end="2026-03-08 02:30",
                    timezone_name="America/New_York",
                ),
                r"Issue #409: 終了.*存在しません",
            ),
        ]

        for invalid, expected in cases:
            with self.subTest(issue=invalid["number"]):
                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaisesRegex(EventError, expected):
                        generate([invalid], "owner/repo", Path(directory))

    def test_unambiguous_times_work_in_dst_and_non_dst_zones(self):
        new_york = issue_to_event(
            issue(
                start="2026-03-08 03:30",
                end="2026-03-08 04:30",
                timezone_name="America/New_York",
            ),
            "owner/repo",
        )
        tokyo = issue_to_event(issue(timezone_name="Asia/Tokyo"), "owner/repo")
        utc = issue_to_event(issue(timezone_name="UTC"), "owner/repo")

        self.assertEqual(new_york.start.astimezone(timezone.utc).hour, 7)
        self.assertEqual(tokyo.start.astimezone(timezone.utc).hour, 1)
        self.assertEqual(utc.start.astimezone(timezone.utc).hour, 10)

    def test_all_day_event_is_not_subject_to_dst_wall_time_validation(self):
        event = issue_to_event(
            issue(
                start="2026-03-08",
                end="2026-03-08",
                timezone_name="America/New_York",
                all_day=True,
            ),
            "owner/repo",
        )

        self.assertTrue(event.all_day)
        self.assertEqual(event.start, date(2026, 3, 8))
        self.assertEqual(event.end, date(2026, 3, 9))

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

    def test_render_normalizes_text_newlines_but_preserves_uri_characters(self):
        special = issue(labels=[{"name": "calendar:event"}])
        special["body"] = special["body"].replace(
            "進捗確認, 質疑\n次の行",
            "Windows\r\nMac\rUnix\n改行",
        ).replace(
            "https://example.com",
            "https://example.com/path?q=one,two;three",
        )

        calendar = render_calendar(
            [issue_to_event(special, "owner/repo")], "owner/repo", "全体"
        )

        self.assertIn(r"DESCRIPTION:Windows\nMac\nUnix\n改行", calendar)
        self.assertIn("URL:https://example.com/path?q=one,two;three", calendar)
        self.assertNotIn("URL:https://example.com/path?q=one\\,two\\;three", calendar)

    def test_generate_rejects_control_characters_with_issue_number(self):
        invalid = issue(405, title="[予定] 不正\x00件名")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EventError, r"Issue #405:.*制御文字"):
                generate([invalid], "owner/repo", Path(directory))

    def test_all_rendered_properties_are_folded_to_75_octets(self):
        long_value = "長い値" * 40
        special = issue(
            title="[予定] " + long_value,
            labels=[
                {"name": "calendar:event"},
                {"name": "type:" + long_value},
            ],
        )
        special["body"] = special["body"].replace("第1会議室", long_value)
        special["body"] = special["body"].replace(
            "進捗確認, 質疑\n次の行", long_value
        )
        special["body"] = special["body"].replace(
            "https://example.com", "https://example.com/" + "path/" * 30
        )

        calendar = render_calendar(
            [issue_to_event(special, "owner/repo")],
            "owner/repo",
            long_value,
        )

        self.assertTrue(
            all(
                len(line.encode("utf-8")) <= 75
                for line in calendar.split("\r\n")
            )
        )

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

    def test_generate_replaces_the_complete_output_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            stale = output / "calendars" / "obsolete.ics"
            unrelated = output / "partial.txt"
            stale.parent.mkdir(parents=True)
            stale.write_text("old calendar", encoding="utf-8")
            unrelated.write_text("old partial output", encoding="utf-8")

            generate(
                [issue(labels=[{"name": "calendar:event"}, {"name": "group:new"}])],
                "owner/repo",
                output,
            )

            self.assertTrue((output / "calendar.ics").exists())
            self.assertTrue((output / "calendars/new.ics").exists())
            self.assertFalse(stale.exists())
            self.assertFalse(unrelated.exists())
            self.assertEqual(list(Path(directory).glob(".public.tmp-*")), [])

    def test_generate_keeps_previous_output_when_staging_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            previous = output / "calendar.ics"
            output.mkdir()
            previous.write_text("last known good", encoding="utf-8")

            with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    generate([], "owner/repo", output)

            self.assertEqual(previous.read_text(encoding="utf-8"), "last known good")
            self.assertEqual(list(Path(directory).glob(".public.tmp-*")), [])

    def test_generate_restores_previous_output_when_commit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            previous = output / "calendar.ics"
            output.mkdir()
            previous.write_text("last known good", encoding="utf-8")

            real_replace = __import__("os").replace
            calls = 0

            def fail_new_output(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("rename failed")
                return real_replace(source, destination)

            with patch("github_calendar.generate.os.replace", side_effect=fail_new_output):
                with self.assertRaisesRegex(OSError, "rename failed"):
                    generate([], "owner/repo", output)

            self.assertEqual(previous.read_text(encoding="utf-8"), "last known good")
            self.assertEqual(list(Path(directory).glob(".public.tmp-*")), [])

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
        self.assertEqual(urlopen.call_args_list[0].kwargs["timeout"], 15.0)

    def test_fetch_issues_retries_temporary_errors_with_exponential_backoff(self):
        responses = [
            _http_error(503, "Service Unavailable"),
            _JsonResponse([]),
        ]

        with (
            patch("urllib.request.urlopen", side_effect=responses) as urlopen,
            patch("github_calendar.generate.time_module.sleep") as sleep,
        ):
            issues = fetch_issues("owner/repo", "token", backoff=2)

        self.assertEqual(issues, [])
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_fetch_issues_honors_retry_after(self):
        responses = [
            _http_error(429, "Too Many Requests", {"Retry-After": "7"}),
            _JsonResponse([]),
        ]

        with (
            patch("urllib.request.urlopen", side_effect=responses),
            patch("github_calendar.generate.time_module.sleep") as sleep,
        ):
            fetch_issues("owner/repo", "token")

        sleep.assert_called_once_with(7.0)

    def test_fetch_issues_does_not_retry_permanent_http_errors(self):
        error = _http_error(401, "Unauthorized")

        with (
            patch("urllib.request.urlopen", side_effect=error) as urlopen,
            patch("github_calendar.generate.time_module.sleep") as sleep,
        ):
            with self.assertRaisesRegex(ApiError, r"owner/repo page 1: HTTP 401"):
                fetch_issues("owner/repo", "secret-token")

        urlopen.assert_called_once()
        sleep.assert_not_called()

    def test_fetch_issues_reports_rate_limit_reset_without_token(self):
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"}

        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error(403, "Forbidden", headers),
        ):
            with self.assertRaises(ApiError) as raised:
                fetch_issues("owner/repo", "secret-token")

        message = str(raised.exception)
        self.assertIn("rate limit exhausted", message)
        self.assertIn("1970-01-01T00:00:00+00:00", message)
        self.assertNotIn("secret-token", message)

    def test_fetch_issues_stops_after_retry_limit_for_network_errors(self):
        with (
            patch(
                "urllib.request.urlopen",
                side_effect=URLError("temporary network failure"),
            ) as urlopen,
            patch("github_calendar.generate.time_module.sleep") as sleep,
        ):
            with self.assertRaisesRegex(ApiError, r"owner/repo page 1"):
                fetch_issues("owner/repo", "token", attempts=3, backoff=1)

        self.assertEqual(urlopen.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_fetch_issues_rejects_invalid_json_without_retry(self):
        response = _RawResponse(b"not JSON")

        with (
            patch("urllib.request.urlopen", return_value=response) as urlopen,
            patch("github_calendar.generate.time_module.sleep") as sleep,
        ):
            with self.assertRaisesRegex(ApiError, r"invalid JSON.*page 1"):
                fetch_issues("owner/repo", "token")

        urlopen.assert_called_once()
        sleep.assert_not_called()


class _JsonResponse:
    def __init__(self, value):
        self._content = io.BytesIO(json.dumps(value).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size=-1):
        return self._content.read(size)


class _RawResponse(_JsonResponse):
    def __init__(self, value):
        self._content = io.BytesIO(value)


def _http_error(code, reason, headers=None):
    return HTTPError(
        "https://api.github.com/repos/owner/repo/issues",
        code,
        reason,
        headers or {},
        None,
    )


if __name__ == "__main__":
    unittest.main()
