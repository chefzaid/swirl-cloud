#!/usr/bin/env python3
"""Guards for the CI security include: every job uses the platform's pinned
Trivy image and server, secret detection always fails on a finding, scans only
block when enforcement is on, and the image scan keeps an SBOM."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI = yaml.safe_load((ROOT / "ci/security.gitlab-ci.yml").read_text())


class CiSecurity(unittest.TestCase):
    def test_jobs(self):
        image = CI[".swirl-trivy"]["image"]["name"]
        self.assertRegex(image, r"^docker\.io/aquasec/trivy:[0-9.]+@sha256:[0-9a-f]{64}$")
        scanner = (ROOT / "k8s/platform/host-package-scan.yaml").read_text()
        self.assertIn(image, scanner, "keep the include on the same Trivy release as the platform")
        self.assertIn("--exit-code 1", CI["swirl-secret-detection"]["script"][0])
        self.assertNotIn("allow_failure", CI["swirl-secret-detection"])
        for job in ("swirl-dependency-scan", "swirl-image-scan"):
            self.assertTrue(CI[job]["allow_failure"])
            self.assertIn('"$SWIRL_SECURITY_ENFORCE" = true', "\n".join(CI[job]["script"]))
            self.assertTrue(all("--server" in line for line in CI[job]["script"] if line.lstrip().startswith("trivy ")))
        self.assertIn("sbom.cdx.json", CI["swirl-image-scan"]["artifacts"]["paths"])


if __name__ == "__main__":
    unittest.main()
