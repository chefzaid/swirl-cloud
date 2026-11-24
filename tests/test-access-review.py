#!/usr/bin/env python3
"""Guards for the access review's Vault sign-in roles: OIDC roles show the
groups they admit and Kubernetes roles their service accounts, with policies,
and an OIDC role open to any account of the realm is a finding."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review", ROOT / "scripts/access-review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
OUTPUT = "\n".join([
    "##ROLE oidc platform-admin", '{"data":{"bound_claims":{"groups":"platform-admins"},"token_policies":["platform-admin"]}}',
    "##ROLE oidc loose", '{"data":{"token_policies":["default"]}}',
    "##ROLE kubernetes external-secrets-role",
    '{"data":{"bound_service_account_namespaces":["infra"],"bound_service_account_names":["external-secrets"],"token_policies":["external-secrets"]}}'])


class VaultRoles(unittest.TestCase):
    def test_roles_and_findings(self):
        with mock.patch.object(review.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="token\n")), \
                mock.patch.object(review, "run", side_effect=["vault-0\n", OUTPUT]) as calls:
            roles = review.vault_roles()
        self.assertEqual("token\n", calls.call_args_list[1].kwargs["stdin"])
        self.assertEqual([("oidc", "platform-admin", "groups=platform-admins", ["platform-admin"]),
                          ("oidc", "loose", "any account of the realm", ["default"]),
                          ("kubernetes", "external-secrets-role", "infra/external-secrets", ["external-secrets"])],
                         [(r["mount"], r["role"], r["admits"], r["policies"]) for r in roles])
        found = review.findings({"identities": [], "argocd_admin_enabled": False, "keycloak_realms": {}, "vault_roles": roles,
                                 "gitlab_requires_2fa": True})
        self.assertEqual(["Vault OIDC role loose grants default to any account of the realm; bind it to a group."], found)


if __name__ == "__main__":
    unittest.main()
