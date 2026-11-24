#!/usr/bin/env python3
"""Guards for the read-only HA readiness check: it passes a cluster whose every
replicated service, database and volume spans three hosts, and fails, with a
reason, as soon as one host carries what should be spread, a copy is unhealthy,
or recovery depends on unverified fencing."""
import copy
import datetime
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_ha", ROOT / "scripts/verify-high-availability.py")
ha = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ha)

HOSTS = ["n1", "n2", "n3"]
READY = {"conditions": [{"type": "Ready", "status": "True"}]}


def pod(name, host, labels, containers=None):
    status = copy.deepcopy(READY)
    if containers:
        status["containerStatuses"] = containers
    return {"metadata": {"name": name, "labels": labels}, "spec": {"nodeName": host}, "status": status}


def control_plane(name, address):
    return {"metadata": {"name": name, "labels": {"node-role.kubernetes.io/control-plane": "true", "node-role.kubernetes.io/etcd": "true"},
                         "annotations": {"node.swirl-cloud.io/fenced-detach-policy": "verified"}},
            "spec": {}, "status": {**copy.deepcopy(READY), "addresses": [{"type": "InternalIP", "address": address}]}}


WORKLOADS = {("daemonset", "traefik"): 3, ("deployment", "coredns"): 3, ("deployment", "cloudnative-pg"): 3,
             ("statefulset", "shared-redis-ha-server"): 3, ("deployment", "shared-redis-ha-haproxy"): 3,
             ("deployment", "keycloak"): 2, ("deployment", "external-secrets"): 2, ("deployment", "external-secrets-webhook"): 2,
             ("deployment", "external-secrets-cert-controller"): 2, ("deployment", "argocd-server"): 2,
             ("deployment", "argocd-repo-server"): 2, ("deployment", "argocd-applicationset-controller"): 2,
             ("deployment", "argocd-redis-ha-haproxy"): 2, ("statefulset", "argocd-redis-ha-server"): 3}
OTHER_NAMESPACES = {"coredns": "kube-system", "cloudnative-pg": "cnpg-system"}


def volume(name, replicas=3, state="attached", robustness="healthy"):
    return {"metadata": {"name": name}, "spec": {"numberOfReplicas": replicas}, "status": {"state": state, "robustness": robustness}}


def replica(name, volume_name, host, healthy=True, failed=False):
    return {"metadata": {"name": name}, "spec": {"volumeName": volume_name, "nodeID": host,
                                                 "healthyAt": "2026-11-24T00:00:00Z" if healthy else "",
                                                 "failedAt": "2026-11-24T00:00:00Z" if failed else ""}}


def engine(volume_name, modes, state="running"):
    return {"spec": {"volumeName": volume_name}, "status": {"currentState": state, "replicaModeMap": modes}}


