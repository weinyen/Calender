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
            self.assertIn("calendars/company.ics", index)
            self.assertIn("calendars/development.ics", index)
            self.assertNotIn("Planning meeting", index)


if __name__ == "__main__":
    unittest.main()
