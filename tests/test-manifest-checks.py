#!/usr/bin/env python3
"""Guards for the manifest policy checks themselves (scripts/lib/manifest_checks.py
and access_checks.py), on small synthetic manifests: each check accepts a
compliant resource and rejects the specific mistake it exists for, so a check
that silently stopped matching would fail here instead of passing every render."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import access_checks  # noqa: E402
import manifest_checks as checks  # noqa: E402


def container(**extra):
    base = {"name": "app", "image": "registry.example.com/app:1@sha256:" + "a" * 64,
            "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "1", "memory": "64Mi"}}}
    base.update(extra)
    return base


def deployment(name="app", **pod):
    spec = {"automountServiceAccountToken": False, "priorityClassName": "platform-standard", "containers": [container()]}
    spec.update(pod)
    return {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": name, "namespace": "infra"},
            "spec": {"template": {"spec": spec}}}


class WorkloadPolicy(unittest.TestCase):
    def test_a_compliant_workload_passes(self):
        checks.workload_policy(deployment())
        cron = {"apiVersion": "batch/v1", "kind": "CronJob", "metadata": {"name": "job", "namespace": "infra"},
                "spec": {"jobTemplate": {"spec": deployment()["spec"]}}}
        checks.workload_policy(cron)
        checks.workload_policy({"kind": "Service", "metadata": {"name": "x"}, "spec": {}})

    def test_token_mounting_must_be_declared(self):
        doc = deployment()
        del doc["spec"]["template"]["spec"]["automountServiceAccountToken"]
        with self.assertRaisesRegex(ValueError, "declare token mounting intent"):
            checks.workload_policy(doc)

    def test_every_container_has_requests_and_limits(self):
        for budget, key in (("requests", "cpu"), ("limits", "memory")):
            doc = deployment(initContainers=[container(name="init")])
            del doc["spec"]["template"]["spec"]["initContainers"][0]["resources"][budget][key]
            with self.subTest(budget=budget), self.assertRaisesRegex(ValueError, f"init: missing {budget}"):
                checks.workload_policy(doc)

    def test_probes_wait_for_a_real_failure(self):
        cases = {
            "short liveness timeout": ({"livenessProbe": {"timeoutSeconds": 2, "periodSeconds": 20, "failureThreshold": 3}}, "5 seconds"),
            "quick liveness restart": ({"livenessProbe": {"timeoutSeconds": 5, "periodSeconds": 10, "failureThreshold": 3}}, "a minute"),
            "default liveness timeout": ({"livenessProbe": {"periodSeconds": 30, "failureThreshold": 3}}, "5 seconds"),
            "short readiness timeout": ({"readinessProbe": {"timeoutSeconds": 1}}, "3 seconds"),
            "env expansion": ({"livenessProbe": {"timeoutSeconds": 5, "periodSeconds": 20, "failureThreshold": 3,
                                                 "exec": {"command": ["sh", "-c", "check $(PASSWORD)"]}},
                               "env": [{"name": "PASSWORD", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}]}, "expands"),
        }
        for label, (fields, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(ValueError, message):
                checks.probe_policy("app", container(**fields))
        checks.probe_policy("app", container(livenessProbe={"timeoutSeconds": 5, "periodSeconds": 20, "failureThreshold": 3},
                                             readinessProbe={"timeoutSeconds": 3},
                                             startupProbe={"exec": {"command": ["check", "$(PORT)"]}},
                                             env=[{"name": "PORT", "value": "8080"}]))

    def test_every_workload_names_a_rendered_priority_class(self):
        classes = [{"kind": "PriorityClass", "metadata": {"name": "platform-standard"}}]
        checks.workload_priority(classes + [deployment()])
        with self.assertRaisesRegex(ValueError, "Deployment/other: name a rendered PriorityClass"):
            checks.workload_priority(classes + [deployment("other", priorityClassName="missing")])
        with self.assertRaisesRegex(ValueError, "Cluster/pg"):
            checks.workload_priority(classes + [{"kind": "Cluster", "metadata": {"name": "pg"}, "spec": {}}])

    def test_runners_name_a_rendered_priority_class(self):
        classes = [{"kind": "PriorityClass", "metadata": {"name": "ci-jobs"}}]
        config = lambda text: {"kind": "ConfigMap", "metadata": {"name": "runner"}, "data": {"config.toml": text}}  # noqa: E731
        checks.runner_priority(classes + [config('[runners.kubernetes]\n  priority_class_name = "ci-jobs"\n')])
        for text in ('[runners.kubernetes]\n  image = "x"\n', '[runners.kubernetes]\n  priority_class_name = "other"\n'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "priority_class_name"):
                checks.runner_priority(classes + [config(text)])


class Resources(unittest.TestCase):
    def test_duplicates_are_refused(self):
        with self.assertRaisesRegex(ValueError, "Duplicate resource"):
            checks.index([deployment(), deployment()])
        self.assertEqual(2, len(checks.index([deployment("a"), deployment("b")])))

    def test_images_are_found_everywhere_they_can_hide(self):
        doc = deployment(initContainers=[container(name="init", image="busybox:1")])
        doc["spec"]["imageName"] = "postgres:18"
        doc["spec"]["rule"] = {"expression": "object.spec.containers.all(c, c.image: 'kyverno:1')", "message": "image: 'x'"}
        found = list(checks.images(doc))
        self.assertIn("busybox:1", found)
        self.assertIn("postgres:18", found)
        self.assertIn("kyverno:1", found)
        self.assertNotIn("x", found)

    def test_embedded_scripts_must_parse(self):
        good = {"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"run.sh": "echo ok\n", "a.py": "x = 1\n"}}
        checks.embedded_syntax(good)
        with self.assertRaises(SyntaxError):
            checks.embedded_syntax({"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"a.py": "def (:\n"}})
        with self.assertRaises(Exception):
            checks.embedded_syntax({"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"run.sh": "if then fi\n"}})


class Alerts(unittest.TestCase):
    def rules(self, rule):
        return [{"kind": "ConfigMap", "metadata": {"name": "rules"},
                 "data": {"rules.yaml": checks.yaml.safe_dump({"groups": [{"name": "g", "rules": [rule]}]})}}]

    def test_alerts_have_an_owner_and_a_runbook_section(self):
        good = {"alert": "NodeRebootRequired", "labels": {"owner": "platform"},
                "annotations": {"runbook_url": "https://x/docs/guides/alert-runbooks.md#noderebootrequired"}}
        checks.alert_policy(self.rules(good))
        checks.alert_policy(self.rules({"record": "job:up:sum", "expr": "sum(up)"}))
        cases = {"missing owner label": dict(good, labels={}),
                 "missing runbook_url": dict(good, annotations={"runbook_url": "https://x/docs/guides/alert-runbooks.md#other"}),
                 "no section": dict(good, alert="NoSuchAlertAnywhere", annotations={
                     "runbook_url": "https://x/docs/guides/alert-runbooks.md#nosuchalertanywhere"})}
        for message, rule in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                checks.alert_policy(self.rules(rule))


class Lifecycle(unittest.TestCase):
    def test_named_lifecycle_policies_are_stored(self):
        stored = 'curl -X PUT "$ES/_ilm/policy/logs-30d" -d @p.json\n'
        named = '"index.lifecycle.name": "logs-30d"'
        checks.lifecycle_policies(stored + named, "elk")
        with self.assertRaisesRegex(ValueError, "never stored: logs-90d"):
            checks.lifecycle_policies(stored + '"index.lifecycle.name": "logs-90d"', "elk")
        with self.assertRaisesRegex(ValueError, "never stored"):
            checks.lifecycle_policies('curl -X GET "$ES/_ilm/policy/logs-30d"\n' + named, "elk")
        with self.assertRaisesRegex(ValueError, "no index.lifecycle.name found"):
            checks.lifecycle_policies(stored, "elk")


class AccessGroups(unittest.TestCase):
    def resources(self):
        reconcile = "for access_group in access-portal access-grafana; do\n  create\ndone\n"
        services = checks.yaml.safe_dump([{"Platform": [{"Grafana": {"href": "https://grafana"}}, {"GitLab": {"href": "https://gitlab"}}]}])
        gate = '{"open": ["GitLab"], "groups": {"Grafana": "access-grafana"}, "applicationGroups": {}, "groupAccess": {}}'
        address = "http://oauth2-proxy.infra.svc/oauth2/auth?allowed_groups=platform-admins,access-grafana"
        return [
            {"kind": "ConfigMap", "metadata": {"name": "keycloak-sso-reconciler"}, "data": {"reconcile.sh": reconcile}},
            {"kind": "ConfigMap", "metadata": {"name": "homepage-config"}, "data": {"services.yaml": services}},
            {"kind": "ConfigMap", "metadata": {"name": "portal-gate"}, "data": {"access.json": gate}},
            {"kind": "Middleware", "metadata": {"name": "platform-access-grafana", "namespace": "infra"},
             "spec": {"forwardAuth": {"address": address}}},
            {"kind": "Middleware", "metadata": {"name": "platform-sso", "namespace": "infra"},
             "spec": {"forwardAuth": {"address": "http://oauth2-proxy.infra.svc/oauth2/auth?allowed_groups=platform-admins"}}},
            {"kind": "Deployment", "metadata": {"name": "oauth2-proxy"},
             "spec": {"template": {"spec": {"containers": [{"args": ["--provider=oidc"]}]}}}},
            {"kind": "Ingress", "metadata": {"name": "grafana", "annotations": {
                "traefik.ingress.kubernetes.io/router.middlewares":
                    "infra-security-headers@kubernetescrd,infra-platform-access-grafana@kubernetescrd"}}},
        ]

    def test_a_consistent_policy_passes(self):
        access_checks.access_groups(self.resources())

    def test_mistakes_are_refused(self):
        def middleware(resources, address):
            resources[3]["spec"]["forwardAuth"]["address"] = address

        def gate(resources, text):
            resources[2]["data"]["access.json"] = text

        def ingress(resources, chain):
            resources[6]["metadata"]["annotations"]["traefik.ingress.kubernetes.io/router.middlewares"] = chain
        cases = {
            "allowed_groups including platform-admins": lambda r: middleware(
                r, "http://oauth2-proxy/oauth2/auth?allowed_groups=access-grafana"),
            "groups the reconciler does not create: access-kibana": lambda r: middleware(
                r, "http://oauth2-proxy/oauth2/auth?allowed_groups=platform-admins,access-kibana"),
            "global --allowed-group": lambda r: r[5]["spec"]["template"]["spec"]["containers"][0]["args"].append("--allowed-group=x"),
            "entries missing from the portal: Kibana": lambda r: gate(
                r, '{"open": ["Kibana"], "groups": {}, "applicationGroups": {}, "groupAccess": {}}'),
            "either open to everyone or needs a group": lambda r: gate(
                r, '{"open": ["Grafana"], "groups": {"Grafana": "access-grafana"}, "applicationGroups": {}, "groupAccess": {}}'),
            "portal-gate: groups the reconciler does not create": lambda r: gate(
                r, '{"open": [], "groups": {"Grafana": "access-nobody"}, "applicationGroups": {}, "groupAccess": {}}'),
            "unknown sign-in middleware": lambda r: ingress(r, "infra-platform-access-other@kubernetescrd"),
            "one sign-in middleware decides access": lambda r: ingress(
                r, "infra-platform-access-grafana@kubernetescrd,infra-platform-sso@kubernetescrd"),
            "access groups are named access-<service>": lambda r: r[0]["data"].update(
                {"reconcile.sh": "for access_group in grafana-users; do\ndone\n"}),
        }
        for message, change in cases.items():
            resources = copy.deepcopy(self.resources())
            change(resources)
            with self.subTest(message), self.assertRaisesRegex(ValueError, message.replace("(", r"\(").replace(")", r"\)")):
                access_checks.access_groups(resources)


if __name__ == "__main__":
    unittest.main()
