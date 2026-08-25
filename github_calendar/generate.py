"""Generate RFC 5545 calendars from GitHub Issues."""

from __future__ import annotations

import argparse
import email.utils
import json
import re
import sys
import time as time_module
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


FIELD_RE = re.compile(r"^###\s+(.+?)\s*$", re.MULTILINE)
GROUP_RE = re.compile(r"^group:([a-z0-9][a-z0-9-]*)$")
EMPTY_VALUES = {"", "_No response_", "なし", "None"}
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
DEFAULT_API_TIMEOUT = 15.0
DEFAULT_API_ATTEMPTS = 3
DEFAULT_API_BACKOFF = 1.0


class EventError(ValueError):
    """An issue could not be converted to an event."""


class ApiError(RuntimeError):
    """GitHub Issues could not be fetched safely."""


@dataclass(frozen=True)
class Event:
    issue_number: int
    title: str
    start: date | datetime
    end: date | datetime
    all_day: bool
    timezone_name: str
    location: str
    description: str
    url: str
    groups: tuple[str, ...]
    categories: tuple[str, ...]
    updated_at: datetime


def parse_fields(body: str) -> dict[str, str]:
    """Parse the stable Markdown headings emitted by GitHub Issue Forms."""
    matches = list(FIELD_RE.finditer(body or ""))
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        fields[match.group(1).strip()] = body[match.end() : end].strip()
    return fields


def _value(fields: dict[str, str], name: str) -> str:
    value = fields.get(name, "").strip()
    return "" if value in EMPTY_VALUES else value


def issue_to_event(issue: dict, repository: str) -> Event:
    fields = parse_fields(issue.get("body") or "")
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label)
        for label in issue.get("labels", [])
    )
    all_day = "[x]" in _value(fields, "終日予定").lower()
    timezone_name = _value(fields, "タイムゾーン") or "Asia/Tokyo"
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise EventError(f"未対応のタイムゾーンです: {timezone_name}") from exc

    start_text, end_text = _value(fields, "開始"), _value(fields, "終了")
    try:
        if all_day:
            start: date | datetime = date.fromisoformat(start_text)
            final_day = date.fromisoformat(end_text)
            end: date | datetime = final_day + timedelta(days=1)
        else:
            start = datetime.strptime(start_text, "%Y-%m-%d %H:%M").replace(tzinfo=zone)
            end = datetime.strptime(end_text, "%Y-%m-%d %H:%M").replace(tzinfo=zone)
    except ValueError as exc:
        expected = "YYYY-MM-DD" if all_day else "YYYY-MM-DD HH:MM"
        raise EventError(f"開始・終了は {expected} 形式で入力してください") from exc
    if end <= start:
        raise EventError("終了は開始より後にしてください")

    raw_title = issue.get("title", "").strip()
    title = re.sub(r"^\[予定\]\s*", "", raw_title).strip()
    if not title:
        raise EventError("予定名が空です")
    event_url = _value(fields, "関連URL")
    if event_url:
        parsed_url = urllib.parse.urlparse(event_url)
        if "\n" in event_url or "\r" in event_url or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise EventError("関連URLは有効な http または https URLにしてください")
    groups = tuple(sorted(match.group(1) for label in labels if (match := GROUP_RE.fullmatch(label))))
    types = sorted(label.split(":", 1)[1] for label in labels if label.startswith("type:") and len(label) > 5)
    categories = tuple(groups + tuple(types))
    updated = issue.get("updated_at") or datetime.now(timezone.utc).isoformat()
    return Event(
        issue_number=int(issue["number"]), title=title, start=start, end=end,
        all_day=all_day, timezone_name=timezone_name,
        location=_value(fields, "場所"), description=_value(fields, "説明"),
        url=event_url, groups=groups, categories=categories,
        updated_at=datetime.fromisoformat(updated.replace("Z", "+00:00")),
    )


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def fold(line: str, limit: int = 75) -> str:
    """Fold a content line without splitting a UTF-8 code point."""
    parts: list[str] = []
    current = ""
    current_bytes = 0
    byte_limit = limit
    for char in line:
        size = len(char.encode("utf-8"))
        if current and current_bytes + size > byte_limit:
            parts.append(current)
            current, current_bytes, byte_limit = char, size, limit - 1
        else:
            current += char
            current_bytes += size
    parts.append(current)
    return "\r\n ".join(parts)


