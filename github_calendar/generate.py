"""Generate RFC 5545 calendars from GitHub Issues."""

from __future__ import annotations

import argparse
import email.utils
import html
import json
import os
import re
import shutil
import sys
import tempfile
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
SCHEMA_MARKER_RE = re.compile(r"<!--\s*calendar-schema:\s*(\d+)\s*-->")
SCHEMA_MARKER_PREFIX_RE = re.compile(r"<!--\s*calendar-schema:")
SCHEMA_LABEL_RE = re.compile(r"^calendar:schema-v(\d+)$")
EMPTY_VALUES = {"", "_No response_", "なし", "None"}
RETRYABLE_HTTP_STATUSES = {429, 502, 503, 504}
DEFAULT_API_TIMEOUT = 15.0
DEFAULT_API_ATTEMPTS = 3
DEFAULT_API_BACKOFF = 1.0
DEFAULT_SCHEMA_VERSION = 1
SCHEMAS = {
    1: {
        "start": "開始",
        "end": "終了",
        "timezone": "タイムゾーン",
        "all_day": "終日予定",
        "location": "場所",
        "url": "関連URL",
        "description": "説明",
    }
}


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


@dataclass(frozen=True)
class CalendarResult:
    name: str
    path: str
    event_count: int


@dataclass(frozen=True)
class GenerationResult:
    generated_at: datetime
    published_events: int
    excluded_events: int
    private_events: int
    calendars: tuple[CalendarResult, ...]


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


def detect_schema_version(body: str, labels: Iterable[str]) -> int:
    """Resolve an Issue Form schema version without breaking legacy issues."""
    marker_values = SCHEMA_MARKER_RE.findall(body or "")
    if SCHEMA_MARKER_PREFIX_RE.search(body or "") and not marker_values:
        raise EventError("calendar schema markerの形式が不正です")
    if len(marker_values) > 1:
        raise EventError("calendar schema markerが重複しています")

    schema_labels = [label for label in labels if label.startswith("calendar:schema-")]
    label_values = []
    for label in schema_labels:
        match = SCHEMA_LABEL_RE.fullmatch(label)
        if not match:
            raise EventError(f"calendar schemaラベルの形式が不正です: {label}")
        label_values.append(match.group(1))
    if len(label_values) > 1:
        raise EventError("calendar schemaラベルが重複しています")

    declared = {int(value) for value in marker_values + label_values}
    if len(declared) > 1:
        raise EventError("calendar schemaの指定が競合しています")
    version = declared.pop() if declared else DEFAULT_SCHEMA_VERSION
    if version not in SCHEMAS:
        supported = ", ".join(str(item) for item in sorted(SCHEMAS))
        raise EventError(
            f"未対応のcalendar schema versionです: {version} (対応version: {supported})"
        )
    return version


def _schema_value(
    fields: dict[str, str], schema_version: int, logical_name: str
) -> str:
    return _value(fields, SCHEMAS[schema_version][logical_name])


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if any(
        (ord(char) < 0x20 and char not in {"\n", "\t"}) or ord(char) == 0x7f
        for char in value
    ):
        raise EventError("ICSのテキストに制御文字は使用できません")
    return value


