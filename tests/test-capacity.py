#!/usr/bin/env python3
"""Guards for the multi-server budget (sizing.py check-capacity, TODO §26): with
more than one server, a sizing profile is refused when its requests or its Longhorn
copies no longer fit once the largest server fails; DaemonSets, bare CI pods and
Jobs are counted as the scheduler sees them, and a single server is not checked."""
import io
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import capacity  # noqa: E402
import sizing  # noqa: E402

GIB = 2 ** 30
q = sizing.quantity


def node(name, cpu="8", memory="32Gi", taints=None, unschedulable=False):
    return {"metadata": {"name": name}, "spec": {"taints": taints or [], "unschedulable": unschedulable},
            "status": {"allocatable": {"cpu": cpu, "memory": memory}}}


def pod(name, namespace="apps", owner=None, node_name="n1", cpu="1", memory="1Gi", phase="Running"):
    references = [{"kind": owner[0], "name": owner[1], "controller": True}] if owner else []
    return {"metadata": {"name": name, "namespace": namespace, "ownerReferences": references},
            "spec": {"nodeName": node_name, "containers": [{"name": "c", "resources": {"requests": {"cpu": cpu, "memory": memory}}}]},
            "status": {"phase": phase}}


def volume(name, size, copies, claim=None):
    status = {"kubernetesStatus": {"namespace": claim.split("/")[0], "pvcName": claim.split("/")[1]}} if claim else {}
    return {"metadata": {"name": name}, "spec": {"size": str(size), "numberOfReplicas": copies}, "status": status}


def longhorn_node(name, maximum, reserved=0, scheduling=True):
    return {"metadata": {"name": name}, "spec": {"allowScheduling": scheduling,
                                                 "disks": {"default": {"allowScheduling": True, "storageReserved": reserved}}},
            "status": {"diskStatus": {"default": {"storageMaximum": maximum}}}}


class Quantities(unittest.TestCase):
    def test_cpu(self):
        self.assertEqual(0.25, capacity.cpu("250m"))
        self.assertEqual(2.0, capacity.cpu("2"))
        self.assertEqual(1.5, capacity.cpu(1.5))
        with self.assertRaises(ValueError):
            capacity.cpu("two")

    def test_requests_default_to_zero(self):
        self.assertEqual({"cpu": 0.0, "memory": 0.0}, capacity.requests_of({"limits": {"cpu": "4"}}, q))
        self.assertEqual({"cpu": 0.5, "memory": 2 * GIB}, capacity.requests_of({"requests": {"cpu": "500m", "memory": "2Gi"}}, q))


