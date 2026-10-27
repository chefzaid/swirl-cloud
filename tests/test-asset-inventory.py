#!/usr/bin/env python3
"""Guards for the asset inventory: it never asks Kubernetes for Secrets, its CSV
sheets cannot run as spreadsheet formulas, it writes private files without
overwriting, a source it could not read fails the run instead of looking
complete, and every outside service in the guide reaches the inventory."""
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("asset_inventory", ROOT / "scripts/asset-inventory.py")
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)

NODE = {"metadata": {"name": "node-1", "labels": {"node-role.kubernetes.io/control-plane": "true",
                                                    "topology.kubernetes.io/region": "eu-west"}},
        "status": {"capacity": {"cpu": "8", "memory": "32814176Ki"}, "addresses": [{"type": "InternalIP", "address": "10.0.0.1"}],
                   "nodeInfo": {"architecture": "amd64", "osImage": "Ubuntu 26.04 LTS", "kernelVersion": "7.0.0",
                                "kubeletVersion": "v1.36.4+k3s1"}}}
WORKLOADS = [
    {"kind": "Deployment", "metadata": {"name": "keycloak", "namespace": "infra"},
     "spec": {"template": {"metadata": {"labels": {"app": "keycloak"}},
                           "spec": {"initContainers": [{"image": "busybox:1"}], "containers": [{"image": "keycloak:26"}]}}}},
    {"kind": "CronJob", "metadata": {"name": "report", "namespace": "apps-int", "labels": {"swirl-cloud.io/owner": "=HYPERLINK(1)"}},
     "spec": {"jobTemplate": {"spec": {"template": {"spec": {"containers": [{"image": "report:1"}]}}}}}},
    {"kind": "StatefulSet", "metadata": {"name": "db", "namespace": "apps",
                                         "labels": {"swirl-cloud.io/criticality": "medium",
                                                    "swirl-cloud.io/data-classification": "personal"}},
     "spec": {"template": {"spec": {"containers": [{"image": "postgres:18"}]}}}},
]
CLAIM = {"metadata": {"name": "postgres-data", "namespace": "infra"},
         "spec": {"storageClassName": "longhorn", "accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "2Gi"}}},
         "status": {"phase": "Bound", "capacity": {"storage": "2Gi"}}}


def fake_kubectl(directory, broken=""):
    """A kubectl that serves fixtures, logs every call and refuses any Secret read."""
    log = directory / "calls.log"
    fixtures = {"nodes": [NODE], inventory.WORKLOAD_KINDS: WORKLOADS, "persistentvolumeclaims": [CLAIM]}
    (directory / "fixtures.json").write_text(json.dumps(fixtures))
    script = directory / "kubectl"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        args = sys.argv[1:]
        open({str(log)!r}, "a").write(" ".join(args) + "\\n")
        if any("secret" in arg.lower() for arg in args):
            sys.exit("Secrets must never be read")
        kind = args[args.index("get") + 1]
        if kind == {broken!r}:
            sys.exit("forbidden")
        print(json.dumps({{"items": json.load(open({str(directory / 'fixtures.json')!r}))[kind]}}))
        """))
    script.chmod(0o755)
    return log


class AssetInventoryTest(unittest.TestCase):
    def run_inventory(self, *arguments, broken=""):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        log = fake_kubectl(directory, broken)
        # Run from the temporary directory so a regression can never write into the checkout.
        result = subprocess.run([sys.executable, str(ROOT / "scripts/asset-inventory.py"), *arguments], text=True,
                                capture_output=True, cwd=directory,
                                env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"})
        return result, log.read_text() if log.exists() else "", directory

    def test_reads_only_metadata_and_classifies(self):
        result, calls, _ = self.run_inventory("--format", "json")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("secret", calls.lower())
        data = json.loads(result.stdout)
        [server] = data["servers"]
        self.assertEqual(("control-plane", "31.3", "eu-west", "platform"),
                         (server["roles"], server["memory_gib"], server["region"], server["owner"]))
        rows = {row["name"]: row for row in data["workloads"]}
        self.assertEqual("busybox:1 keycloak:26", rows["keycloak"]["images"])
        self.assertEqual(("platform", "high (derived)"), (rows["keycloak"]["owner"], rows["keycloak"]["criticality"]))
        self.assertEqual(("report:1", "low (derived)"), (rows["report"]["images"], rows["report"]["criticality"]))
        self.assertEqual(("medium", "personal"), (rows["db"]["criticality"], rows["db"]["data_classification"]))
        self.assertEqual("2Gi", data["data_stores"][0]["capacity"])
        self.assertIn("TODO §37", data["staff_devices"])

    def test_every_listed_outside_service_is_inventoried(self):
        guide = inventory.EXTERNAL_SERVICES.read_text()
        expected, counting = 0, False
        for line in guide.splitlines():
            if line.startswith(("| Service |", "| Source |")):
                counting = True
            elif not line.startswith("|"):
                counting = False
            elif counting and not line.startswith("| ---"):
                expected += 1
        rows = inventory.external_services(guide)
        self.assertGreater(expected, 0)
        self.assertEqual(expected, len(rows))
        self.assertFalse(any(row["service"] == "GitLab" and "telemetry" in row["category"].lower() for row in rows))
        self.assertTrue(all(row["service"] and row["category"] for row in rows), rows)

    def test_sheets_are_private_and_formula_safe(self):
        result, _, directory = self.run_inventory("--output-dir", "")
        self.assertNotEqual(0, result.returncode)  # An empty path is refused rather than written to the working directory.
        output = directory / "out"
        result, _, _ = self.run_inventory("--output-dir", str(output))
        self.assertEqual(0, result.returncode, result.stderr)
        [sheets] = output.iterdir()
        self.assertEqual(0o700, stat.S_IMODE(sheets.stat().st_mode))
        for path in sheets.iterdir():
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), path)
        owners = [row["owner"] for row in csv.DictReader(io.StringIO((sheets / "workloads.csv").read_text()))]
        self.assertIn("'=HYPERLINK(1)", owners)
        self.assertFalse(any(owner.startswith(("=", "+", "-", "@")) for owner in owners))

    def test_unreadable_source_fails_instead_of_looking_complete(self):
        result, _, _ = self.run_inventory("--format", "json", broken="persistentvolumeclaims")
        self.assertEqual(1, result.returncode)
        data = json.loads(result.stdout)
        self.assertEqual([], data["data_stores"])
        self.assertTrue(any(error.startswith("data_stores:") for error in data["errors"]), data["errors"])


if __name__ == "__main__":
    unittest.main()
