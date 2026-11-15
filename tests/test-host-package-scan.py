#!/usr/bin/env python3
"""Guards for the server package scan, run with a fake trivy: a working scan
writes each finding once with its success and time; a failed scan keeps the
previous findings and their time and reports the failure; the K3s binaries are
scanned as libraries; the platform-reports snapshot tracks the findings."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
FINDING = 'swirl_cloud_host_vulnerability{vuln_id="CVE-2026-1",severity="High",package="openssl",installed_version="3.0.13-1",fixed_version="3.0.13-2"} 1'
BINARY = 'swirl_cloud_host_vulnerability{vuln_id="CVE-2026-3",severity="High",package="stdlib",installed_version="v1.24.1",fixed_version="1.24.4"} 1'
FAKE_TRIVY = textwrap.dedent(f"""\
    #!/bin/sh
    echo "$*" >> "$CALLS"
    [ "$TRIVY_FAIL" = true ] && exit 1
    case "$*" in
      *"--pkg-types os"*) printf '\\n%s\\n%s\\n' '{FINDING}' '{FINDING}' ;;
      *"--pkg-types library"*) printf '\\n%s\\n' '{BINARY}' ;;
    esac
    """)


def scan_script():
    documents = [document for document in yaml.safe_load_all((ROOT / "k8s/platform/host-package-scan.yaml").read_text()) if document]
    return next(document for document in documents if document["kind"] == "ConfigMap")["data"]["scan.sh"]


class HostPackageScan(unittest.TestCase):
    def run_scan(self, directory, fail=False):
        env = dict(os.environ, PATH=f"{directory / 'bin'}:{os.environ['PATH']}", CALLS=str(directory / "calls"),
                   TRIVY_FAIL="true" if fail else "false", HOST_ROOT=str(directory / "host"),
                   TEXTFILE_DIR=str(directory / "textfile"), TMPDIR=str(directory / "work"), SCAN_ONCE="true",
                   TRIVY_SERVER="http://trivy:4954", TEMPLATE="/dev/null")
        result = subprocess.run(["sh", str(directory / "scan.sh")], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (directory / "textfile/swirl_cloud_host_vulnerabilities.prom").read_text()

    def test_scan_writes_findings_once_and_keeps_them_when_a_scan_fails(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            for sub in ("bin", "textfile", "work", "host/usr/local/bin", "host/var/lib/rancher/k3s/data/current/bin"):
                (directory / sub).mkdir(parents=True)
            trivy = directory / "bin/trivy"
            trivy.write_text(FAKE_TRIVY)
            trivy.chmod(trivy.stat().st_mode | stat.S_IEXEC)
            (directory / "scan.sh").write_text(scan_script())

            first = self.run_scan(directory)
            self.assertEqual(first.count(FINDING), 1)
            self.assertEqual(first.count(BINARY), 1)
            self.assertIn("swirl_cloud_host_vulnerability_scan_success 1", first)
            stamp = next(line for line in first.splitlines() if line.startswith("swirl_cloud_host_vulnerability_scan_timestamp_seconds"))
            calls = (directory / "calls").read_text().splitlines()
            self.assertEqual(len(calls), 3)
            self.assertIn("--ignore-unfixed", calls[0])
            self.assertIn(f"{directory}/host/var/lib/longhorn", calls[0])
            self.assertTrue(calls[2].endswith("/host/var/lib/rancher/k3s/data/current/bin"))
            self.assertEqual(oct((directory / "textfile/swirl_cloud_host_vulnerabilities.prom").stat().st_mode & 0o777), "0o644")

            second = self.run_scan(directory, fail=True)
            self.assertIn("swirl_cloud_host_vulnerability_scan_success 0", second)
            self.assertIn(FINDING, second)
            self.assertIn(stamp, second)
            self.assertEqual(list((directory / "textfile").glob(".swirl*")), [])

    def test_reports_track_server_findings_as_images(self):
        reports = (ROOT / "k8s/platform/platform-reports.yaml").read_text()
        self.assertIn("or swirl_cloud:host_vulnerability_id{severity=~", reports)
        rules = (ROOT / "k8s/files/prometheus/rules.yaml").read_text()
        self.assertIn("record: swirl_cloud:host_vulnerability_id", rules)
        self.assertIn("alert: HostPackageScanFailing", rules)


if __name__ == "__main__":
    unittest.main()