def _localize_datetime(value: str, zone: ZoneInfo, field_name: str) -> datetime:
    """Parse a local time while rejecting DST gaps and ambiguous wall times."""
    naive = datetime.strptime(value, "%Y-%m-%d %H:%M")
    candidates = [naive.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
    valid = [
        candidate
        for candidate in candidates
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        == naive
    ]
    offsets = {candidate.utcoffset() for candidate in valid}
    if not valid:
        raise EventError(
            f"{field_name}のローカル時刻 {value} は {zone.key} では存在しません。"
            "DSTの切り替えを避けた別の時刻を指定してください"
        )
    if len(offsets) > 1:
        raise EventError(
            f"{field_name}のローカル時刻 {value} は {zone.key} では2回存在するため曖昧です。"
            "DSTの切り替えを避けた別の時刻を指定してください"
        )
    return valid[0]


def issue_to_event(issue: dict, repository: str) -> Event:
    body = issue.get("body") or ""
    fields = parse_fields(body)
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label)
        for label in issue.get("labels", [])
    )
    schema_version = detect_schema_version(body, labels)
    all_day = "[x]" in _schema_value(
        fields, schema_version, "all_day"
    ).lower()
    timezone_name = (
        _schema_value(fields, schema_version, "timezone") or "Asia/Tokyo"
    )
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise EventError(f"未対応のタイムゾーンです: {timezone_name}") from exc

    start_text = _schema_value(fields, schema_version, "start")
    end_text = _schema_value(fields, schema_version, "end")
    try:
        if all_day:
            start: date | datetime = date.fromisoformat(start_text)
            final_day = date.fromisoformat(end_text)
            end: date | datetime = final_day + timedelta(days=1)
        else:
            start = _localize_datetime(start_text, zone, "開始")
            end = _localize_datetime(end_text, zone, "終了")
    except EventError:
        raise
    except ValueError as exc:
        expected = "YYYY-MM-DD" if all_day else "YYYY-MM-DD HH:MM"
        raise EventError(f"開始・終了は {expected} 形式で入力してください") from exc
    if end <= start:
        raise EventError("終了は開始より後にしてください")

    raw_title = issue.get("title", "").strip()
    title = re.sub(r"^\[予定\]\s*", "", raw_title).strip()
    if not title:
        raise EventError("予定名が空です")
    event_url = _schema_value(fields, schema_version, "url")
    if event_url:
        parsed_url = urllib.parse.urlparse(event_url)
        if (
            any(ord(char) < 0x20 or ord(char) == 0x7f for char in event_url)
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
        ):
            raise EventError("関連URLは有効な http または https URLにしてください")
    groups = tuple(sorted(match.group(1) for label in labels if (match := GROUP_RE.fullmatch(label))))
    types = sorted(label.split(":", 1)[1] for label in labels if label.startswith("type:") and len(label) > 5)
    categories = tuple(groups + tuple(types))
    location = _schema_value(fields, schema_version, "location")
    description = _schema_value(fields, schema_version, "description")
    for text_value in (title, location, description, *categories):
        _normalize_text(text_value)
    updated = issue.get("updated_at") or datetime.now(timezone.utc).isoformat()
    return Event(
        issue_number=int(issue["number"]), title=title, start=start, end=end,
        all_day=all_day, timezone_name=timezone_name,
        location=location, description=description,
        url=event_url, groups=groups, categories=categories,
        updated_at=datetime.fromisoformat(updated.replace("Z", "+00:00")),
    )


def escape(value: str) -> str:
    """Normalize and escape an RFC 5545 TEXT value."""
    value = _normalize_text(value)
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


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


def _pages_base_url(repository: str) -> str:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name:
        raise ValueError("repository must use owner/repository format")
    owner_path = urllib.parse.quote(owner, safe="")
    repository_path = urllib.parse.quote(name, safe="")
    if name.casefold() == f"{owner}.github.io".casefold():
        return f"https://{owner_path}.github.io"
    return f"https://{owner_path}.github.io/{repository_path}"


