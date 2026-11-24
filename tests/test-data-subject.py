#!/usr/bin/env python3
"""Guards for data subject exports: every source is collected or reported as
not collected without stopping the others, secrets never reach the export,
the files are private, and the recorded request holds no email address."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dsr", ROOT / "scripts/data-subject.py")
dsr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dsr)

ACCOUNT = [{"account": {"id": "u1", "username": "ada", "email": "ada@example.com"},
            "credentials": [{"type": "password", "createdDate": 1, "secretData": "x", "credentialData": "y"}], "groups": []}]


def rows(database, sql, email):
    if database == "odoo":
        raise RuntimeError("database \"odoo\" does not exist")
    return [{"id": 3, "email": email, "password": "$2a$hash", "login": "ada"}]


class DataSubject(unittest.TestCase):
    def collect(self):
        searches = []
        def search(index, query):
            searches.append((index, query))
            return [{"keycloak": {"type": "LOGIN", "username": "ada"}}]
        with mock.patch.object(dsr, "identity", return_value={"KEYCLOAK_REALM": "staff", "PUBLIC_DOMAIN": "example.com"}), \
                mock.patch.object(dsr, "keycloak", return_value=ACCOUNT), mock.patch.object(dsr, "elasticsearch", side_effect=search), \
                mock.patch.object(dsr, "postgres_rows", side_effect=rows):
            return dsr.collect("ada@example.com"), searches

    def test_sources_are_collected_or_reported(self):
        found, searches = self.collect()
        self.assertNotIn("secretData", json.dumps(found))
        self.assertNotIn("credentialData", json.dumps(found))
        self.assertNotIn("$2a$hash", json.dumps(found))
        self.assertEqual("password", found["keycloak"][0]["credentials"][0]["type"])
        self.assertIn("does not exist", found["odoo-contacts"]["error"])
        self.assertEqual("ada@example.com", found["zammad-users"][0]["email"])
        self.assertEqual({"identity-audit-*", "access-grants"}, {index for index, _ in searches})
        self.assertIn({"terms": {"keycloak.username": ["ada"]}}, searches[0][1]["bool"]["should"])

    def test_export_files_are_private_and_summarised(self):
        found, _ = self.collect()
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name) / "out"
            dsr.write_export(directory, "ada@example.com", found, "DSR-1", datetime(2026, 11, 24, tzinfo=timezone.utc))
            summary = (directory / "SUMMARY.md").read_text()
            self.assertIn("Answer due by 2026-12-24", summary)
            self.assertIn("| odoo-contacts | not collected:", summary)
            self.assertEqual("0o700", oct(directory.stat().st_mode & 0o777))
            self.assertTrue(all(oct(path.stat().st_mode & 0o777) == "0o600" for path in directory.iterdir()))

    def test_queries_take_the_email_as_a_variable(self):
        for queries in dsr.SQL.values():
            for sql in queries.values():
                self.assertIn(":'email'", sql)
                self.assertNotIn("{", sql)


if __name__ == "__main__":
    unittest.main()
