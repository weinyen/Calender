import json
import re
import tempfile
import unittest
from pathlib import Path

from github_calendar.generate import generate


FIXTURES = Path(__file__).parent / "fixtures"
UTC_DATE_TIME = re.compile(r"^\d{8}T\d{6}Z$")
DATE = re.compile(r"^\d{8}$")


def unfold(data: bytes) -> list[str]:
    """Validate physical lines and return unfolded RFC 5545 content lines."""
    if not data.endswith(b"\r\n"):
        raise AssertionError("ICS must end with CRLF")
    if b"\n" in data.replace(b"\r\n", b""):
        raise AssertionError("ICS contains a bare LF")

    physical_lines = data[:-2].split(b"\r\n")
    if any(len(line) > 75 for line in physical_lines):
        raise AssertionError("ICS contains a physical line over 75 octets")

    logical_lines: list[str] = []
    for raw_line in physical_lines:
        line = raw_line.decode("utf-8")
        if line.startswith((" ", "\t")):
            if not logical_lines:
                raise AssertionError("ICS starts with an orphan continuation line")
            logical_lines[-1] += line[1:]
        else:
            logical_lines.append(line)
    return logical_lines


class IcsComplianceTests(unittest.TestCase):
    def test_generated_calendars_satisfy_rfc5545_invariants(self):
        issues = json.loads((FIXTURES / "issues.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            generate(issues, "owner/repository", output)

            calendars = sorted(output.rglob("*.ics"))
            self.assertEqual(len(calendars), 3)
            for calendar in calendars:
                with self.subTest(calendar=str(calendar.relative_to(output))):
                    self.assert_calendar(calendar.read_bytes())

    def assert_calendar(self, data: bytes):
        lines = unfold(data)
        self.assertEqual(lines[0], "BEGIN:VCALENDAR")
        self.assertEqual(lines[-1], "END:VCALENDAR")
        self.assertEqual(lines.count("BEGIN:VCALENDAR"), 1)
        self.assertEqual(lines.count("END:VCALENDAR"), 1)
        self.assertIn("VERSION:2.0", lines)
        self.assertIn("PRODID:-//GitHub Calendar//Calender//JA", lines)

        events: list[list[str]] = []
        current: list[str] | None = None
        for line in lines[1:-1]:
            if line == "BEGIN:VEVENT":
                self.assertIsNone(current)
                current = []
            elif line == "END:VEVENT":
                self.assertIsNotNone(current)
                events.append(current or [])
                current = None
            elif current is not None:
                current.append(line)
        self.assertIsNone(current)

        seen_uids: set[str] = set()
        for event in events:
            properties: dict[str, list[str]] = {}
            for line in event:
                name, separator, value = line.partition(":")
                self.assertEqual(separator, ":", line)
                properties.setdefault(name, []).append(value)

            for required in ("UID", "DTSTAMP", "SUMMARY"):
                self.assertEqual(len(properties.get(required, [])), 1, required)
            self.assertRegex(properties["DTSTAMP"][0], UTC_DATE_TIME)

            uid = properties["UID"][0]
            self.assertNotIn(uid, seen_uids)
            seen_uids.add(uid)

            if "DTSTART;VALUE=DATE" in properties:
                self.assertEqual(len(properties["DTSTART;VALUE=DATE"]), 1)
                self.assertEqual(len(properties.get("DTEND;VALUE=DATE", [])), 1)
                self.assertRegex(properties["DTSTART;VALUE=DATE"][0], DATE)
                self.assertRegex(properties["DTEND;VALUE=DATE"][0], DATE)
            else:
                self.assertEqual(len(properties.get("DTSTART", [])), 1)
                self.assertEqual(len(properties.get("DTEND", [])), 1)
                self.assertRegex(properties["DTSTART"][0], UTC_DATE_TIME)
                self.assertRegex(properties["DTEND"][0], UTC_DATE_TIME)


if __name__ == "__main__":
    unittest.main()
