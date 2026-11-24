#!/usr/bin/env python3
"""Guards for --vulnerability-report --package: exact, case-insensitive package
names across the SBOM reports, one line per workload and version."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("report", ROOT / "scripts/vulnerability-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def sbom(namespace, name, repository, components):
    return {"metadata": {"namespace": namespace, "name": f"replicaset-{name}",
                         "labels": {"trivy-operator.resource.kind": "ReplicaSet", "trivy-operator.resource.name": name}},
            "report": {"artifact": {"repository": repository, "tag": "1.0"},
                       "components": {"components": [{"name": n, "version": v} for n, v in components]}}}


class PackageUsage(unittest.TestCase):
    def test_exact_names_across_workloads(self):
        reports = [sbom("apps", "shop", "acme/shop", [("log4j-core", "2.17.1"), ("log4j-api", "2.17.1")]),
                   sbom("infra", "gitlab", "gitlab/gitlab-ce", [("Log4J-Core", "2.20.0")]),
                   sbom("infra", "grafana", "grafana/grafana", [("openssl", "3.0")])]
        self.assertEqual([("apps", "ReplicaSet/shop", "acme/shop:1.0", "2.17.1"), ("infra", "ReplicaSet/gitlab", "gitlab/gitlab-ce:1.0", "2.20.0")],
                         report.package_usage("log4j-core", reports))
        self.assertEqual([], report.package_usage("log4j", reports))


if __name__ == "__main__":
    unittest.main()
