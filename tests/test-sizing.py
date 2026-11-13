#!/usr/bin/env python3
"""Guards for sizing profiles (TODO §26): standard renders exactly as before, a larger
profile applies every entry and records itself on the root Application, a manifest
that drifts from an entry fails the render, Helm and scripts/lib/sizing.py agree,
and volumes never shrink."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import install_inputs  # noqa: E402
import sizing  # noqa: E402

VALUES = {"publicDomain": "example.com", "internalDnsZone": "internal.example.com",
          "gitopsRepositoryURL": "https://example.com/swirl-cloud.git", "cloudflareAccessTeamName": "team",
          "appsEnabled": True, "deschedulerEnabled": True, "organizationName": "Example", "organizationSlug": "example"}


def render(chart=ROOT / "k8s", **values):
    with tempfile.NamedTemporaryFile("w", suffix=".json") as file:
        json.dump({**VALUES, **values}, file)
        file.flush()
        return subprocess.run(["helm", "template", "swirl-cloud", str(chart), "--namespace", "infra", "--skip-tests",
                               "--values", file.name], capture_output=True, text=True)


def documents(text):
    return {(d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"]): d
            for d in yaml.safe_load_all(text) if d}


def container(document, name):
    return next(c for c in document["spec"]["template"]["spec"]["containers"] if c["name"] == name)


class Sizing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.default = render()
        cls.standard = render(sizingProfile="standard")
        cls.large = render(sizingProfile="large")
        for result in (cls.default, cls.standard, cls.large):
            if result.returncode:
                raise AssertionError(result.stderr)
        cls.docs = documents(cls.large.stdout)

    def test_standard_renders_exactly_as_without_a_profile(self):
        self.assertEqual(self.default.stdout, self.standard.stdout)

    def test_large_applies_resources_storage_and_service_settings(self):
        docs = self.docs
        self.assertEqual(container(docs[("Deployment", "infra", "gitlab")], "gitlab")["resources"]["limits"],
                         {"cpu": "6", "memory": "16Gi"})
        omnibus = next(e["value"] for e in container(docs[("Deployment", "infra", "gitlab")], "gitlab")["env"]
                       if e["name"] == "GITLAB_OMNIBUS_CONFIG")
        self.assertIn("puma['worker_processes'] = 4", omnibus)
        runner = docs[("ConfigMap", "gitlab-runners", "gitlab-runner-config")]["data"]["config.template.toml"]
        self.assertIn("concurrent = 6", runner)
        self.assertIn('memory_limit = "8Gi"', runner)
        self.assertEqual(docs[("PersistentVolumeClaim", "infra", "postgres-v18-pvc")]["spec"]["resources"]["requests"]
                         ["storage"], "20Gi")
        self.assertIn("shared_buffers=2GB", container(docs[("Deployment", "infra", "postgres")], "postgres")["args"])
        es = container(docs[("Deployment", "infra", "elasticsearch")], "elasticsearch")
        self.assertIn({"name": "ES_JAVA_OPTS", "value": "-Xms4g -Xmx4g"}, es["env"])
        self.assertIn("--storage.tsdb.retention.size=20GB",
                      container(docs[("Deployment", "infra", "prometheus")], "prometheus")["args"])
        logs = docs[("ConfigMap", "host-agents", "fluent-bit-config")]["data"]["ilm-policy.json"]
        self.assertIn('"min_age": "14d"', logs)

    def test_untouched_documents_keep_their_requests_and_the_root_application_records_the_profile(self):
        standard = documents(self.standard.stdout)
        self.assertEqual(container(self.docs[("Deployment", "infra", "vault-ui-theme")], "ui-theme")["resources"],
                         container(standard[("Deployment", "infra", "vault-ui-theme")], "ui-theme")["resources"])
        values = self.docs[("Application", "infra", "swirl-cloud")]["spec"]["source"]["helm"]["valuesObject"]
        self.assertEqual(values["sizingProfile"], "large")
        self.assertEqual(values["trivy-operator"]["operator"]["scanJobsConcurrentLimit"], 4)
        self.assertNotIn("sizingProfile", documents(self.standard.stdout)[("Application", "infra", "swirl-cloud")]
                         ["spec"]["source"]["helm"]["valuesObject"])

    def test_a_manifest_that_drifts_from_the_profile_fails_the_render(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory) / "k8s"
            shutil.copytree(ROOT / "k8s", chart)
            manifest = chart / "platform/gitlab.yaml"
            manifest.write_text(manifest.read_text().replace("puma['worker_processes'] = 2", "puma['worker_processes'] = 3"))
            result = render(chart, sizingProfile="large")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must occur exactly once", result.stderr)

    def test_an_unknown_profile_fails(self):
        result = render(sizingProfile="huge")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("files/sizing/huge.yaml is missing", result.stderr)

    def test_scripts_apply_the_same_runner_text_as_helm(self):
        raw = next(d for d in yaml.safe_load_all((ROOT / "k8s/platform/gitlab-runner.yaml").read_text())
                   if d and d["kind"] == "ConfigMap")["data"]["config.template.toml"]
        sized = sizing.apply_replacements(raw, "platform/gitlab-runner.yaml", "ConfigMap/gitlab-runner-config", "large")
        rendered = self.docs[("ConfigMap", "gitlab-runners", "gitlab-runner-config")]["data"]["config.template.toml"]
        self.assertEqual(sized.replace("__INTERNAL_DNS_ZONE__", "internal.example.com"), rendered)

    def test_volumes_never_shrink(self):
        live = {"infra/postgres-v18-pvc": "20Gi", "infra/gitlab-data": "40Gi"}
        self.assertEqual(sizing.shrinking({"infra/postgres-v18-pvc": "2Gi", "infra/gitlab-data": "150Gi"}, live),
                         [("infra/postgres-v18-pvc", "20Gi", "2Gi")])
        self.assertEqual(sizing.shrinking({"infra/postgres-v18-pvc": "20480Mi"}, live), [])

    def test_the_suggestion_and_the_installer_choices_follow_the_profiles(self):
        self.assertEqual(sizing.suggested(threads=16, memory_gib=125), "large")
        self.assertEqual(sizing.suggested(threads=8, memory_gib=125), "standard")
        self.assertEqual(sizing.suggested(threads=16, memory_gib=31), "standard")
        self.assertEqual(set(install_inputs.BY_NAME["SIZING_PROFILE"].choices), set(sizing.available()))
        with self.assertRaises(sizing.SizingError):
            sizing.load("../values")


if __name__ == "__main__":
    unittest.main()