def render_index(
    events: Iterable[Event],
    repository: str,
    generated_at: datetime,
) -> str:
    """Render a dependency-free subscription and publication status page."""
    event_list = list(events)
    groups = sorted({group for event in event_list for group in event.groups})
    base_url = _pages_base_url(repository)
    generated_at = generated_at.astimezone(timezone.utc)

    def calendar_card(title: str, path: str, count: int) -> str:
        https_url = f"{base_url}/{urllib.parse.quote(path, safe='/')}"
        webcal_url = "webcal://" + https_url.removeprefix("https://")
        escaped_path = html.escape(path, quote=True)
        escaped_https = html.escape(https_url, quote=True)
        return f"""
        <article class="calendar-card" data-calendar-path="{escaped_path}">
          <div>
            <p class="eyebrow">{count}件の予定</p>
            <h3>{html.escape(title)}</h3>
          </div>
          <code class="calendar-url">{escaped_https}</code>
          <div class="actions">
            <a class="button primary subscribe-link" href="{html.escape(webcal_url, quote=True)}" aria-label="{html.escape(title)}をwebcalで購読">webcalで購読</a>
            <button class="button copy-button" type="button">URLをコピー</button>
            <a class="text-link download-link" href="{escaped_https}">ICSを開く</a>
          </div>
        </article>"""

    cards = [calendar_card("全体カレンダー", "calendar.ics", len(event_list))]
    cards.extend(
        calendar_card(
            group,
            f"calendars/{group}.ics",
            sum(group in event.groups for event in event_list),
        )
        for group in groups
    )
    group_summary = (
        f"{len(groups)}個のグループ別カレンダーを公開しています。"
        if groups
        else "現在、グループ別カレンダーはありません。"
    )
    timestamp = generated_at.isoformat().replace("+00:00", "Z")
    display_timestamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="GitHub Issuesから生成した公開カレンダーの購読案内">
  <title>GitHub Calendar</title>
  <style>
    :root {{ color-scheme: light; --ink: #172235; --muted: #5d6878; --line: #dce2ea; --surface: #fff; --accent: #3157d5; --accent-dark: #2442a5; --tint: #eef3ff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: var(--ink); background: #f5f7fb; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; }}
    .shell {{ width: min(960px, calc(100% - 32px)); margin: 0 auto; padding: 56px 0 72px; }}
    .hero {{ padding: 40px; color: #fff; background: linear-gradient(135deg, #172a61, #3157d5 62%, #6486ef); border-radius: 24px; box-shadow: 0 18px 50px rgb(35 58 125 / 18%); }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; margin: 0 0 16px; padding: 5px 11px; color: #143a29; background: #c9f7df; border-radius: 999px; font-size: .875rem; font-weight: 700; }}
    .status::before {{ width: 8px; height: 8px; content: ""; background: #168553; border-radius: 50%; }}
    h1 {{ margin: 0; font-size: clamp(2rem, 7vw, 4rem); line-height: 1.08; letter-spacing: -.04em; }}
    .lead {{ max-width: 650px; margin: 18px 0 0; color: #e5ebff; font-size: 1.05rem; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 22px 0 0; }}
    .metric {{ padding: 14px 16px; background: rgb(255 255 255 / 12%); border: 1px solid rgb(255 255 255 / 20%); border-radius: 14px; }}
    .metric strong, .metric span {{ display: block; }}
    .metric strong {{ font-size: 1.2rem; }}
    .metric span {{ color: #d9e2ff; font-size: .82rem; }}
    section {{ margin-top: 42px; }}
    h2 {{ margin: 0 0 8px; font-size: 1.6rem; letter-spacing: -.02em; }}
    .section-copy {{ margin: 0 0 20px; color: var(--muted); }}
    .calendar-grid {{ display: grid; gap: 16px; }}
    .calendar-card {{ display: grid; gap: 18px; padding: 24px; background: var(--surface); border: 1px solid var(--line); border-radius: 18px; box-shadow: 0 8px 24px rgb(23 34 53 / 5%); }}
    .eyebrow {{ margin: 0; color: var(--accent); font-size: .8rem; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; }}
    h3 {{ margin: 2px 0 0; font-size: 1.25rem; }}
    code {{ display: block; overflow-wrap: anywhere; padding: 12px 14px; color: #26334c; background: #f3f5f8; border-radius: 10px; font-size: .86rem; }}
    .actions {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }}
    .button {{ min-height: 42px; padding: 9px 15px; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 10px; font: inherit; font-weight: 700; text-decoration: none; cursor: pointer; }}
    .button:hover {{ border-color: #a9b6ca; background: #f8faff; }}
    .primary {{ color: #fff; background: var(--accent); border-color: var(--accent); }}
    .primary:hover {{ background: var(--accent-dark); border-color: var(--accent-dark); }}
    .text-link {{ padding: 8px 4px; color: var(--accent); font-weight: 700; }}
    .guide {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .guide article {{ padding: 20px; background: var(--tint); border-radius: 16px; }}
    .guide h3 {{ font-size: 1rem; }}
    .guide p {{ margin: 8px 0 0; color: var(--muted); font-size: .92rem; }}
    .notice {{ padding: 20px 22px; background: #fff8e5; border: 1px solid #f0d88e; border-radius: 16px; }}
    .notice strong {{ display: block; margin-bottom: 4px; }}
    .notice p {{ margin: 0; color: #685719; }}
    footer {{ margin-top: 44px; color: var(--muted); font-size: .86rem; text-align: center; }}
    @media (max-width: 680px) {{ .shell {{ padding-top: 24px; }} .hero {{ padding: 28px 22px; border-radius: 18px; }} .metrics, .guide {{ grid-template-columns: 1fr; }} .calendar-card {{ padding: 20px; }} .actions {{ align-items: stretch; }} .button, .text-link {{ width: 100%; text-align: center; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="hero">
      <p class="status">公開中</p>
      <h1>GitHub Calendar</h1>
      <p class="lead">GitHub Issuesから生成された公開カレンダーです。利用するカレンダーのURLを、普段お使いのアプリへ登録してください。</p>
      <div class="metrics">
        <div class="metric"><strong>{len(event_list)}件</strong><span>公開予定</span></div>
        <div class="metric"><strong><time datetime="{timestamp}">{display_timestamp}</time></strong><span>最終生成</span></div>
      </div>
    </header>

    <section aria-labelledby="calendars-title">
      <h2 id="calendars-title">購読するカレンダー</h2>
      <p class="section-copy">{group_summary} ファイルを一度だけ取り込むのではなく、URLを指定して購読してください。</p>
      <div class="calendar-grid">{"".join(cards)}</div>
      <p class="copy-status" aria-live="polite"></p>
    </section>

    <section aria-labelledby="guide-title">
      <h2 id="guide-title">登録方法</h2>
      <p class="section-copy">アプリによって更新の反映まで時間がかかる場合があります。</p>
      <div class="guide">
        <article><h3>Google Calendar</h3><p>「URLから追加」にHTTPS URLを設定します。</p></article>
        <article><h3>Apple Calendar</h3><p>「カレンダー照会」でHTTPS URLを設定するか、「webcalで購読」を選びます。</p></article>
        <article><h3>Outlook</h3><p>インターネットカレンダーとしてHTTPS URLを追加します。</p></article>
      </div>
    </section>

    <section class="notice" aria-label="公開上の注意">
      <strong>公開カレンダーです</strong>
      <p>ここから購読できる予定情報は公開されています。更新間隔は各カレンダーアプリに依存します。</p>
    </section>

    <footer>Source: {html.escape(repository)}</footer>
  </main>
  <script>
    const status = document.querySelector('.copy-status');
    document.querySelectorAll('[data-calendar-path]').forEach((card) => {{
      const url = new URL(card.dataset.calendarPath, window.location.href).href;
      card.querySelector('.calendar-url').textContent = url;
      card.querySelector('.download-link').href = url;
      card.querySelector('.subscribe-link').href = url.replace(/^https?:/, 'webcal:');
      card.querySelector('.copy-button').addEventListener('click', async () => {{
        try {{
          await navigator.clipboard.writeText(url);
          status.textContent = '購読URLをコピーしました。';
        }} catch (error) {{
          status.textContent = 'コピーできませんでした。表示されたURLを選択してコピーしてください。';
        }}
      }});
    }});
  </script>
</body>
</html>
"""


def render_summary(result: GenerationResult, repository: str) -> str:
    """Render generation metadata for a GitHub Actions job summary."""
    base_url = _pages_base_url(repository)
    generated_at = result.generated_at.astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    rows = []
    for calendar in result.calendars:
        url = f"{base_url}/{urllib.parse.quote(calendar.path, safe='/')}"
        rows.append(
            f"| {calendar.name} | {calendar.event_count} | "
            f"[`{calendar.path}`]({url}) |"
        )
    return "\n".join(
        [
            "## Calendar generation",
            "",
            "✅ Calendar artifacts were generated successfully.",
            "",
            "| Item | Result |",
            "| --- | ---: |",
            f"| Published events | {result.published_events} |",
            f"| Excluded events | {result.excluded_events} |",
            f"| Private events | {result.private_events} |",
            f"| Group calendars | {max(0, len(result.calendars) - 1)} |",
            f"| Generated at | {generated_at} |",
            "",
            "### Published calendars",
            "",
            "| Calendar | Events | URL |",
            "| --- | ---: | --- |",
            *rows,
            "",
            f"[Open calendar index]({base_url}/)",
            "",
        ]
    )


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


def _validate_calendar(content: str, filename: str) -> None:
    """Reject malformed rendered output before it can replace published files."""
    if not content.endswith("\r\n") or "\n" in content.replace("\r\n", ""):
        raise EventError(f"{filename}: ICSの改行はCRLFである必要があります")
    lines = content.removesuffix("\r\n").split("\r\n")
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        raise EventError(f"{filename}: VCALENDARの開始・終了が不正です")
    if any(len(line.encode("utf-8")) > 75 for line in lines):
        raise EventError(f"{filename}: ICSの行が75 octetを超えています")
    if lines.count("BEGIN:VEVENT") != lines.count("END:VEVENT"):
        raise EventError(f"{filename}: VEVENTの開始・終了が対応していません")


def _replace_output(staged: Path, output: Path, temporary_root: Path) -> None:
    """Replace an output tree, restoring the previous tree if commit fails."""
    previous = temporary_root / "previous"
    had_previous = output.exists() or output.is_symlink()
    if had_previous:
        os.replace(output, previous)
    try:
        os.replace(staged, output)
    except BaseException:
        if had_previous:
            os.replace(previous, output)
        raise
    if had_previous:
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
        else:
            previous.unlink()


def generate(
    issues: Iterable[dict],
    repository: str,
    output: Path,
    *,
    generated_at: datetime | None = None,
) -> GenerationResult:
    events, errors = [], []
    excluded_events = 0
    private_events = 0
    for issue in issues:
        labels = {label["name"] if isinstance(label, dict) else str(label) for label in issue.get("labels", [])}
        if "calendar:private" in labels:
            private_events += 1
            continue
        if "calendar:exclude" in labels:
            excluded_events += 1
            continue
        try:
            events.append(issue_to_event(issue, repository))
        except EventError as exc:
            errors.append(f"Issue #{issue.get('number')}: {exc}")
    if errors:
        raise EventError("\n".join(errors))

    generated_at = generated_at or datetime.now(timezone.utc)
    group_names = sorted({group for event in events for group in event.groups})
    calendar_results = [CalendarResult("All", "calendar.ics", len(events))]
    artifacts = {
        Path("calendar.ics"): render_calendar(
            events, repository, "全体カレンダー"
        )
    }
    for group in group_names:
        selected = [event for event in events if group in event.groups]
        calendar_results.append(
            CalendarResult(group, f"calendars/{group}.ics", len(selected))
        )
        artifacts[Path("calendars") / f"{group}.ics"] = render_calendar(
            selected, repository, group
        )
    artifacts[Path("index.html")] = render_index(
        events,
        repository,
        generated_at,
    )
    for relative_path, content in artifacts.items():
        if relative_path.suffix != ".ics":
            continue
        _validate_calendar(content, relative_path.as_posix())

    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f".{output.name}.tmp-"
    with tempfile.TemporaryDirectory(prefix=prefix, dir=output.parent) as directory:
        temporary_root = Path(directory)
        staged = temporary_root / "next"
        staged.mkdir()
        for relative_path, content in artifacts.items():
            destination = staged / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8", newline="")
        _replace_output(staged, output, temporary_root)
    return GenerationResult(
        generated_at=generated_at,
        published_events=len(events),
        excluded_events=excluded_events,
        private_events=private_events,
        calendars=tuple(calendar_results),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True, help="owner/repository")
    parser.add_argument("--token", help="GitHub token; unnecessary with --input")
    parser.add_argument("--input", type=Path, help="JSON issue fixture instead of GitHub API")
    parser.add_argument("--output", type=Path, default=Path("public"))
    parser.add_argument("--summary", type=Path, help="append a GitHub Actions job summary")
    args = parser.parse_args()
    try:
        if args.input:
            issues = json.loads(args.input.read_text(encoding="utf-8"))
        elif args.token:
            issues = fetch_issues(args.repository, args.token)
        else:
            parser.error("--token or --input is required")
        result = generate(issues, args.repository, args.output)
        if args.summary:
            with args.summary.open("a", encoding="utf-8", newline="\n") as summary:
                summary.write(render_summary(result, args.repository))
    except (ApiError, EventError, json.JSONDecodeError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
