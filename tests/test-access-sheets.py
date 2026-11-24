#!/usr/bin/env python3
"""Guards for the per-group access review sheets: one sheet per access group of
the platform realm, its members only, time-limited grants shown with their
approver and end, permanent members marked, and the owner named."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("access_sheets", ROOT / "scripts/lib/access_sheets.py")
sheets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sheets)


def person(name, groups, realm="staff", active=True):
    return {"system": "Keycloak", "scope": realm, "identity": name, "email": f"{name}@example.com", "active": active,
            "groups": groups, "last_activity": ""}


class AccessSheets(unittest.TestCase):
    def test_one_sheet_per_group_with_grants(self):
        review = {"generated_at": "2026-11-24T10:00", "identities": [
            person("ada", ["/platform-admins", "/access-grafana"]), person("bob", ["/access-grafana"]),
            person("eve", ["/access-grafana"], realm="master"), person("dan", ["/developers"]),
            {"system": "GitLab", "scope": "gitlab", "identity": "ada", "email": "", "active": True, "groups": ["/access-grafana"], "last_activity": ""}]}
        grants = [{"user": "bob", "group": "access-grafana", "kind": "role", "granted_at": "2026-10-01T00:00:00+00:00",
                   "expires_at": "2027-03-31T00:00:00+00:00", "approved_by": "ada"},
                  {"user": "ada", "granted_at": "2026-11-24T08:00:00+00:00", "expires_at": "2026-11-24T12:00:00+00:00", "approved_by": "bob"}]
        result = sheets.sheets(review, grants, "staff", {"access-grafana": "Grafana owner <g@example.com>"})
        self.assertEqual(["access-grafana", "platform-admins"], sorted(result))
        grafana = result["access-grafana"]
        self.assertIn("for Grafana owner <g@example.com>", grafana)
        self.assertIn("| bob | bob@example.com | active | grant since 2026-10-01 | ada | 2027-03-31 |", grafana)
        self.assertIn("| ada | ada@example.com | active | permanent member |", grafana)
        self.assertNotIn("eve", grafana)
        self.assertIn("grant since 2026-11-24", result["platform-admins"])
        self.assertIn("for the platform administrators", result["platform-admins"])

    def test_owner_file_lists_every_reconciled_group(self):
        reconciled = (ROOT / "k8s/platform/keycloak-sso.yaml").read_text().split("for access_group in ", 1)[1].split("; do", 1)[0].split()
        self.assertEqual(set(reconciled) | {"platform-admins"}, set(sheets.owners()))

    def test_tickets_only_for_owners_with_an_email(self):
        requests, skipped = sheets.ticket_requests({"access-grafana": "sheet A", "access-argocd": "sheet B"},
                                                   {"access-grafana": "Grafana Owner <g@example.com>".split("<")[1].rstrip(">"), "access-argocd": "Ops team"},
                                                   "20261124T100000Z")
        self.assertEqual([{"group": "access-grafana", "owner": "g@example.com", "title": "Access review 2026-11-24: access-grafana",
                           "body": "sheet A"}], requests)
        self.assertEqual(["access-argocd: no owner email in config/access-owners.yaml"], skipped)
        self.assertNotIn("g@example.com", sheets.ZAMMAD_TICKETS)


if __name__ == "__main__":
    unittest.main()