class Cluster:
    """A healthy three-host HA cluster, as kubectl would return it."""

    def __init__(self):
        self.objects = {
            ("configmap", "infra", "swirl-cloud-topology"): {"data": {"highAvailabilityEnabled": "true"}},
            ("configmap", "infra", "swirl-cloud-public-ingress"): {"data": {"mode": "tunnel", "tunnelID": "t1", "publishedTunnelID": "t1",
                                                                            "domain": "example.com"}},
            ("nodes", None, None): {"items": [control_plane(h, f"10.0.0.{i}") for i, h in enumerate(HOSTS, 1)]},
            ("endpointslices", "default", None): {"items": [{"metadata": {"labels": {"kubernetes.io/service-name": "kubernetes"}},
                                                            "endpoints": [{"addresses": [f"10.0.0.{i}"], "conditions": {"ready": True}}
                                                                          for i in (1, 2, 3)]}]},
            ("clusters.postgresql.cnpg.io", "infra", "postgres-ha"): {"status": {"currentPrimary": "postgres-ha-1"}},
            ("settings.longhorn.io", "longhorn-system", "node-down-pod-deletion-policy"): {"value": "do-nothing"},
            ("volumes.longhorn.io", "longhorn-system", None): {"items": [volume("pvc-a"), volume("pvc-b", state="detached")]},
            ("replicas.longhorn.io", "longhorn-system", None): {"items": [replica(f"{v}-r{i}", v, h) for v in ("pvc-a", "pvc-b")
                                                                         for i, h in enumerate(HOSTS)]},
            ("engines.longhorn.io", "longhorn-system", None): {"items": [engine("pvc-a", {f"pvc-a-r{i}": "RW" for i in range(3)})]},
            ("application", "infra", "swirl-cloud"): {"spec": {"source": {"helm": {"valuesObject": {
                "nodeFencing": {"enabled": True, "nodeNames": list(HOSTS)}}}}}},
            ("cronjob", "infra", "node-fencing"): {"spec": {}, "status": {"lastSuccessfulTime": (
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")}},
        }
        pods = {"infra": [], "kube-system": [], "cnpg-system": []}
        for (kind, name), count in WORKLOADS.items():
            self.objects[(kind, OTHER_NAMESPACES.get(name, "infra"), name)] = {"spec": {"selector": {"matchLabels": {"app": name}}}}
            containers = [{"name": "traefik", "ready": True}, {"name": "cloudflared", "ready": True}] if name == "traefik" else None
            pods[OTHER_NAMESPACES.get(name, "infra")] += [pod(f"{name}-{i}", HOSTS[i], {"app": name}, containers) for i in range(count)]
        pods["infra"] += [pod(f"postgres-ha-{i}", h, {"cnpg.io/cluster": "postgres-ha", "cnpg.io/podRole": "instance"})
                          for i, h in enumerate(HOSTS, 1)]
        for namespace, items in pods.items():
            self.objects[("pods", namespace, None)] = {"items": items}
        self.answers = {"psql": "2\non\nANY 1 (postgres-ha-2,postgres-ha-3)",
                        "redis-cli": "OK 3 usable Sentinels. Quorum and failover authorization can be reached"}
        self.commands = []

    def get(self, kind, namespace=None, name=None):
        return copy.deepcopy(self.objects[(kind, namespace, name)])

    def run(self, command):
        self.commands.append(command)
        for word, answer in self.answers.items():
            if word in command:
                return answer
        return ""


def verify(cluster, *argv):
    with mock.patch.object(ha, "get", cluster.get), mock.patch.object(ha, "run", cluster.run), \
            mock.patch.object(sys, "argv", ["verify-high-availability.py", *argv]), \
            mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        ha.main()
    return out.getvalue()


class HealthyCluster(unittest.TestCase):
    def test_a_healthy_cluster_passes_every_stage(self):
        cluster = Cluster()
        output = verify(cluster)
        self.assertEqual(5, output.count("PASS:"))
        self.assertIn("NOTE: singleton recovery is not certified", output)
        scripts = [Path(c[1]).name for c in cluster.commands if c[0] in ("python3", "bash")]
        self.assertEqual(["configure-cloudflare-tunnel.py", "configure-vault-ha.sh", "configure-kafka-ha.py"], scripts)
        tunnel = next(c for c in cluster.commands if "configure-cloudflare-tunnel.py" in c[1])
        self.assertEqual(["--verify-origins", "--domain", "example.com"], tunnel[2:])

    def test_fencing_is_certified_only_when_required_and_recent(self):
        self.assertIn("PASS: automatic fencing covers every workload host", verify(Cluster(), "--require-fencing"))

    def test_only_reads_and_execs_are_run(self):
        cluster = Cluster()
        verify(cluster, "--require-fencing")
        for command in cluster.commands:
            if command[0] == "kubectl":
                self.assertIn("exec", command)
            else:
                self.assertTrue({"--verify-origins", "--verify", "verify"} & set(command), command)


class FailingCluster(unittest.TestCase):
    def fails(self, change, message, *argv):
        cluster = Cluster()
        change(cluster)
        with self.assertRaises(RuntimeError) as raised:
            verify(cluster, *argv)
        self.assertIn(message, str(raised.exception))

    def test_ha_must_be_enabled(self):
        self.fails(lambda c: c.objects[("configmap", "infra", "swirl-cloud-topology")]["data"].update(highAvailabilityEnabled="false"),
                   "HA is not enabled")

    def test_control_planes(self):
        nodes = ("nodes", None, None)
        cases = {
            "two members": (lambda c: c.objects[nodes]["items"].pop(), "at least three members"),
            "one not ready": (lambda c: c.objects[nodes]["items"][0]["status"]["conditions"][0].update(status="False"), "must be Ready"),
            "not etcd": (lambda c: c.objects[nodes]["items"][1]["metadata"]["labels"].pop("node-role.kubernetes.io/etcd"),
                         "embedded-etcd member"),
            "detach unverified": (lambda c: c.objects[nodes]["items"][2]["metadata"]["annotations"].clear(), "detach safety"),
            "API endpoint missing": (lambda c: c.objects[("endpointslices", "default", None)]["items"][0]["endpoints"].pop(),
                                     "private control-plane endpoint"),
            "API endpoint not ready": (lambda c: c.objects[("endpointslices", "default", None)]["items"][0]["endpoints"][0]
                                       .update(conditions={"ready": False}), "private control-plane endpoint"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.fails(change, message)

    def test_the_tunnel_must_be_published(self):
        ingress = ("configmap", "infra", "swirl-cloud-public-ingress")
        for data in ({"mode": "direct"}, {"publishedTunnelID": ""}, {"publishedTunnelID": "t0"}):
            with self.subTest(data=data):
                self.fails(lambda c: c.objects[ingress]["data"].update(data), "Public DNS publication has not been verified")

    def test_replicas_must_be_on_distinct_ready_hosts(self):
        def same_host(c):
            for item in c.objects[("pods", "infra", None)]["items"]:
                if item["metadata"]["labels"].get("app") == "keycloak":
                    item["spec"]["nodeName"] = "n1"

        def not_ready(c):
            for item in c.objects[("pods", "kube-system", None)]["items"]:
                item["status"]["conditions"][0]["status"] = "False"

        def deleting(c):
            for item in c.objects[("pods", "infra", None)]["items"]:
                if item["metadata"]["labels"].get("app") == "argocd-redis-ha-server" and item["spec"]["nodeName"] == "n3":
                    item["metadata"]["deletionTimestamp"] = "2026-11-24T10:00:00Z"
        for change, message in ((same_host, "keycloak: need 2 Ready pods on distinct hosts; found 1"),
                                (not_ready, "coredns: need 3 Ready pods"), (deleting, "argocd-redis-ha-server: need 3")):
            with self.subTest(message):
                self.fails(change, message)

    def test_every_ingress_pod_needs_a_ready_connector(self):
        def connector_down(c):
            traefik = next(p for p in c.objects[("pods", "infra", None)]["items"] if p["metadata"]["labels"].get("app") == "traefik")
            traefik["status"]["containerStatuses"][1]["ready"] = False
        self.fails(connector_down, "Ready tunnel connector")

    def test_databases_and_quorums(self):
        cases = {
            "one standby": (lambda c: c.answers.update(psql="1\non\nANY 1 (postgres-ha-2)"), "synchronous standbys"),
            "asynchronous": (lambda c: c.answers.update(psql="2\noff\nANY 1 (postgres-ha-2,postgres-ha-3)"), "synchronous standbys"),
            "no quorum names": (lambda c: c.answers.update(psql="2\non\n"), "synchronous standbys"),
            "sentinel quorum": (lambda c: c.answers.update({"redis-cli": "NOQUORUM 1 usable Sentinels"}), "failover quorum"),
            "two instances": (lambda c: c.objects[("pods", "infra", None)]["items"].pop(), "PostgreSQL: need 3"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.fails(change, message)

    def test_longhorn_must_leave_pod_deletion_to_fencing(self):
        self.fails(lambda c: c.objects[("settings.longhorn.io", "longhorn-system", "node-down-pod-deletion-policy")]
                   .update(value="delete-both-statefulset-and-deployment-pod"), "verified fencing")

    def test_fencing_requirements(self):
        values = ("application", "infra", "swirl-cloud")
        job = ("cronjob", "infra", "node-fencing")
        fencing = lambda c: c.objects[values]["spec"]["source"]["helm"]["valuesObject"]["nodeFencing"]  # noqa: E731
        cases = {
            "disabled": (lambda c: fencing(c).update(enabled=False), "verified fencing inventory"),
            "host missing": (lambda c: fencing(c)["nodeNames"].pop(), "does not cover every workload host"),
            "suspended": (lambda c: c.objects[job]["spec"].update(suspend=True), "has not completed"),
            "never ran": (lambda c: c.objects[job]["status"].clear(), "has not completed"),
            "stale": (lambda c: c.objects[job]["status"].update(lastSuccessfulTime="2026-01-01T00:00:00Z"), "no recent successful"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                self.fails(change, message, "--require-fencing")

    def test_a_tainted_host_need_not_be_fenced(self):
        cluster = Cluster()
        extra = control_plane("n4", "10.0.0.4")
        extra["spec"]["taints"] = [{"key": "dedicated", "effect": "NoSchedule"}]
        cluster.objects[("nodes", None, None)]["items"].append(extra)
        cluster.objects[("endpointslices", "default", None)]["items"][0]["endpoints"].append({"addresses": ["10.0.0.4"]})
        self.assertIn("PASS: automatic fencing", verify(cluster, "--require-fencing"))


class Storage(unittest.TestCase):
    def check(self, volumes, replicas, engines):
        ha.verify_storage(volumes, replicas, engines)

    def test_three_healthy_copies_on_distinct_hosts_pass(self):
        self.check([volume("v")], [replica(f"r{i}", "v", h) for i, h in enumerate(HOSTS)],
                   [engine("v", {"r0": "RW", "r1": "RW", "r2": "RW"})])

    def test_storage_failures(self):
        good = [replica(f"r{i}", "v", h) for i, h in enumerate(HOSTS)]
        rw = engine("v", {"r0": "RW", "r1": "RW", "r2": "RW"})
        cases = {
            "two requested": ([volume("v", replicas=2)], good, [rw], "fewer than three requested copies"),
            "two hosts": ([volume("v")], good[:2] + [replica("r2", "v", "n1")], [rw], "distinct hosts"),
            "copy failed": ([volume("v")], good[:2] + [replica("r2", "v", "n3", failed=True)], [rw], "distinct hosts"),
            "copy not rebuilt": ([volume("v")], good[:2] + [replica("r2", "v", "n3", healthy=False)], [rw], "distinct hosts"),
            "copy not in the engine": ([volume("v")], good, [engine("v", {"r0": "RW", "r1": "RW", "r2": "WO"})], "distinct hosts"),
            "engine stopped": ([volume("v")], good, [engine("v", {"r0": "RW", "r1": "RW", "r2": "RW"}, state="stopped")],
                               "distinct hosts"),
            "degraded": ([volume("v", robustness="degraded")], good, [rw], "attached volume is not healthy"),
        }
        for label, (volumes, replicas, engines, message) in cases.items():
            with self.subTest(label), self.assertRaisesRegex(RuntimeError, message):
                self.check(volumes, replicas, engines)

    def test_a_detached_volume_needs_no_engine_and_a_deleted_one_is_skipped(self):
        self.check([volume("v", state="detached")], [replica(f"r{i}", "v", h) for i, h in enumerate(HOSTS)], [])
        deleted = volume("gone", replicas=1)
        deleted["metadata"]["deletionTimestamp"] = "2026-11-24T10:00:00Z"
        self.check([deleted], [], [])


class Helpers(unittest.TestCase):
    def test_ready_needs_a_true_ready_condition_and_no_deletion(self):
        self.assertTrue(ha.ready({"metadata": {}, "status": READY}))
        self.assertFalse(ha.ready({"metadata": {}, "status": {}}))
        self.assertFalse(ha.ready({"metadata": {"deletionTimestamp": "x"}, "status": READY}))

    def test_a_failed_read_names_the_command_only(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="token=secret")
        with mock.patch.object(ha.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError) as raised:
                ha.run(["kubectl", "-n", "infra", "exec", "pod", "--", "psql"])
        self.assertEqual("Read-only check failed: kubectl -n infra exec", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
