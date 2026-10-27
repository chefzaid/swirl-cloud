#!/usr/bin/env python3
"""Guards for the third-party licence register and the release VEX: every SBOM
entry needs a valid record, a record without an SBOM entry fails too, a release
bundle ships THIRD_PARTY_NOTICES and VEX statements under its signed manifest or
is not built at all, an expired exception never reaches the VEX, and an exception
for an image the release does not ship stops the bundle."""
import json
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


class VexTest(LicenceRegisterTest):
    EXCEPTION = """  - image: library/postgres
    vulnerability: {vulnerability}
    owner: platform
    triaged: "2026-01-01"
    expires: "{expires}"
    reason: {reason}
    status: {status}
{justification}"""

    def exceptions(self, *entries):
        (self.repo / "k8s/files").mkdir(parents=True, exist_ok=True)
        text = "exceptions:\n" + "".join(self.EXCEPTION.format(**entry) for entry in entries)
        (self.repo / "k8s/files/vulnerability-exceptions.yaml").write_text(text)

    def bundle(self):
        self.commit(register())
        result = self.release("bundle", "--output-dir", str(self.repo / "dist"))
        return result, self.repo / "dist/swirl-cloud-9.9.9"

    def test_bundle_ships_valid_statements_only(self):
        self.exceptions(
            {"vulnerability": "CVE-2026-1", "expires": "2999-01-01", "reason": "Not built in.", "status": "not_affected",
             "justification": "    justification: vulnerable_code_not_present\n"},
            {"vulnerability": "CVE-2026-2", "expires": "2999-01-01", "reason": "Fix pending.", "status": "affected",
             "justification": ""},
            {"vulnerability": "CVE-2020-3", "expires": "2020-01-01", "reason": "Expired.", "status": "affected",
             "justification": ""})
        result, bundle = self.bundle()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("  vex.openvex.json\n", (bundle / "SHA256SUMS").read_text())
        document = json.loads((bundle / "vex.openvex.json").read_text())
        self.assertEqual("https://openvex.dev/ns/v0.2.0", document["@context"])
        statements = {s["vulnerability"]["name"]: s for s in document["statements"]}
        self.assertEqual({"CVE-2026-1", "CVE-2026-2"}, set(statements))
        self.assertEqual(("not_affected", "vulnerable_code_not_present"),
                         (statements["CVE-2026-1"]["status"], statements["CVE-2026-1"]["justification"]))
        self.assertIn("Accepted until 2999-01-01", statements["CVE-2026-2"]["action_statement"])
        self.assertIn(f"pkg:oci/postgres@{DIGEST.replace(':', '%3A')}", statements["CVE-2026-1"]["products"][0]["@id"])

    def test_exception_for_an_unshipped_image_stops_the_bundle(self):
        self.exceptions({"vulnerability": "CVE-2026-1", "expires": "2999-01-01", "reason": "x", "status": "affected",
                         "justification": ""})
        text = (self.repo / "k8s/files/vulnerability-exceptions.yaml").read_text()
        (self.repo / "k8s/files/vulnerability-exceptions.yaml").write_text(text.replace("library/postgres", "library/mongo"))
        result, _ = self.bundle()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("library/mongo CVE-2026-1", result.stderr)
        self.assertFalse((self.repo / "dist").exists())


if __name__ == "__main__":
    unittest.main()
