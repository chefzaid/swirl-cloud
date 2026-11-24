#!/usr/bin/env python3
"""Guards for the compliance report: a control passes only with evidence and no
failing series, fails on any failing series, has no evidence when a query fails
or finds nothing, and every PromQL expression of the control catalog parses."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("compliance", ROOT / "scripts/compliance-report.py")
compliance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compliance)
CATALOG = yaml.safe_load((ROOT / "k8s/files/compliance-controls.yaml").read_text())


class FakePrometheus:
    def __init__(self, answers):
        self.answers = answers

    def query(self, expr):
        answer = self.answers.get(expr, [])
        if isinstance(answer, Exception):
            raise answer
        return {"result": answer}


def series(value="1", **labels):
    return {"metric": labels, "value": [0, value]}


class ComplianceReport(unittest.TestCase):
    def test_statuses(self):
        catalog = {"frameworks": {"nis2": "NIS2"}, "controls": [
            {"id": "ok", "title": "OK", "objective": "", "frameworks": {"nis2": ["21"]},
             "checks": [{"id": "a", "description": "", "present": "up_a", "failing": "down_a"}]},
            {"id": "bad", "title": "Bad", "objective": "", "frameworks": {"nis2": ["21"]},
             "checks": [{"id": "b", "description": "", "present": "up_b", "failing": "down_b"}]},
            {"id": "none", "title": "None", "objective": "", "frameworks": {},
             "checks": [{"id": "c", "description": "", "present": "up_c", "failing": "down_c"},
                        {"id": "d", "description": "", "present": "up_d", "failing": "down_d"}]},
            {"id": "paper", "title": "Paper", "objective": "", "frameworks": {"nis2": ["20"]}, "manual": ["Signed policy"]}]}
        prometheus = FakePrometheus({"up_a": [series()], "up_b": [series()], "down_b": [series(node="n1")],
                                     "up_c": [], "up_d": RuntimeError("timeout")})
        results = {r["id"]: r for r in compliance.evaluate(catalog, prometheus)}
        self.assertEqual(compliance.PASS, results["ok"]["status"])
        self.assertEqual(compliance.FAIL, results["bad"]["status"])
        self.assertEqual(["node=n1 (1)"], results["bad"]["checks"][0]["details"])
        self.assertEqual(compliance.NO_EVIDENCE, results["none"]["status"])
        self.assertEqual(compliance.MANUAL, results["paper"]["status"])
        summary = compliance.framework_summary(catalog, list(results.values()))["nis2"]
        self.assertEqual((3, 2, 1, 1, 1), (summary["controls"], summary["automated"], summary["passing"], summary["failing"], summary["manual"]))

    @unittest.skipUnless(shutil.which("promtool"), "promtool is required")
    def test_every_catalog_expression_parses(self):
        rules = [{"record": f"check:{check['id'].replace('-', '_')}:{part}", "expr": check[part]}
                 for control in CATALOG["controls"] for check in control.get("checks", []) for part in ("present", "failing")]
        self.assertTrue(rules)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.yaml"
            path.write_text(yaml.safe_dump({"groups": [{"name": "catalog", "rules": rules}]}))
            result = subprocess.run(["promtool", "check", "rules", str(path)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
