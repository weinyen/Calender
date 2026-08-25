import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected"


class GenerateEndToEndTests(unittest.TestCase):
    def test_issue_form_json_generates_expected_calendars(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "public"
            summary = Path(directory) / "summary.md"
            validation_report = Path(directory) / "validation-report.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "github_calendar.generate",
                    "--repository",
                    "owner/repository",
                    "--input",
                    str(FIXTURES / "issues.json"),
                    "--output",
                    str(output),
                    "--summary",
                    str(summary),
                    "--validation-report",
                    str(validation_report),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / "index.html").exists())
            actual_files = {
                path.relative_to(output) for path in output.rglob("*.ics")
            }
            expected_files = {
                path.relative_to(EXPECTED) for path in EXPECTED.rglob("*.ics")
            }
            self.assertEqual(actual_files, expected_files)
            for relative_path in expected_files:
                with self.subTest(calendar=str(relative_path)):
                    actual = (output / relative_path).read_bytes()
                    self.assertTrue(actual.endswith(b"\r\n"))
                    self.assertNotIn(b"\n", actual.replace(b"\r\n", b""))
                    self.assertEqual(
                        actual.decode("utf-8").replace("\r\n", "\n"),
                        (EXPECTED / relative_path).read_text(encoding="utf-8"),
                    )

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("GitHub Calendar", index)
            self.assertIn("2件</strong><span>公開予定", index)
            self.assertIn("https://owner.github.io/repository/calendar.ics", index)
            self.assertIn("webcal://owner.github.io/repository/calendar.ics", index)
            self.assertIn("webcalで購読", index)
            self.assertIn("calendars/company.ics", index)
            self.assertIn("calendars/development.ics", index)
            self.assertNotIn("Planning meeting", index)

            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("## Calendar generation", summary_text)
            self.assertIn("| Published events | 2 |", summary_text)
            self.assertIn("| Excluded events | 1 |", summary_text)
            self.assertIn("| Private events | 0 |", summary_text)
            self.assertIn("| development | 2 |", summary_text)
            self.assertIn("https://owner.github.io/repository/", summary_text)

            report = json.loads(validation_report.read_text(encoding="utf-8"))
            self.assertEqual(report["version"], 1)
            self.assertEqual(report["checked_issue_numbers"], [101, 102, 103])
            self.assertEqual(report["errors"], [])

    def test_invalid_issue_writes_validation_report_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "issues.json"
            output = root / "public"
            report = root / "validation-report.json"
            source.write_text(
                '[{"number":410,"title":"[予定] invalid","body":"",'
                '"labels":[{"name":"calendar:event"}],'
                '"updated_at":"2026-08-25T00:00:00Z"}]',
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "github_calendar.generate",
                    "--repository",
                    "owner/repository",
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--validation-report",
                    str(report),
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["checked_issue_numbers"], [410])
            self.assertEqual(payload["errors"][0]["issue_number"], 410)
            self.assertIn("開始・終了", payload["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
