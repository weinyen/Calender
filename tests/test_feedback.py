import unittest
from unittest.mock import patch

from github_calendar.feedback import COMMENT_MARKER, FeedbackError, sync_feedback


class FeedbackTests(unittest.TestCase):
    def test_sync_creates_escaped_error_comment(self):
        report = {
            "version": 1,
            "checked_issue_numbers": [12],
            "errors": [{"issue_number": 12, "message": "終了 < 開始 & 要修正"}],
        }

        with patch(
            "github_calendar.feedback._request_json",
            side_effect=[[], {"id": 1}],
        ) as request:
            sync_feedback("owner/repository", "token", report)

        create = request.call_args_list[1]
        self.assertEqual(create.kwargs["method"], "POST")
        body = create.kwargs["payload"]["body"]
        self.assertIn(COMMENT_MARKER, body)
        self.assertIn("終了 &lt; 開始 &amp; 要修正", body)
        self.assertNotIn("終了 < 開始", body)

    def test_sync_updates_bot_comment_when_error_is_resolved(self):
        old_comment = {
            "url": "https://api.github.com/comments/99",
            "body": COMMENT_MARKER + "\nold error",
            "user": {"login": "github-actions[bot]"},
        }
        user_comment = {
            "url": "https://api.github.com/comments/98",
            "body": COMMENT_MARKER + "\nspoofed",
            "user": {"login": "someone"},
        }
        report = {
            "version": 1,
            "checked_issue_numbers": [12],
            "errors": [],
        }

        with patch(
            "github_calendar.feedback._request_json",
            side_effect=[[user_comment, old_comment], {"id": 99}],
        ) as request:
            sync_feedback("owner/repository", "token", report)

        update = request.call_args_list[1]
        self.assertEqual(update.args[0], old_comment["url"])
        self.assertEqual(update.kwargs["method"], "PATCH")
        self.assertIn("入力エラーは解消", update.kwargs["payload"]["body"])

    def test_sync_does_not_create_success_comments_or_repeat_identical_errors(self):
        report = {
            "version": 1,
            "checked_issue_numbers": [10, 11],
            "errors": [{"issue_number": 11, "message": "invalid"}],
        }
        existing_body = (
            f"{COMMENT_MARKER}\n"
            "### ❌ カレンダーへ公開できません\n\n"
            "<p>invalid</p>\n\n"
            "Issue Formの入力を修正してください。修正後に再検証されます。"
        )
        existing = {
            "url": "https://api.github.com/comments/11",
            "body": existing_body,
            "user": {"login": "github-actions[bot]"},
        }

        with patch(
            "github_calendar.feedback._request_json",
            side_effect=[[], [existing]],
        ) as request:
            sync_feedback("owner/repository", "token", report)

        self.assertEqual(request.call_count, 2)

    def test_sync_rejects_invalid_report(self):
        with self.assertRaisesRegex(FeedbackError, "Unsupported"):
            sync_feedback("owner/repository", "token", {"version": 2})

    def test_sync_finds_bot_comment_on_later_comment_page(self):
        first_page = [
            {"body": "ordinary", "user": {"login": "someone"}}
            for _ in range(100)
        ]
        existing = {
            "url": "https://api.github.com/comments/101",
            "body": COMMENT_MARKER + "\nold error",
            "user": {"login": "github-actions[bot]"},
        }
        report = {
            "version": 1,
            "checked_issue_numbers": [12],
            "errors": [],
        }

        with patch(
            "github_calendar.feedback._request_json",
            side_effect=[first_page, [existing], {"id": 101}],
        ) as request:
            sync_feedback("owner/repository", "token", report)

        self.assertIn("page=1", request.call_args_list[0].args[0])
        self.assertIn("page=2", request.call_args_list[1].args[0])
        self.assertEqual(request.call_args_list[2].kwargs["method"], "PATCH")


if __name__ == "__main__":
    unittest.main()
