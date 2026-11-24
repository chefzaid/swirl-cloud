#!/usr/bin/env python3
"""Guards for the installation acceptance record: every item must pass, duties
count only within 30 days, and the record carries the handover list and the
signature lines."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acceptance", ROOT / "scripts/acceptance-record.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class AcceptanceRecord(unittest.TestCase):
    def test_duties_count_only_when_recent(self):
        completions = {"restore-drill": {"done": "2026-11-20T10:00:00Z", "by": "ada", "note": "disposable host"},
                       "recovery-kit": {"done": "2026-09-01T10:00:00Z", "by": "ada"}}
        self.assertEqual((True, "done 2026-11-20 by ada (disposable host)"), acceptance.duty("restore-drill", completions, NOW))
        self.assertFalse(acceptance.duty("recovery-kit", completions, NOW)[0])
        self.assertEqual((False, "not recorded"), acceptance.duty("game-day", completions, NOW))

    def test_record_needs_every_item(self):
        items = [("Platform verification (strict)", True, ["all 30 checks passed"]), ("Sign-in through Keycloak", False, "the sign-in probe fails")]
        text, passed = acceptance.record("Example SAS", items, NOW)
        self.assertFalse(passed)
        self.assertIn("| Sign-in through Keycloak | **not passed** | the sign-in probe fails |", text)
        self.assertIn("Accepted: no", text)
        self.assertIn("the installer keeps no copy", text)
        self.assertIn("| Customer (Example SAS) | | | |", text)
        _, passed = acceptance.record("Example SAS", items[:1], NOW)
        self.assertTrue(passed)


if __name__ == "__main__":
    unittest.main()
