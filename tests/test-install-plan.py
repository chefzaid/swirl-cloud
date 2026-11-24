#!/usr/bin/env python3
"""Guards for the read-only rerun plan (./install.sh --plan): each stage is
reported as installed, missing, drifted, failed or skipped from what the host and
cluster show, with what the next run will do, and nothing is changed."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import install_plan  # noqa: E402

CONTRACT = """# pins
DEFAULT_K3S_INSTALL_VERSION=v1.36.6+k3s1
DEFAULT_LONGHORN_CHART_VERSION=1.13.0
DEFAULT_TRAEFIK_CHART_VERSION=42.4.1
DEFAULT_VAULT_CHART_VERSION="0.34.1"
DEFAULT_EXTERNAL_SECRETS_CHART_VERSION=2.15.0
DEFAULT_ARGOCD_CHART_VERSION='10.18.0'
DATASTORE_WAIT_APPS=postgres,redis
PLATFORM_WAIT_APPS=keycloak,grafana
"""
TOOLS = ("ansible", "jq", "kubectl", "helm", "shellcheck", "sqlite3")


def node(name, control=False, version="v1.36.6+k3s1"):
    labels = {"node-role.kubernetes.io/control-plane": "true"} if control else {}
    return {"metadata": {"name": name, "labels": labels}, "status": {"nodeInfo": {"kubeletVersion": version}}}


def pods(*ready):
    return {"items": [{"status": {"phase": "Running" if r is not None else "Pending",
                                  "containerStatuses": [{"ready": bool(r)}]}} for r in ready]}


class Host:
    """What the host and cluster answer; every command the plan runs is recorded."""

    def __init__(self, reachable=True, k3s=True, tools=TOOLS):
        self.reachable, self.k3s, self.tools = reachable, k3s, set(tools)
        self.nodes = [node("cp1", control=True)]
        self.releases = [{"name": "longhorn", "chart": "longhorn-1.13.0", "status": "deployed"},
                         {"name": "traefik", "chart": "traefik-42.4.1", "status": "deployed"},
                         {"name": "vault", "chart": "vault-0.34.1", "status": "deployed"},
                         {"name": "external-secrets", "chart": "external-secrets-2.15.0", "status": "deployed"},
                         {"name": "argocd", "chart": "argo-cd-10.18.0", "status": "deployed"}]
        self.apps = {}
        self.audit = "active"
        self.timer = True
        self.identity = {}
        self.commands = []

    def which(self, tool):
        return f"/usr/bin/{tool}" if tool in self.tools else None

    def run(self, *args, timeout=30):
        self.commands.append(args)
        if args[:2] == ("kubectl", "--request-timeout=10s"):
            return (0, "ok") if self.reachable else (1, "")
        if args[:3] == ("helm", "list", "-A"):
            return 0, json.dumps(self.releases)
        if args[0] == "systemctl" and args[2] == "k3s":
            return 0, "active\n" if self.k3s else "inactive\n"
        if args[0] == "systemctl":
            return 0, "enabled\n" if self.timer else "disabled\n"
        if "get" in args and "nodes" in args:
            return 0, json.dumps({"items": self.nodes})
        if "get" in args and "pods" in args:
            app = next(a for a in args if a.startswith("app="))[4:]
            return 0, json.dumps(self.apps.get(app, pods(True)))
        if args[0].endswith("configure-k3s-audit.sh"):
            return 0, self.audit + "\n"
        if args[:2] == ("python3", str(self.root / "scripts/platform-identity.py")):
            return (0, json.dumps(self.identity)) if self.identity else (1, "")
        raise AssertionError(args)


class Plan(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "repo"
        (self.root / "config").mkdir(parents=True)
        (self.root / "config/platform.env").write_text(CONTRACT)
        self.state = Path(temp.name) / "state"
        self.state.mkdir()

    def plan(self, host, values=None):
        host.root = self.root
        with mock.patch.object(install_plan, "run", host.run), mock.patch.object(install_plan.shutil, "which", host.which):
            rows = install_plan.plan(self.root, values or {}, self.state)
        return {row[0]: row[1:] for row in rows}, rows

    def test_the_contract_is_read_as_data(self):
        contract = install_plan.platform_contract(self.root)
        self.assertEqual("0.34.1", contract["DEFAULT_VAULT_CHART_VERSION"])
        self.assertEqual("10.18.0", contract["DEFAULT_ARGOCD_CHART_VERSION"])
        self.assertNotIn("# pins", " ".join(contract))

    def test_a_fresh_host_plans_a_full_install(self):
        host = Host(reachable=False, k3s=False, tools=("jq",))
        host.timer = False
        stages, rows = self.plan(host)
        self.assertEqual(("missing", "missing: ansible, kubectl, helm, shellcheck, sqlite3", "update"), stages["System prerequisites"])
        self.assertEqual(("missing", "not installed on this host", "install"), stages["K3s control plane"])
        self.assertEqual("K3s control plane", rows[1][0])
        for name in ("Longhorn storage", "Traefik ingress", "Vault", "External Secrets", "Argo CD"):
            self.assertEqual(("skipped", "cluster not reachable", "install"), stages[name])
        for name in ("Data stores", "Platform services", "Odoo"):
            self.assertEqual(("skipped", "cluster not reachable", "install"), stages[name])
        self.assertEqual("installed", stages["Cloudflare DNS, TLS, Access"][0])
        self.assertEqual(("missing", "no nightly timer; local only", "reconcile"), stages["Backups"])
        self.assertNotIn("API audit logging", stages)
        self.assertNotIn("Installation identity", stages)
        self.assertNotIn("Cluster nodes", stages)

    def test_an_installed_platform_plans_reconciles_only(self):
        stages, _ = self.plan(Host())
        self.assertEqual(("installed", "all tools present", "update"), stages["System prerequisites"])
        self.assertEqual(("installed", "v1.36.6+k3s1", "keep"), stages["K3s control plane"])
        self.assertEqual(("installed", "1 registered (1 control plane); planned 1 (1)", "keep"), stages["Cluster nodes"])
        self.assertEqual(("installed", "chart 10.18.0", "reconcile"), stages["Argo CD"])
        self.assertEqual(("installed", "2/2 ready", "reconcile"), stages["Platform services"])
        self.assertEqual(("installed", "writing /var/log/kubernetes/audit/audit.log", "keep"), stages["API audit logging"])
        self.assertEqual(("installed", "nightly timer enabled; local only", "reconcile"), stages["Backups"])

    def test_charts_that_drifted_or_failed_are_named(self):
        host = Host()
        host.releases[1]["chart"] = "traefik-41.0.0"
        host.releases[2]["status"] = "failed"
        host.releases = [r for r in host.releases if r["name"] != "argocd"]
        stages, _ = self.plan(host, {"INSTALL_VAULT_STACK": "false"})
        self.assertEqual(("drift", "chart 41.0.0; pinned 42.4.1", "upgrade"), stages["Traefik ingress"])
        self.assertEqual(("failed", "Helm release failed", "skip"), stages["Vault"])
        self.assertEqual(("installed", "chart 2.15.0", "skip"), stages["External Secrets"])
        self.assertEqual(("missing", "not installed", "install"), stages["Argo CD"])

    def test_k3s_drift_is_reported_but_never_upgraded_by_a_rerun(self):
        host = Host()
        host.nodes = [node("cp1", control=True, version="v1.35.2+k3s1")]
        stages, _ = self.plan(host)
        self.assertEqual(("drift", "v1.35.2+k3s1; pinned v1.36.6+k3s1 (reruns do not upgrade K3s)", "keep"), stages["K3s control plane"])
        stages, _ = self.plan(host, {"INSTALL_K3S": "yes"})
        self.assertEqual("reinstall", stages["K3s control plane"][2])

    def test_planned_nodes_are_counted_against_registered_ones(self):
        host = Host()
        host.nodes = [node("cp1", control=True), node("w1")]
        stages, _ = self.plan(host, {"CONTROL_PLANE_COUNT": "3", "CLUSTER_NODE_COUNT": "5"})
        self.assertEqual(("missing", "2 registered (1 control plane); planned 5 (3)", "enroll 3 node(s)"), stages["Cluster nodes"])

    def test_unready_services_are_failed_and_absent_ones_missing(self):
        host = Host()
        host.apps = {"grafana": pods(True, False), "postgres": {"items": []}, "redis": {"items": []}}
        stages, _ = self.plan(host, {"DEPLOY_PLATFORM_SERVICES": "false"})
        self.assertEqual(("failed", "1/2 ready: not ready grafana", "skip"), stages["Platform services"])
        self.assertEqual(("missing", "0/2 ready", "reconcile"), stages["Data stores"])

    def test_odoo_is_skipped_outside_the_apps_scope(self):
        stages, _ = self.plan(Host(), {"INSTALL_SCOPE": "infra"})
        self.assertEqual(("skipped", "scope infra", "skip"), stages["Odoo"])
        self.assertEqual(("installed", "1/1 ready", "reconcile"), self.plan(Host())[0]["Odoo"])

    def test_audit_logging_states(self):
        for audit, state, action in (("pending-restart", "drift", "configure (restart from Maintenance)"),
                                     ("missing", "missing", "configure (restart from Maintenance)"),
                                     ("", "drift", "configure (restart from Maintenance)")):
            with self.subTest(audit=audit):
                host = Host()
                host.audit = audit
                stage = self.plan(host)[0]["API audit logging"]
                self.assertEqual((state, action), (stage[0], stage[2]))
        host = Host()
        host.audit = ""
        self.assertIn("needs passwordless sudo", self.plan(host)[0]["API audit logging"][1])

    def test_cloudflare_follows_the_exposure(self):
        self.assertEqual(("skipped", "local exposure or disabled", "skip"),
                         self.plan(Host(), {"SERVER_EXPOSURE": "local"})[0]["Cloudflare DNS, TLS, Access"])
        self.assertEqual("reconcile", self.plan(Host(), {"SERVER_EXPOSURE": "local", "CONFIGURE_CLOUDFLARE": "true"})[0]
                         ["Cloudflare DNS, TLS, Access"][2])
        self.assertEqual("skip", self.plan(Host(), {"CONFIGURE_CLOUDFLARE": "false"})[0]["Cloudflare DNS, TLS, Access"][2])

    def test_off_site_backups_are_shown(self):
        self.assertEqual("nightly timer enabled; off-node S3 selected",
                         self.plan(Host(), {"CONFIGURE_OFFSITE_BACKUPS": "true"})[0]["Backups"][1])

    def test_identity_changes_are_migrations_not_reruns(self):
        host = Host()
        host.identity = {"PLATFORM_DOMAIN": "example.com", "ORGANIZATION_NAME": "Example"}
        self.assertEqual(("installed", "recorded; no input values to compare", "keep"), self.plan(host)[0]["Installation identity"])
        self.assertEqual(("installed", "matches the input file", "keep"),
                         self.plan(host, {"PLATFORM_DOMAIN": "example.com"})[0]["Installation identity"])
        stage = self.plan(host, {"PLATFORM_DOMAIN": "example.org", "ORGANIZATION_NAME": "Example"})[0]["Installation identity"]
        self.assertEqual(("drift", "differs: PLATFORM_DOMAIN - renames are migrations, not reruns", "review first"), stage)
        host.identity = {}
        self.assertEqual(("missing", "not recorded yet", "keep"), self.plan(host)[0]["Installation identity"])

    def test_the_last_run_is_shown_with_how_to_resume(self):
        (self.state / "last-run.json").write_text(json.dumps({"title": "Install", "finished": "2026-11-24T10:00:00Z", "code": 3,
                                                              "log": "/var/log/swirl-cloud/install.log"}))
        stage = self.plan(Host())[0]["Last run"]
        self.assertEqual("failed", stage[0])
        self.assertEqual("Install 2026-11-24T10:00:00Z exit 3; log /var/log/swirl-cloud/install.log", stage[1])
        self.assertIn("resume by rerunning", stage[2])
        (self.state / "last-run.json").write_text(json.dumps({"title": "Install", "finished": "x", "code": 0, "log": "l"}))
        self.assertEqual(("installed", "-"), self.plan(Host())[0]["Last run"][::2])

    def test_the_plan_only_reads(self):
        host = Host()
        host.identity = {"PLATFORM_DOMAIN": "example.com"}
        self.plan(host)
        read_only = {"get", "list", "is-active", "is-enabled", "--check", "--discover"}
        for command in host.commands:
            self.assertTrue(read_only & set(command), command)

    def test_without_helm_installed_charts_are_not_listed(self):
        host = Host(tools=("kubectl",))
        stages, _ = self.plan(host)
        self.assertNotIn(("helm", "list", "-A", "-o", "json"), host.commands)
        self.assertEqual(("missing", "not installed", "install"), stages["Vault"])

    def test_render_counts_what_needs_work(self):
        rows = [("K3s control plane", "installed", "v1", "keep"), ("Vault", "drift", "chart 1; pinned 2", "upgrade"),
                ("Argo CD", "missing", "not installed", "install"), ("Data stores", "failed", "1/2 ready", "reconcile")]
        text = install_plan.render(rows)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("Stage"))
        self.assertIn("Vault              drift      upgrade", text)
        self.assertIn("chart 1; pinned 2", text)
        self.assertIn("1 missing, 1 drifted, 1 failed.", text)


class Command(unittest.TestCase):
    def test_an_invalid_input_file_still_plans_with_its_parsed_values(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "install.env"
            path.write_text("SWIRL_CLOUD_INSTALL_INPUTS=1\nSERVER_EXPOSURE=local\nPLATFORM_DOMAIN=not a domain\n")
            path.chmod(0o600)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/install-plan.py"), "--env-file", str(path)],
                                    capture_output=True, text=True, timeout=120,
                                    env={"PATH": "/nonexistent", "HOME": temp, "XDG_STATE_HOME": temp})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.startswith("Input file problems (the plan uses the parsed values):\n  - "))
        self.assertIn("Cloudflare DNS, TLS, Access  skipped", result.stdout)
        self.assertIn("System prerequisites", result.stdout)


if __name__ == "__main__":
    unittest.main()
