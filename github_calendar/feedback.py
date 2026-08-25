"""Publish calendar validation results back to their GitHub Issues."""

from __future__ import annotations

import argparse
import html
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError


COMMENT_MARKER = "<!-- calendar-validation-feedback -->"


class FeedbackError(RuntimeError):
    """Validation feedback could not be synchronized."""


def _request_json(
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
):
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise FeedbackError(f"GitHub validation feedback request failed: {method} {url}") from exc


def _comment_body(message: str | None) -> str:
    if message is None:
        return (
            f"{COMMENT_MARKER}\n"
            "### ✅ カレンダー入力エラーは解消しました\n\n"
            "このIssueは現在のフォームschemaで正常に検証されています。"
        )
    safe_message = html.escape(message).replace("\n", "<br>")
    return (
        f"{COMMENT_MARKER}\n"
        "### ❌ カレンダーへ公開できません\n\n"
        f"<p>{safe_message}</p>\n\n"
        "Issue Formの入力を修正してください。修正後に再検証されます。"
    )


def _list_comments(api_base: str, issue_number: int, token: str) -> list[dict]:
    comments = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        batch = _request_json(
            f"{api_base}/issues/{issue_number}/comments?{query}", token
        )
        if not isinstance(batch, list):
            raise FeedbackError("GitHub comments response was not a list")
        comments.extend(batch)
        if len(batch) < 100:
            return comments
        page += 1


def sync_feedback(repository: str, token: str, report: dict) -> None:
    if report.get("version") != 1:
        raise FeedbackError("Unsupported validation report version")
    checked = report.get("checked_issue_numbers")
    errors = report.get("errors")
    if not isinstance(checked, list) or not isinstance(errors, list):
        raise FeedbackError("Invalid validation report")
    messages = {int(item["issue_number"]): str(item["message"]) for item in errors}
    api_base = f"https://api.github.com/repos/{repository}"

    for issue_number in sorted({int(value) for value in checked}):
        comments = _list_comments(api_base, issue_number, token)
        existing = next(
            (
                comment
                for comment in comments
                if COMMENT_MARKER in str(comment.get("body", ""))
                and comment.get("user", {}).get("login") == "github-actions[bot]"
            ),
            None,
        )
        message = messages.get(issue_number)
        if message is None and existing is None:
            continue
        body = _comment_body(message)
        if existing is not None and existing.get("body") == body:
            continue
        if existing is not None:
            _request_json(
                str(existing["url"]), token, method="PATCH", payload={"body": body}
            )
        else:
            create_url = f"{api_base}/issues/{issue_number}/comments"
            _request_json(create_url, token, method="POST", payload={"body": body})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        sync_feedback(args.repository, args.token, report)
    except (FeedbackError, OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
