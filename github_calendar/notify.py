"""Maintain a single GitHub Issue for calendar publishing incidents."""

from __future__ import annotations

import argparse
import html
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError


INCIDENT_MARKER = "<!-- calendar-publish-incident -->"
INCIDENT_TITLE = "[Calendar] Publish workflow failure"


class NotificationError(RuntimeError):
    """The operational notification Issue could not be synchronized."""


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
        raise NotificationError(
            f"GitHub incident notification request failed: {method} {url}"
        ) from exc


def _find_incident(api_base: str, token: str) -> dict | None:
    page = 1
    while True:
        query = urllib.parse.urlencode(
            {"state": "all", "per_page": 100, "page": page, "sort": "updated"}
        )
        batch = _request_json(f"{api_base}/issues?{query}", token)
        if not isinstance(batch, list):
            raise NotificationError("GitHub Issues response was not a list")
        for issue in batch:
            if (
                "pull_request" not in issue
                and issue.get("title") == INCIDENT_TITLE
                and INCIDENT_MARKER in str(issue.get("body", ""))
                and issue.get("user", {}).get("login") == "github-actions[bot]"
            ):
                return issue
        if len(batch) < 100:
            return None
        page += 1


def _failure_body(
    run_url: str,
    workflow: str,
    commit_sha: str,
    build_result: str,
    deploy_result: str,
) -> str:
    return (
        f"{INCIDENT_MARKER}\n"
        "## カレンダー公開workflowが失敗しています\n\n"
        "最後に正常公開されたGitHub Pagesを維持しています。"
        "Actionsの実行結果を確認してください。\n\n"
        f"- Workflow: `{html.escape(workflow)}`\n"
        f"- Build: `{html.escape(build_result)}`\n"
        f"- Deploy: `{html.escape(deploy_result)}`\n"
        f"- Commit: `{html.escape(commit_sha)}`\n"
        f"- [失敗したworkflow runを開く]({html.escape(run_url, quote=True)})\n\n"
        "新しい失敗が発生した場合は、このIssueの内容を更新します。"
    )


def _resolved_body(run_url: str) -> str:
    return (
        f"{INCIDENT_MARKER}\n"
        "## カレンダー公開workflowは復旧しました\n\n"
        f"[正常終了したworkflow run]({html.escape(run_url, quote=True)})を確認し、"
        "このインシデントを自動的にCloseしました。"
    )


def sync_incident(
    repository: str,
    token: str,
    *,
    status: str,
    run_url: str,
    workflow: str,
    commit_sha: str,
    build_result: str,
    deploy_result: str,
) -> None:
    if status not in {"failure", "resolved"}:
        raise NotificationError(f"Unsupported incident status: {status}")
    api_base = f"https://api.github.com/repos/{repository}"
    incident = _find_incident(api_base, token)
    if status == "resolved":
        if incident is None or incident.get("state") != "open":
            return
        _request_json(
            str(incident["url"]),
            token,
            method="PATCH",
            payload={"body": _resolved_body(run_url), "state": "closed"},
        )
        return

    body = _failure_body(
        run_url, workflow, commit_sha, build_result, deploy_result
    )
    if incident is None:
        _request_json(
            f"{api_base}/issues",
            token,
            method="POST",
            payload={"title": INCIDENT_TITLE, "body": body},
        )
        return
    if incident.get("state") == "open" and incident.get("body") == body:
        return
    _request_json(
        str(incident["url"]),
        token,
        method="PATCH",
        payload={"body": body, "state": "open"},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--status", choices=("failure", "resolved"), required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--build-result", required=True)
    parser.add_argument("--deploy-result", required=True)
    args = parser.parse_args()
    try:
        sync_incident(
            args.repository,
            args.token,
            status=args.status,
            run_url=args.run_url,
            workflow=args.workflow,
            commit_sha=args.commit,
            build_result=args.build_result,
            deploy_result=args.deploy_result,
        )
    except (NotificationError, KeyError, TypeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
