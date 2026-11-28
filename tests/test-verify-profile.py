#!/usr/bin/env python3
"""Guards for ./install.sh --verify-profile, on recorded answers: each check
passes for an account that holds the profile and only it, and fails, naming
what it found, for one that can read a secret, write, deploy or administer, or
for a service it cannot reach."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("profile", ROOT / "scripts/verify-profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def results(rows):
    return {(r["service"], r["check"]): r["result"] for r in rows}


class Checks(unittest.TestCase):
    def test_keycloak_groups(self):
        with mock.patch.object(profile, "kubectl", return_value="access-portal\nplatform-readers\n"):
            self.assertEqual({"PASS"}, set(results(profile.keycloak("readonly-test", "staff")).values()))
        with mock.patch.object(profile, "kubectl", return_value="access-portal\nplatform-readers\nplatform-admins\n"):
            row = profile.keycloak("readonly-test", "staff")[0]
        self.assertEqual(("FAIL", "access-portal, platform-admins, platform-readers"), (row["result"], row["observed"]))
        with mock.patch.object(profile, "kubectl", return_value="NO_USER\n"):
            self.assertEqual("FAIL", profile.keycloak("readonly-test", "staff")[0]["result"])

    def test_kubernetes_impersonates_the_account_and_its_groups(self):
        calls = []

        def can_i(command, **kwargs):
            calls.append(command)
            allowed = command[4] == "get" and command[5] in ("pods", "pods/log")
            return mock.Mock(stdout="yes\n" if allowed else "no\n", stderr="")
        with mock.patch.object(profile.subprocess, "run", can_i):
            rows = profile.kubernetes("readonly-test")
        self.assertEqual({"PASS"}, {r["result"] for r in rows})
        self.assertTrue(all("--as=oidc:readonly-test" in c and "--as-group=oidc:platform-readers" in c for c in calls))
        with mock.patch.object(profile.subprocess, "run", lambda command, **kwargs: mock.Mock(stdout="yes\n", stderr="")):
            failed = {r["check"] for r in profile.kubernetes("readonly-test") if r["result"] == "FAIL"}
        self.assertIn("read Secrets", failed)
        self.assertIn("exec into pods", failed)

    def test_argo_cd_rbac(self):
        def can(*args, **kwargs):
            return "Yes\n" if args[11] == "get" else "No\n"
        with mock.patch.object(profile, "kubectl", side_effect=can):
            self.assertEqual({"PASS"}, {r["result"] for r in profile.argocd()})
        with mock.patch.object(profile, "kubectl", return_value="Yes\n"):
            self.assertIn("sync applications", {r["check"] for r in profile.argocd() if r["result"] == "FAIL"})

    def elastic(self, roles, write=False, kibana_all=False):
        user = {"readonly-test": {"roles": roles}}
        privileges = {"cluster": {"manage": False, "manage_security": False},
                      "index": {"kubernetes-logs-*": {"read": True, "write": write, "delete": False, "manage": False}},
                      "application": {"kibana-.kibana": {"*": {"read": True, "all": kibana_all}}}}
        return f"USER {json.dumps(user)}\nPRIVILEGES {json.dumps(privileges)}\n"

    def test_elasticsearch_runs_as_the_account(self):
        with mock.patch.object(profile, "kubectl", return_value=self.elastic(["platform_reader"])) as kubectl:
            self.assertEqual({"PASS"}, {r["result"] for r in profile.elasticsearch("readonly-test")})
        self.assertEqual("readonly-test\n", kubectl.call_args.kwargs["stdin"])
        with mock.patch.object(profile, "kubectl", return_value=self.elastic(["superuser"], write=True, kibana_all=True)):
            failed = {r["check"] for r in profile.elasticsearch("readonly-test") if r["result"] == "FAIL"}
        self.assertEqual({"roles", "write or delete logs", "change anything"}, failed)
        with mock.patch.object(profile, "kubectl", return_value="USER {}\nPRIVILEGES {}\n"):
            self.assertEqual("FAIL", profile.elasticsearch("readonly-test")[0]["result"])

    def test_kafka_ui_role_from_the_real_manifest(self):
        deployment = next(d for d in yaml.safe_load_all((ROOT / "k8s/platform/kafka-ui.yaml").read_text())
                          if d and d["kind"] == "Deployment")
        with mock.patch.object(profile, "kubectl", return_value=json.dumps(deployment)):
            self.assertEqual("PASS", profile.kafka_ui()[0]["result"])
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        env.append({"name": "RBAC_ROLES_1_PERMISSIONS_1_ACTIONS_9", "value": "MESSAGES_PRODUCE"})
        with mock.patch.object(profile, "kubectl", return_value=json.dumps(deployment)):
            self.assertEqual(("FAIL", "MESSAGES_PRODUCE"), (profile.kafka_ui()[0]["result"], profile.kafka_ui()[0]["observed"]))

    def test_gitlab(self):
        answer = lambda found: "noise\nSWIRL_JSON" + json.dumps(found) + "\n"  # noqa: E731
        with mock.patch.object(profile, "kubectl", return_value=answer({"admin": False, "state": "active", "highest": 20})) as kubectl:
            self.assertEqual({"PASS"}, {r["result"] for r in profile.gitlab("readonly-test", "acme")})
        self.assertEqual(("readonly-test", "acme"), kubectl.call_args.args[-2:])
        with mock.patch.object(profile, "kubectl", return_value=answer({"admin": False, "state": "blocked_pending_approval",
                                                                       "highest": 40})):
            failed = {r["check"]: r["observed"] for r in profile.gitlab("readonly-test", "acme") if r["result"] == "FAIL"}
        self.assertEqual({"account approved": "blocked_pending_approval", "highest role in the platform group": "Maintainer"}, failed)
        with mock.patch.object(profile, "kubectl", return_value=answer({"missing": True})):
            self.assertEqual("not signed in yet", profile.gitlab("readonly-test", "acme")[0]["observed"])

    def test_vault_roles(self):
        roles = "ROLE platform-admin " + json.dumps({"data": {"bound_claims": {"groups": "platform-admins"}}}) + "\n"
        with mock.patch.object(profile, "run", side_effect=["root-token\n", "vault-0\n"]), \
                mock.patch.object(profile, "kubectl", return_value=roles) as kubectl:
            self.assertEqual("PASS", profile.vault()[0]["result"])
        self.assertEqual("root-token\n", kubectl.call_args.kwargs["stdin"])
        roles += "ROLE readers " + json.dumps({"data": {"bound_claims": {"groups": ["platform-readers"]}}}) + "\n"
        with mock.patch.object(profile, "run", side_effect=["root-token\n", "vault-0\n"]), \
                mock.patch.object(profile, "kubectl", return_value=roles):
            row = profile.vault()[0]
        self.assertEqual(("FAIL", "readers"), (row["result"], row["observed"]))

    def test_vaultwarden_organizations(self):
        with mock.patch.object(profile, "run", return_value="postgres-0\n"), \
                mock.patch.object(profile, "kubectl", return_value="Read-only demo\n") as kubectl:
            self.assertEqual("PASS", profile.vaultwarden("readonly-test@example.com")[0]["result"])
        self.assertTrue(kubectl.call_args.kwargs["stdin"].startswith("readonly-test@example.com\nSELECT"))
        with mock.patch.object(profile, "run", return_value="postgres-0\n"), \
                mock.patch.object(profile, "kubectl", return_value="Read-only demo\nSwirl\n"):
            self.assertEqual(("FAIL", "Read-only demo, Swirl"), tuple(profile.vaultwarden("x@example.com")[0][k] for k in ("result", "observed")))


class Report(unittest.TestCase):
    def test_an_unreachable_service_fails_without_stopping_the_others(self):
        def broken():
            raise RuntimeError("deployments.apps \"grafana\" not found")
        rows = profile.verify("readonly-test", [("Grafana", broken), ("Vault", lambda: [profile.row("Vault", "roles", "none", "none")])])
        self.assertEqual([("Grafana", "FAIL"), ("Vault", "PASS")], [(r["service"], r["result"]) for r in rows])
        text = profile.render(rows, "readonly-test")
        self.assertIn('FAIL  Grafana  reachable: deployments.apps "grafana" not found (expected yes)', text)
        self.assertTrue(text.endswith("1 of 2 checks as the profile requires; 1 failed.\n"))
        markdown = profile.render(rows, "readonly-test", markdown=True)
        self.assertIn("| Vault | roles | none | none | PASS |", markdown)


if __name__ == "__main__":
    unittest.main()
