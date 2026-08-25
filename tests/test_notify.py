import unittest
from unittest.mock import patch

from github_calendar.notify import (
    INCIDENT_MARKER,
    INCIDENT_TITLE,
    NotificationError,
    sync_incident,
)


ARGS = {
    "run_url": "https://github.com/owner/repository/actions/runs/123",
    "workflow": "Publish calendar",
    "commit_sha": "abc123",
    "build_result": "failure",
    "deploy_result": "skipped",
}


class NotificationTests(unittest.TestCase):
    def test_failure_creates_single_incident(self):
        with patch(
            "github_calendar.notify._request_json",
            side_effect=[[], {"number": 20}],
        ) as request:
            sync_incident(
                "owner/repository", "token", status="failure", **ARGS
            )

        create = request.call_args_list[1]
        self.assertEqual(create.kwargs["method"], "POST")
        payload = create.kwargs["payload"]
        self.assertEqual(payload["title"], INCIDENT_TITLE)
        self.assertIn(INCIDENT_MARKER, payload["body"])
        self.assertIn(ARGS["run_url"], payload["body"])
        self.assertIn("`failure`", payload["body"])

    def test_failure_reopens_and_updates_previous_incident(self):
        incident = {
            "url": "https://api.github.com/issues/20",
            "title": INCIDENT_TITLE,
            "body": INCIDENT_MARKER + "\nresolved",
            "state": "closed",
            "user": {"login": "github-actions[bot]"},
        }
        with patch(
            "github_calendar.notify._request_json",
            side_effect=[[incident], {"number": 20}],
        ) as request:
            sync_incident(
                "owner/repository", "token", status="failure", **ARGS
            )

        update = request.call_args_list[1]
        self.assertEqual(update.args[0], incident["url"])
        self.assertEqual(update.kwargs["method"], "PATCH")
        self.assertEqual(update.kwargs["payload"]["state"], "open")

    def test_success_closes_open_incident(self):
        incident = {
            "url": "https://api.github.com/issues/20",
            "title": INCIDENT_TITLE,
            "body": INCIDENT_MARKER + "\nfailure",
            "state": "open",
            "user": {"login": "github-actions[bot]"},
        }
        with patch(
            "github_calendar.notify._request_json",
            side_effect=[[incident], {"number": 20}],
        ) as request:
            sync_incident(
                "owner/repository", "token", status="resolved", **ARGS
            )

        update = request.call_args_list[1]
        self.assertEqual(update.kwargs["payload"]["state"], "closed")
        self.assertIn("復旧しました", update.kwargs["payload"]["body"])

    def test_success_without_open_incident_is_noop(self):
        with patch(
            "github_calendar.notify._request_json", return_value=[]
        ) as request:
            sync_incident(
                "owner/repository", "token", status="resolved", **ARGS
            )

        request.assert_called_once()

    def test_user_issue_with_marker_is_not_modified(self):
        spoofed = {
            "url": "https://api.github.com/issues/19",
            "title": INCIDENT_TITLE,
            "body": INCIDENT_MARKER,
            "state": "open",
            "user": {"login": "someone"},
        }
        with patch(
            "github_calendar.notify._request_json",
            side_effect=[[spoofed], {"number": 20}],
        ) as request:
            sync_incident(
                "owner/repository", "token", status="failure", **ARGS
            )

        self.assertEqual(request.call_args_list[1].kwargs["method"], "POST")

    def test_incident_search_paginates(self):
        first_page = [
            {
                "title": f"ordinary {number}",
                "body": "",
                "user": {"login": "someone"},
            }
            for number in range(100)
        ]
        with patch(
            "github_calendar.notify._request_json",
            side_effect=[first_page, []],
        ) as request:
            sync_incident(
                "owner/repository", "token", status="resolved", **ARGS
            )

        self.assertIn("page=1", request.call_args_list[0].args[0])
        self.assertIn("page=2", request.call_args_list[1].args[0])

    def test_invalid_status_is_rejected(self):
        with self.assertRaisesRegex(NotificationError, "Unsupported"):
            sync_incident(
                "owner/repository", "token", status="unknown", **ARGS
            )


if __name__ == "__main__":
    unittest.main()