def render_calendar(events: Iterable[Event], repository: str, name: str) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//GitHub Calendar//Calender//JA",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{escape(name)}",
    ]
    def sort_key(item: Event) -> tuple[datetime, int]:
        if isinstance(item.start, datetime):
            value = item.start.astimezone(timezone.utc)
        else:
            value = datetime.combine(item.start, time.min, tzinfo=timezone.utc)
        return value, item.issue_number

    for event in sorted(events, key=sort_key):
        lines.extend(["BEGIN:VEVENT", f"UID:github-issue-{event.issue_number}@{repository.replace('/', '.')}" ])
        lines.append("DTSTAMP:" + event.updated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        if event.all_day:
            lines.extend([f"DTSTART;VALUE=DATE:{event.start:%Y%m%d}", f"DTEND;VALUE=DATE:{event.end:%Y%m%d}"])
        else:
            lines.extend([
                "DTSTART:" + event.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                "DTEND:" + event.end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            ])
        lines.append(f"SUMMARY:{escape(event.title)}")
        if event.description:
            lines.append(f"DESCRIPTION:{escape(event.description)}")
        if event.location:
            lines.append(f"LOCATION:{escape(event.location)}")
        if event.url:
            lines.append(f"URL:{event.url}")
        if event.categories:
            lines.append("CATEGORIES:" + ",".join(escape(value) for value in event.categories))
        lines.extend([f"X-GITHUB-ISSUE:{event.issue_number}", "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def _retry_after(headers, default: float) -> float:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return default


def _rate_limit_detail(headers) -> str:
    if not headers or headers.get("X-RateLimit-Remaining") != "0":
        return ""
    reset = headers.get("X-RateLimit-Reset")
    if not reset:
        return " (GitHub API rate limit exhausted)"
    try:
        reset_at = datetime.fromtimestamp(int(reset), timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return " (GitHub API rate limit exhausted)"
    return f" (GitHub API rate limit exhausted; resets at {reset_at})"


def _fetch_page(
    request: urllib.request.Request,
    repository: str,
    page: int,
    *,
    timeout: float,
    attempts: int,
    backoff: float,
) -> list[dict]:
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                batch = json.load(response)
            if not isinstance(batch, list):
                raise ApiError(
                    f"GitHub API returned an invalid response for {repository} page {page}: expected a list"
                )
            return batch
        except HTTPError as exc:
            detail = _rate_limit_detail(exc.headers)
            message = (
                f"GitHub API request failed for {repository} page {page}: "
                f"HTTP {exc.code} {exc.reason}{detail}"
            )
            if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == attempts:
                raise ApiError(message) from exc
            delay = _retry_after(exc.headers, backoff * (2 ** (attempt - 1)))
        except (TimeoutError, URLError) as exc:
            message = (
                f"GitHub API request failed for {repository} page {page}: {exc}"
            )
            if attempt == attempts:
                raise ApiError(message) from exc
            delay = backoff * (2 ** (attempt - 1))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(
                f"GitHub API returned invalid JSON for {repository} page {page}"
            ) from exc
        time_module.sleep(delay)
    raise AssertionError("unreachable")


def fetch_issues(
    repository: str,
    token: str,
    *,
    timeout: float = DEFAULT_API_TIMEOUT,
    attempts: int = DEFAULT_API_ATTEMPTS,
    backoff: float = DEFAULT_API_BACKOFF,
) -> list[dict]:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    if backoff < 0:
        raise ValueError("backoff must not be negative")
    issues: list[dict] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"state": "open", "labels": "calendar:event", "per_page": 100, "page": page})
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/issues?{query}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
        batch = _fetch_page(
            request, repository, page,
            timeout=timeout, attempts=attempts, backoff=backoff,
        )
        issues.extend(issue for issue in batch if "pull_request" not in issue)
        if len(batch) < 100:
            return issues
        page += 1


def generate(issues: Iterable[dict], repository: str, output: Path) -> None:
    events, errors = [], []
    for issue in issues:
        labels = {label["name"] if isinstance(label, dict) else str(label) for label in issue.get("labels", [])}
        if not {"calendar:exclude", "calendar:private"}.isdisjoint(labels):
            continue
        try:
            events.append(issue_to_event(issue, repository))
        except EventError as exc:
            errors.append(f"Issue #{issue.get('number')}: {exc}")
    if errors:
        raise EventError("\n".join(errors))
    output.mkdir(parents=True, exist_ok=True)
    (output / "calendar.ics").write_text(render_calendar(events, repository, "全体カレンダー"), encoding="utf-8", newline="")
    group_dir = output / "calendars"
    group_dir.mkdir(exist_ok=True)
    for old_file in group_dir.glob("*.ics"):
        old_file.unlink()
    for group in sorted({group for event in events for group in event.groups}):
        selected = [event for event in events if group in event.groups]
        (group_dir / f"{group}.ics").write_text(render_calendar(selected, repository, group), encoding="utf-8", newline="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--token", help="GitHub token; unnecessary with --input")
    parser.add_argument("--input", type=Path, help="JSON issue fixture instead of GitHub API")
    parser.add_argument("--output", type=Path, default=Path("public"))
    args = parser.parse_args()
    try:
        if args.input:
            issues = json.loads(args.input.read_text(encoding="utf-8"))
        elif args.token:
            issues = fetch_issues(args.repository, args.token)
        else:
            parser.error("--token or --input is required")
        generate(issues, args.repository, args.output)
    except (ApiError, EventError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
