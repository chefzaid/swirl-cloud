#!/usr/bin/env python3
"""Guards for the read-only profile (TODO §51) on the rendered platform: every
place that names `platform-readers` grants a read-only role, and nothing that
deploys, changes or reveals secrets admits it: no Kubernetes binding but
`view`, Argo CD `role:readonly` only, Grafana Viewer, a Kafka UI role without a
write action, no Vault OIDC role and no administrator-only sign-in middleware."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "platform-readers"
VALUES = {"publicDomain": "example.com", "internalDnsZone": "internal.example.com",
          "gitopsRepositoryURL": "https://example.com/swirl-cloud.git", "cloudflareAccessTeamName": "team",
          "appsEnabled": True, "deschedulerEnabled": True, "organizationName": "Example", "organizationSlug": "example"}
KAFKA_READ_ACTIONS = {"VIEW", "MESSAGES_READ", "ANALYSIS_VIEW"}


@unittest.skipUnless(shutil.which("helm"), "helm is required")
class RenderedProfile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as values:
            json.dump(VALUES, values)
            values.flush()
            result = subprocess.run(["helm", "template", "swirl-cloud", str(ROOT / "k8s"), "--namespace", "infra", "--skip-tests",
                                     "--values", values.name], capture_output=True, text=True, check=True)
        cls.docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def find(self, kind, name):
        return next(doc for doc in self.docs if doc["kind"] == kind and doc["metadata"]["name"] == name)

    def test_kubernetes_grants_the_profile_view_only(self):
        bindings = [doc for doc in self.docs if doc["kind"] in ("ClusterRoleBinding", "RoleBinding")
                    and any(subject.get("name") == f"oidc:{PROFILE}" for subject in doc.get("subjects", []))]
        self.assertEqual([("ClusterRole", "view")], [(doc["roleRef"]["kind"], doc["roleRef"]["name"]) for doc in bindings])
        # The built-in view ClusterRole leaves out Secrets; no aggregated role here adds them to it.
        for doc in self.docs:
            if doc["kind"] == "ClusterRole" and (doc["metadata"].get("labels") or {}).get("rbac.authorization.k8s.io/aggregate-to-view"):
                for rule in doc.get("rules", []):
                    self.assertNotIn("secrets", rule.get("resources", []), doc["metadata"]["name"])
                    self.assertFalse(set(rule.get("verbs", [])) - {"get", "list", "watch"}, doc["metadata"]["name"])

    def test_kafka_ui_role_only_views(self):
        env = {e["name"]: e.get("value") for e in self.find("Deployment", "kafka-ui")["spec"]["template"]["spec"]["containers"][0]["env"]}
        roles = {key.split("_")[2] for key in env if re.fullmatch(r"RBAC_ROLES_\d+_NAME", key)}
        readers = next(index for index in roles if env[f"RBAC_ROLES_{index}_NAME"] == PROFILE)
        self.assertEqual(PROFILE, env[f"RBAC_ROLES_{readers}_SUBJECTS_0_VALUE"])
        actions = {value for key, value in env.items() if re.fullmatch(rf"RBAC_ROLES_{readers}_PERMISSIONS_\d+_ACTIONS_\d+", key)}
        self.assertTrue(actions)
        self.assertLessEqual(actions, KAFKA_READ_ACTIONS)
        resources = {value for key, value in env.items() if re.fullmatch(rf"RBAC_ROLES_{readers}_PERMISSIONS_\d+_RESOURCE", key)}
        self.assertNotIn("applicationconfig", resources)
        for index in roles - {readers}:
            subjects = {value for key, value in env.items() if re.fullmatch(rf"RBAC_ROLES_{index}_SUBJECTS_\d+_VALUE", key)}
            self.assertNotIn(PROFILE, subjects)

    def test_grafana_maps_the_profile_to_viewer(self):
        env = {e["name"]: e.get("value") for e in self.find("Deployment", "grafana")["spec"]["template"]["spec"]["containers"][0]["env"]}
        path = env["GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_PATH"]
        branches = [branch.strip() for branch in re.split(r"\|\|(?![^()]*\))", path)]
        granting = [branch for branch in branches if PROFILE in branch]
        self.assertEqual(1, len(granting))
        self.assertTrue(granting[0].endswith("'Viewer'"), granting[0])
        self.assertNotIn(PROFILE, branches[0])
        self.assertEqual("true", env["GF_AUTH_GENERIC_OAUTH_ROLE_ATTRIBUTE_STRICT"])

    def test_no_admin_only_middleware_admits_the_profile(self):
        for doc in self.docs:
            if doc["kind"] == "Middleware" and "oauth2-proxy" in str((doc["spec"].get("forwardAuth") or {}).get("address")):
                address = doc["spec"]["forwardAuth"]["address"]
                if doc["metadata"]["name"] in ("platform-sso",):
                    self.assertNotIn(PROFILE, address)

    def test_kibana_signs_readers_in_as_a_read_only_user(self):
        sync = next(doc for doc in self.docs if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "kibana-keycloak-user-sync")
        mapping = re.search(r"const groupRoles = (\[.*?\]);", sync["data"]["sync.mjs"])[1]
        self.assertEqual([["platform-admins", ["superuser"]], [PROFILE, ["platform_reader"]]], json.loads(mapping))
        bootstrap = next(doc for doc in self.docs if doc["kind"] == "Job" and doc["metadata"]["name"] == "elasticsearch-security-bootstrap")
        text = "\n".join(c.get("args", [""])[0] if c.get("args") else " ".join(c.get("command", []))
                         for c in bootstrap["spec"]["template"]["spec"]["containers"])
        body = re.search(r"--data-binary '(\{[^']*\})' \\\n\s*\"\$\{endpoint\}/_security/role/platform_reader\"", text)
        self.assertTrue(body, "the platform_reader role is not created")
        role = json.loads(body[1])
        for index in role["indices"]:
            self.assertLessEqual(set(index["privileges"]), {"read", "view_index_metadata"})
        self.assertEqual([{"application": "kibana-.kibana", "privileges": ["read"], "resources": ["*"]}], role["applications"])
        self.assertLessEqual(set(role["cluster"]), {"monitor"})
        self.assertEqual([], role["run_as"])
        ingress = next(doc for doc in self.docs if doc["kind"] == "Ingress" and doc["metadata"]["name"] == "kibana-ingress")
        self.assertIn("infra-platform-access-readers@kubernetescrd",
                      ingress["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"])

    def test_gitlab_holds_readers_at_reporter(self):
        bootstrap = self.find("ConfigMap", "gitlab-sso-admin-bootstrap")["data"]["readers.sh"]
        self.assertIn("reporter = Gitlab::Access::REPORTER", bootstrap)
        self.assertIn("next if user.admin?", bootstrap)
        self.assertNotRegex(bootstrap, r"admin: true|update!\(admin|Gitlab::Access::(DEVELOPER|MAINTAINER|OWNER)")
        self.assertIn("member.access_level > reporter", bootstrap)
        pod = self.find("Deployment", "gitlab")["spec"]["template"]["spec"]
        volume = next(v for v in pod["volumes"] if v["name"] == "platform-readers-members")
        self.assertEqual({"name": "platform-readers-members", "optional": True}, volume["configMap"])
        mount = next(m for m in pod["containers"][0]["volumeMounts"] if m["name"] == "platform-readers-members")
        self.assertTrue(mount["readOnly"])
        self.assertNotIn("subPath", mount, "a subPath mount would never see the sync's updates")
        self.assertIn("/opt/platform/readers-sync", " ".join(pod["containers"][0]["lifecycle"]["postStart"]["exec"]["command"]))

    def test_the_sync_may_write_only_the_readers_list(self):
        role = self.find("Role", "platform-readers-members-writer")
        for rule in role["rules"]:
            self.assertEqual(["configmaps"], rule["resources"])
            if rule["verbs"] != ["create"]:
                self.assertEqual(["platform-readers-members"], rule["resourceNames"])
                self.assertLessEqual(set(rule["verbs"]), {"get", "update"})
        binding = self.find("RoleBinding", "kibana-keycloak-user-sync")
        self.assertEqual("platform-readers-members-writer", binding["roleRef"]["name"])
        for kind in ("Job", "CronJob"):
            doc = self.find(kind, "kibana-keycloak-user-sync")
            spec = doc["spec"]["jobTemplate"]["spec"] if kind == "CronJob" else doc["spec"]
            pod = spec["template"]["spec"]
            self.assertEqual("kibana-keycloak-user-sync", pod["serviceAccountName"])
        others = [doc for doc in self.docs if doc["kind"] in ("RoleBinding", "ClusterRoleBinding")
                  and any(subject.get("name") == "kibana-keycloak-user-sync" for subject in doc.get("subjects", []))]
        self.assertEqual(["kibana-keycloak-user-sync"], [doc["metadata"]["name"] for doc in others])

    def test_the_test_account_holds_only_the_profile_and_the_portal(self):
        script = self.find("ConfigMap", "keycloak-sso-reconciler")["data"]["reconcile.sh"]
        start = re.search(r"^( *)if \[ -n \"\$\{READONLY_TEST_PASSWORD:-\}\" \]; then$", script, re.M)
        block = script[start.start():]
        block = block[:block.index(f"\n{start[1]}fi\n")]
        self.assertIn("for readonly_group in platform-readers access-portal; do", block)
        self.assertNotIn("platform-admins", block)
        self.assertNotIn("role-mappings", block)
        secret = self.find("ExternalSecret", "readonly-test-credentials")
        self.assertEqual("readonly-test-password", secret["spec"]["dataFrom"][0]["sourceRef"]["generatorRef"]["name"])


class ProfileSources(unittest.TestCase):
    def test_argo_cd_gives_the_profile_read_only(self):
        policy = yaml.safe_load((ROOT / "config/argocd-values.yaml").read_text())["configs"]["rbac"]["policy.csv"]
        lines = [line.strip() for line in policy.splitlines() if PROFILE in line]
        self.assertEqual([f"g, {PROFILE}, role:readonly"], lines)

    def test_vault_has_no_role_for_the_profile(self):
        script = (ROOT / "scripts/configure-vault.sh").read_text()
        self.assertNotIn(PROFILE, script)
        for claims in re.findall(r"bound_claims:\s*\{([^}]*)\}", script):
            self.assertNotIn(PROFILE, claims)

    def test_the_reconciler_creates_the_profile_group(self):
        script = (ROOT / "k8s/platform/keycloak-sso.yaml").read_text()
        loop = re.search(r"for access_group in ([a-z0-9 -]+); do", script)[1].split()
        self.assertIn(PROFILE, loop)

    def test_portal_opens_only_viewable_services_to_the_profile(self):
        rules = json.loads((ROOT / "k8s/files/portal-access.json").read_text())
        opened = {name for name, groups in rules["groups"].items() if PROFILE in ([groups] if isinstance(groups, str) else groups)}
        self.assertTrue({"Grafana", "Argo CD", "Kafka UI"} <= opened)
        self.assertFalse(opened & {"Portainer", "DBGate", "Longhorn", "Vault", "Keycloak", "Mailpit"})


if __name__ == "__main__":
    unittest.main()
