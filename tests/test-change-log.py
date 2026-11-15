#!/usr/bin/env python3
"""Guards for the change records: production Argo CD syncs and the root Application
are listed with their rollback, other environments are not; grants keep their
approver; entries without one are flagged; a source that fails is reported, not
fatal; text cannot break the table."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("change_log", ROOT / "scripts/change-log.py")
change_log = importlib.util.module_from_spec(spec)
spec.loader.exec_module(change_log)
SINCE = datetime(2026, 11, 1, tzinfo=timezone.utc)

APPLICATIONS = {"items": [
    {"metadata": {"name": "swirl-cloud", "labels": {}}, "status": {"history": [
        {"id": 1, "revision": "aaaaaaaaaaaaaaaa", "deployedAt": "2026-10-20T10:00:00Z", "initiatedBy": {"automated": True}},
        {"id": 2, "revision": "bbbbbbbbbbbbbbbb", "deployedAt": "2026-11-03T10:00:00Z", "initiatedBy": {"automated": True}}]}},
    {"metadata": {"name": "shop-prod", "labels": {"swirl-cloud/environment": "prod"}}, "status": {"history": [
        {"id": 7, "revision": "cccccccccccccccc", "deployedAt": "2026-11-05T09:00:00Z", "initiatedBy": {"username": "ada"}}]}},
    {"metadata": {"name": "shop-int", "labels": {"swirl-cloud/environment": "int"}}, "status": {"history": [
        {"id": 3, "revision": "dddd", "deployedAt": "2026-11-06T09:00:00Z"}]}}]}
GRANTS = {"hits": {"hits": [{"_source": {"user": "bob", "reason": "fix | outage", "requested_by": "ada",
                                         "approved_by": "carol", "granted_at": "2026-11-04T08:00:00+00:00", "state": "expired"}}]}}


def fake_run(*command, stdin=None):
    if command[0] == "git":
        return "0123456789abcdef\x1fZaid\x1f2026-11-02T12:00:00+01:00\x1fGive every workload a priority\n"
    if "applications.argoproj.io" in command:
        return json.dumps(APPLICATIONS)
    if "deploy/elasticsearch" in command:
        return json.dumps(GRANTS)
    raise RuntimeError("sudo: a password is required")


class ChangeLogTest(unittest.TestCase):
    def test_sources_merge_in_time_order(self):
        with mock.patch.object(change_log, "run", side_effect=fake_run):
            rows, errors = change_log.collect(SINCE)
        self.assertEqual([], errors)  # Unreadable installer runs are skipped, not an error.
        self.assertEqual(["git", "argocd", "grants", "argocd"], [row["source"] for row in rows])
        self.assertEqual("2026-11-02T11:00:00Z", rows[0]["at"])
        root, prod = rows[1], rows[3]
        self.assertEqual(("Deployed swirl-cloud", "argocd app rollback swirl-cloud 1"), (root["change"], root["way_back"]))
        self.assertEqual(("ada", "no earlier sync recorded"), (prod["who"], prod["way_back"]))
        self.assertFalse(any("shop-int" in row["change"] for row in rows))
        self.assertEqual(("ada", "carol"), (rows[2]["who"], rows[2]["approved_by"]))

    def test_markdown_flags_missing_approvers_and_escapes_text(self):
        with mock.patch.object(change_log, "run", side_effect=fake_run):
            rows, errors = change_log.collect(SINCE)
        text = change_log.render(rows, ["Installer runs: unreadable"], SINCE)
        self.assertIn("4 change(s); 3 without a recorded approver", text)
        self.assertIn("fix / outage", text)
        self.assertEqual(text.count("**none recorded**"), 3)
        self.assertIn("## Not collected", text)

    def test_a_failing_source_is_reported(self):
        def broken(*command, stdin=None):
            if "applications.argoproj.io" in command:
                raise RuntimeError("kubectl: connection refused")
            return fake_run(*command, stdin=stdin)
        with mock.patch.object(change_log, "run", side_effect=broken):
            rows, errors = change_log.collect(SINCE)
        self.assertEqual(["Argo CD: kubectl: connection refused"], errors)
        self.assertEqual(["git", "grants"], [row["source"] for row in rows])


if __name__ == "__main__":
    unittest.main()
