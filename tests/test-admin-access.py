#!/usr/bin/env python3
"""Guards for time-limited access: end dates are the end of a day at most a year
away, access groups are only access-* groups, and older administrator grants
without a kind still read as platform administration."""
from datetime import date, datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("admin_access", ROOT / "scripts/admin-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class AdminAccess(unittest.TestCase):
    def test_end_of_day_within_a_year(self):
        today = date(2026, 11, 15)
        self.assertEqual(access.end_of_day("2026-11-15", today), datetime(2026, 11, 16, tzinfo=timezone.utc))
        self.assertEqual(access.end_of_day("2027-11-16", today), datetime(2027, 11, 17, tzinfo=timezone.utc))
        for bad in ("2026-11-14", "2027-11-17", "31/12/2026"):
            with self.assertRaises(SystemExit):
                access.end_of_day(bad, today)

    def test_access_groups_and_old_records(self):
        self.assertTrue(access.ACCESS_GROUP.fullmatch("access-grafana"))
        for bad in ("platform-admins", "access-", "Access-GitLab", "access-x;rm"):
            self.assertFalse(access.ACCESS_GROUP.fullmatch(bad))
        old = {"user": "alice", "approved_by": "bob"}
        self.assertEqual((access.kind_of(old), access.group_of(old)), ("admin", "platform-admins"))
        self.assertIn("guest account, sponsor erin", access.describe({"kind": "guest", "sponsor": "erin"}))
        self.assertIn("access-grafana (role)", access.describe({"kind": "role", "group": "access-grafana"}))


if __name__ == "__main__":
    unittest.main()
