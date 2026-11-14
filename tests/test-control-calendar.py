#!/usr/bin/env python3
"""Guards for the control calendar: the catalog is valid, a duty never done or past
its period is overdue and one done within it is not, recording keeps other duties,
and the status report names each overdue duty with how to do it."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import duties  # noqa: E402

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


class ControlCalendarTest(unittest.TestCase):
    def test_catalog(self):
        catalog = duties.catalog()
        self.assertIn("access-review", catalog)
        for duty in catalog.values():
            self.assertTrue(duty["name"] and duty["done_by"] and duty["evidence"], duty["id"])
            self.assertGreater(duty["every_days"], 0)

    def test_overdue(self):
        catalog = duties.catalog()
        rows = {duty["id"]: days for duty, _, days in duties.status(catalog, {
            "access-review": {"done": "2026-10-01T00:00:00Z"},   # within 92 days
            "game-day": {"done": "2026-09-01T00:00:00Z"},        # 31-day period passed
        }, NOW)}
        self.assertIsNone(rows["access-review"])
        self.assertEqual(43, rows["game-day"])
        self.assertEqual(-1, rows["restore-drill"])               # never done

    def test_record_keeps_other_duties(self):
        stored = {"completions": json.dumps({"game-day": {"done": "2026-11-01T00:00:00Z", "by": "ops", "note": ""}})}
        applied = []
        def kubectl(*args, stdin=None):
            if args[2] == "get":
                return stored["completions"]
            applied.append(json.loads(stdin))
            return ""
        with mock.patch.object(duties, "kubectl", side_effect=kubectl):
            entry = duties.record("access-review", "sheets", by="ada", when=NOW)
            with self.assertRaises(ValueError):
                duties.record("not-a-duty")
        completions = json.loads(applied[0]["data"]["completions"])
        self.assertEqual({"game-day", "access-review"}, set(completions))
        self.assertEqual({"done": "2026-11-14T00:00:00Z", "by": "ada", "note": "sheets"}, entry)
        self.assertEqual(("infra", "swirl-cloud-duties"), (applied[0]["metadata"]["namespace"], applied[0]["metadata"]["name"]))

    def test_status_report_names_overdue_duties(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts/verify-platform.py")
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        report = verify.Report()
        with mock.patch.object(duties, "recorded", return_value={}):
            verify.check_duties(report)
        result = report.results[0]
        self.assertEqual(("WARN", "Periodic duties"), (result["status"], result["check"]))
        self.assertTrue(any(line.startswith("restore-drill - never done - ") for line in result["details"]))


if __name__ == "__main__":
    unittest.main()
