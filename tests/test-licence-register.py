#!/usr/bin/env python3
"""Guards for the third-party licence register: every SBOM entry needs a valid
record, a record without an SBOM entry fails too, and a release bundle ships
THIRD_PARTY_NOTICES under its signed manifest or is not built at all."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64
RECORDS = {
    "docker.io/library/postgres": "PostgreSQL",
    "helm-chart:vault": "MPL-2.0",
    "k3s": "Apache-2.0",
    "helm": "Apache-2.0",
}


def register(records=RECORDS, **overrides):
    entries = []
    for name, licence in records.items():
        entry = {"name": name, "licence": licence, "source": "https://example.org/source",
                 "customer_use": "allowed", "obligations": "Keep the notice."}
        entry.update(overrides.get(name, {}))
        entries.append("  - " + "\n    ".join(f"{key}: {value!r}" for key, value in entry.items()))
    return "components:\n" + "\n".join(entries) + "\n"


class LicenceRegisterTest(unittest.TestCase):
    def setUp(self):
        """A one-image repository with the real release.py, so bundles are built from a commit."""
        self.repo = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.repo / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/release.py", self.repo / "scripts/release.py")
        (self.repo / "k8s").mkdir()
        (self.repo / "k8s/Chart.yaml").write_text("apiVersion: v2\nname: swirl-cloud\nversion: 9.9.9\n")
        (self.repo / "k8s/db.yaml").write_text(f"image: docker.io/library/postgres:18@{DIGEST}\n")
        (self.repo / "config").mkdir()
        (self.repo / "config/platform.env").write_text(
            "DEFAULT_K3S_INSTALL_VERSION=v1.36.4+k3s1\nDEFAULT_HELM_VERSION=v3.22.0\nDEFAULT_VAULT_CHART_VERSION=0.34.1\n")

    def commit(self, text):
        (self.repo / "config/third-party-licences.yaml").write_text(text)
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.org"]
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "test"]):
            subprocess.run(["git", *identity, *command], cwd=self.repo, check=True, capture_output=True)

    def release(self, *arguments):
        return subprocess.run([sys.executable, str(self.repo / "scripts/release.py"), *arguments], cwd=self.repo,
                              capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def test_bundle_ships_notices_under_the_manifest(self):
        self.commit(register())
        result = self.release("bundle", "--output-dir", str(self.repo / "dist"))
        self.assertEqual(0, result.returncode, result.stderr)
        bundle = self.repo / "dist/swirl-cloud-9.9.9"
        notices = (bundle / "THIRD_PARTY_NOTICES").read_text()
        self.assertIn("docker.io/library/postgres 18\n  Licence: PostgreSQL", notices)
        self.assertIn("helm-chart:vault 0.34.1\n  Licence: MPL-2.0", notices)
        self.assertIn("  THIRD_PARTY_NOTICES\n", (bundle / "SHA256SUMS").read_text())
        self.assertEqual(0, self.release("verify", str(bundle)).returncode)

    def test_incomplete_or_invalid_register_fails(self):
        cases = {
            "in the SBOM but not in": register({k: v for k, v in RECORDS.items() if k != "helm-chart:vault"}),
            "in config/third-party-licences.yaml but not in the SBOM": register({**RECORDS, "docker.io/library/mongo": "SSPL-1.0"}),
            "customer_use allowed or decision-needed": register(**{"k3s": {"customer_use": "maybe"}}),
            "licence must be an SPDX expression": register(**{"helm": {"licence": "Apache 2"}}),
            "needs exactly": register(**{"helm": {"reviewed": "yes"}}),
        }
        self.commit(register())  # The inventory reads tracked files; the register is read from the working tree.
        for message, text in cases.items():
            with self.subTest(message):
                (self.repo / "config/third-party-licences.yaml").write_text(text)
                result = self.release("notices")
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
        self.commit(cases["in the SBOM but not in"])
        result = self.release("bundle", "--output-dir", str(self.repo / "dist"))
        self.assertIn("helm-chart:vault: in the SBOM but not in", result.stderr)
        self.assertFalse((self.repo / "dist").exists())  # No partial bundle is left behind.


if __name__ == "__main__":
    unittest.main()