class RenderedWorkloads(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for directory in ("platform", "corp"):
            (self.root / "k8s" / directory).mkdir(parents=True)
        (self.root / "k8s/platform/app.yaml").write_text(textwrap.dedent("""\
            apiVersion: apps/v1
            kind: Deployment
            metadata: {name: web, namespace: infra}
            spec:
              replicas: 2
              template:
                spec:
                  containers:
                    - {name: web, resources: {requests: {cpu: 100m, memory: 256Mi}, limits: {memory: 1Gi}}}
                    - {name: sidecar, resources: {requests: {cpu: 10m, memory: 16Mi}}}
            ---
            apiVersion: apps/v1
            kind: StatefulSet
            metadata: {name: db, namespace: infra}
            spec:
              template:
                spec:
                  containers:
                    - {name: db}
            ---
            apiVersion: v1
            kind: Service
            metadata: {name: web, namespace: infra}
            """))
        (self.root / "k8s/corp/erp.yaml").write_text(textwrap.dedent("""\
            apiVersion: apps/v1
            kind: Deployment
            metadata: {name: erp, namespace: corp}
            spec:
              template:
                spec:
                  containers: [{name: erp, resources: {requests: {cpu: "1", memory: 1Gi}}}]
            """))

    def test_profile_requests_are_merged_per_container(self):
        manifests = {"platform/app.yaml": {"Deployment/web": {"containers": {"web": {"resources": {"requests": {"memory": "2Gi"}}}}}}}
        found = capacity.rendered_workloads(self.root, manifests, q)
        self.assertEqual((2, {"cpu": 0.11, "memory": 2 * GIB + 16 * 2 ** 20}), found[("infra", "Deployment", "web")])
        self.assertEqual((1, {"cpu": 0.0, "memory": 0.0}), found[("infra", "StatefulSet", "db")])
        self.assertEqual((1, {"cpu": 1.0, "memory": GIB}), found[("corp", "Deployment", "erp")])
        self.assertNotIn(("infra", "Service", "web"), found)

    def test_company_applications_count_only_when_enabled(self):
        self.assertNotIn(("corp", "Deployment", "erp"), capacity.rendered_workloads(self.root, {}, q, apps_enabled=False))

    def test_the_large_profile_needs_more_than_standard(self):
        standard = capacity.rendered_workloads(ROOT, sizing.load("standard")["manifests"], q)
        large = capacity.rendered_workloads(ROOT, sizing.load("large")["manifests"], q)
        self.assertEqual(set(standard), set(large))
        self.assertEqual({"cpu": 1.0, "memory": 8 * GIB}, large[("infra", "Deployment", "gitlab")][1])
        total = lambda workloads: sum(r * v["memory"] for r, v in workloads.values())  # noqa: E731
        self.assertGreater(total(large), total(standard))


class Owners(unittest.TestCase):
    def test_pods_map_to_their_workload(self):
        replica_sets = {("apps", "web-5d9f"): "web"}
        self.assertEqual(("apps", "Deployment", "web"), capacity.owner(pod("p", owner=("ReplicaSet", "web-5d9f")), replica_sets))
        self.assertEqual(("apps", "ReplicaSet", "lone"), capacity.owner(pod("p", owner=("ReplicaSet", "lone")), replica_sets))
        self.assertEqual(("apps", "StatefulSet", "db"), capacity.owner(pod("p", owner=("StatefulSet", "db")), {}))
        self.assertIsNone(capacity.owner(pod("p", owner=("Job", "backup-123")), {}))
        self.assertIsNone(capacity.owner(pod("runner-job"), {}))


class Budget(unittest.TestCase):
    def test_daemonsets_reduce_each_node_and_transient_pods_are_left_out(self):
        nodes = [node("n1"), node("n2"), node("n3", memory="64Gi"), node("tainted", taints=[{"effect": "NoSchedule"}]),
                 node("cordoned", unschedulable=True)]
        pods = [pod("agent-1", owner=("DaemonSet", "agent"), node_name="n1", memory="2Gi"),
                pod("agent-3", owner=("DaemonSet", "agent"), node_name="n3", memory="2Gi"),
                pod("app-1", owner=("StatefulSet", "app"), memory="4Gi"),
                pod("ci-job", memory="16Gi"), pod("backup", owner=("Job", "backup"), memory="16Gi"),
                pod("done", owner=("StatefulSet", "old"), memory="16Gi", phase="Succeeded")]
        budget = capacity.compute_budget({}, nodes, pods, {}, q)
        self.assertEqual({"n1", "n2", "n3"}, set(budget["nodes"]))
        self.assertEqual("n3", budget["lost"])
        self.assertEqual({"cpu": 1.0, "memory": 4 * GIB}, budget["need"])
        self.assertEqual(62 * GIB, budget["nodes"]["n3"]["memory"])
        self.assertEqual({"cpu": 15.0, "memory": 62 * GIB}, budget["after_loss"])

    def test_rendered_workloads_replace_their_live_pods_and_keep_live_scale(self):
        workloads = {("infra", "Deployment", "keycloak"): (1, {"cpu": 0.5, "memory": 2 * GIB})}
        pods = [pod(f"keycloak-{i}", namespace="infra", owner=("ReplicaSet", "keycloak-abc"), memory="1Gi") for i in range(2)]
        budget = capacity.compute_budget(workloads, [node("n1"), node("n2")], pods, {("infra", "keycloak-abc"): "keycloak"}, q)
        self.assertEqual({"cpu": 1.0, "memory": 4 * GIB}, budget["need"])

    def test_a_single_server_is_not_checked(self):
        lines, problems = capacity.check({("infra", "Deployment", "x"): (1, {"cpu": 99.0, "memory": 999 * GIB})},
                                         [node("n1"), node("n2", taints=[{"effect": "NoExecute"}])], [], {}, [], [], {}, q)
        self.assertEqual([], problems)
        self.assertIn("One schedulable server", lines[0])

    def test_a_profile_that_fits_after_a_loss_passes(self):
        workloads = {("infra", "Deployment", "x"): (1, {"cpu": 10.0, "memory": 40 * GIB})}
        lines, problems = capacity.check(workloads, [node("n1"), node("n2"), node("n3")], [], {}, [], [], {}, q)
        self.assertEqual([], problems)
        self.assertEqual("Without n1: requests need 10.0 CPU and 40.0 GiB; the other servers offer 16.0 CPU and 64.0 GiB.", lines[0])

    def test_a_profile_that_does_not_fit_is_refused(self):
        workloads = {("infra", "Deployment", "x"): (3, {"cpu": 6.0, "memory": 24 * GIB})}
        _, problems = capacity.check(workloads, [node("n1"), node("n2"), node("n3", memory="128Gi")], [], {}, [], [], {}, q)
        self.assertEqual(["CPU requests (18.0 CPU) exceed what the servers other than n3 offer (16.0 CPU)",
                          "Memory requests (72.0 GiB) exceed what the servers other than n3 offer (64.0 GiB)"], problems)

    def test_the_large_profile_against_small_and_large_servers(self):
        large = capacity.rendered_workloads(ROOT, sizing.load("large")["manifests"], q)
        small = [node(f"n{i}", cpu="8", memory="16Gi") for i in range(3)]
        big = [node(f"n{i}", cpu="16", memory="128Gi") for i in range(3)]
        self.assertTrue(capacity.check(large, small, [], {}, [], [], {}, q)[1])
        self.assertEqual([], capacity.check(large, big, [], {}, [], [], {}, q)[1])


class Storage(unittest.TestCase):
    def test_copies_count_against_the_disk_left_after_a_loss(self):
        volumes = [volume("pvc-1", 100 * GIB, 3, "infra/gitlab-data"), volume("pvc-2", 10 * GIB, 3)]
        hosts = [longhorn_node(f"n{i}", 200 * GIB, reserved=20 * GIB) for i in range(3)]
        hosts.append(longhorn_node("off", 900 * GIB, scheduling=False))
        result = capacity.storage_budget(volumes, hosts, {"infra/gitlab-data": "150Gi", "infra/new-claim": "20Gi"}, q)
        self.assertEqual((150 * 3 + 10 * 3 + 20 * 3) * GIB, result["need"])
        self.assertEqual(360 * GIB, result["after_loss"])
        self.assertEqual((3, 3), (result["hosts"], result["copies"]))

    def test_too_little_disk_is_refused_and_too_few_hosts_noted(self):
        nodes = [node("n1"), node("n2"), node("n3")]
        volumes = [volume("pvc-1", 100 * GIB, 3, "infra/data")]
        hosts = [longhorn_node(f"n{i}", 150 * GIB) for i in range(3)]
        lines, problems = capacity.check({}, nodes, [], {}, volumes, hosts, {}, q)
        self.assertEqual([], problems)
        self.assertIn("Note: volumes keep 3 copies on 3 storage servers", lines[-1])
        hosts[0]["status"]["diskStatus"]["default"]["storageMaximum"] = 100 * GIB
        _, problems = capacity.check({}, nodes, [], {}, volumes, hosts, {}, q)
        self.assertEqual(["Longhorn copies (300 GiB) exceed the schedulable disk space of the storage servers other than n1 (250 GiB)"],
                         problems)

    def test_a_volume_being_deleted_is_not_counted(self):
        gone = volume("pvc-old", 500 * GIB, 3)
        gone["metadata"]["deletionTimestamp"] = "2026-11-24T10:00:00Z"
        self.assertEqual(0, capacity.storage_budget([gone], [longhorn_node("n1", GIB)], {}, q)["need"])


class Command(unittest.TestCase):
    def run_check(self, answers, argv=()):
        kinds = ("nodes", "pods", "replicasets", "volumes.longhorn.io", "nodes.longhorn.io")

        def kubectl_json(*args):
            return answers.get(next((a for a in args if a in kinds), None))
        with mock.patch.object(sizing, "kubectl_json", kubectl_json), mock.patch.dict(sizing.os.environ, {}, clear=True), \
                mock.patch.object(sys, "argv", ["sizing.py", "check-capacity", "--root", str(ROOT), *argv]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = sizing.main()
        return code, out.getvalue(), err.getvalue()

    def test_an_unreachable_cluster_defers_the_check(self):
        code, out, _ = self.run_check({})
        self.assertEqual(0, code)
        self.assertIn("checked on reruns", out)

    def test_the_command_refuses_a_profile_that_does_not_fit(self):
        answers = {"nodes": {"items": [node(f"n{i}", cpu="8", memory="16Gi") for i in range(3)]}}
        code, _, err = self.run_check(answers, ["--profile", "large"])
        self.assertEqual(1, code)
        self.assertIn("After one server fails: Memory requests", err)
        self.assertIn("Sizing profile large does not fit after one server fails", err)
        answers = {"nodes": {"items": [node(f"n{i}", cpu="16", memory="128Gi") for i in range(3)]}}
        self.assertEqual(0, self.run_check(answers, ["--profile", "large", "--apps-enabled", "false"])[0])


if __name__ == "__main__":
    unittest.main()
