#!/usr/bin/env python3
"""Guards for the policy report: every workload of the application namespaces is
submitted as a server-side dry run with its status and managed fields removed,
policy warnings are counted per rule, and a refused dry run is reported."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("policy", ROOT / "scripts/policy-report.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)
WARN = ("Warning: Validation failed for ValidatingAdmissionPolicy 'swirl-cloud-production-availability' with binding 'b': "
        "{rule}: Deployment {name}")


class PolicyReport(unittest.TestCase):
    def test_dry_runs_and_counts(self):
        submitted = []
        items = [{"kind": "Deployment", "metadata": {"name": "shop", "managedFields": [1]}, "status": {"x": 1}},
                 {"kind": "Deployment", "metadata": {"name": "api"}, "status": {}},
                 {"kind": "CronJob", "metadata": {"name": "nightly"}}]

        def kubectl(*args, stdin=None):
            if args[:2] == ("get", "namespaces"):
                return SimpleNamespace(returncode=0, stdout="apps-shop-prod", stderr="")
            if args[0] == "get":
                return SimpleNamespace(returncode=0, stdout=json.dumps({"items": items}), stderr="")
            item = json.loads(stdin)
            submitted.append(item)
            name = item["metadata"]["name"]
            if name == "nightly":
                return SimpleNamespace(returncode=1, stdout="", stderr="Error from server: forbidden")
            warnings = [WARN.format(rule="replicas must be 2 or more", name=name), WARN.format(rule="needs a priority class", name=name)]
            return SimpleNamespace(returncode=0, stdout="", stderr="\n".join(warnings[: 2 if name == "shop" else 1]))
        out = io.StringIO()
        with mock.patch.object(policy, "kubectl", side_effect=kubectl), mock.patch.object(sys, "argv", ["policy-report.py", "--format", "json"]), \
                contextlib.redirect_stdout(out):
            policy.main()
        report = json.loads(out.getvalue())
        self.assertEqual((3, 3), (report["workloads"], report["violating"]))
        self.assertEqual({"replicas must be 2 or more": 2, "needs a priority class": 1}, report["rules"])
        self.assertTrue(all("status" not in item and "managedFields" not in item["metadata"] for item in submitted))
        self.assertEqual("Error from server: forbidden", next(r for r in report["results"] if r["name"] == "nightly")["error"])


if __name__ == "__main__":
    unittest.main()
