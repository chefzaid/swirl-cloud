#!/usr/bin/env python3
"""Guards for customer assurance documents: a questionnaire never silently drops a
catalog control or shows an unverified mapping without saying so, a contract annex
lists every sub-processor in the external-services inventory and never reads
Secrets, a generic annex never touches the cluster, and sheets cannot run as
spreadsheet formulas."""
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("assurance_pack", ROOT / "scripts/assurance-pack.py")
pack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pack)
NODE = {"metadata": {"name": "node-1", "labels": {"topology.kubernetes.io/region": "=eu-west-gra"}},
        "status": {"capacity": {}, "addresses": [], "nodeInfo": {}}}


class AssurancePackTest(unittest.TestCase):
    def run_pack(self, *arguments):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        log = directory / "calls.log"
        kubectl = directory / "kubectl"
        kubectl.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, sys
            open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
            if any("secret" in arg.lower() for arg in sys.argv[1:]):
                sys.exit("Secrets must never be read")
            print(json.dumps({{"items": [{json.dumps(NODE)}]}}))
            """))
        kubectl.chmod(0o755)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/assurance-pack.py"), *arguments], capture_output=True,
                                text=True, cwd=directory, env={**os.environ, "PATH": f"{directory}:{os.environ['PATH']}"})
        return result, log.read_text() if log.exists() else "", directory

    def test_every_catalog_control_reaches_the_questionnaire(self):
        catalog = yaml.safe_load(pack.CATALOG.read_text())
        for name in (p.stem for p in pack.QUESTIONNAIRES.glob("*.yaml")):
            with self.subTest(name):
                document = pack.questionnaire(name, live=False)
                answered = {a["control"] for q in document["questions"] for a in q["answers"]}
                self.assertEqual({c["id"] for c in catalog["controls"]}, answered | set(document["unmapped_controls"]))
        broken = {"controls": {"identity-sso": ["IAM-1"]}}
        with self.assertRaises(SystemExit) as raised:
            original = pack.QUESTIONNAIRES
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "broken.yaml").write_text(yaml.safe_dump(broken))
                pack.QUESTIONNAIRES = Path(directory)
                try:
                    pack.load_mapping("broken", catalog)
                finally:
                    pack.QUESTIONNAIRES = original
        self.assertIn("admin-mfa: missing from broken.yaml", str(raised.exception))
        self.assertIn("IAM-1 is not a CCM v4 control ID", str(raised.exception))

    def test_unverified_mapping_is_marked_everywhere(self):
        mapping = yaml.safe_load((pack.QUESTIONNAIRES / "caiq-v4.yaml").read_text())
        if mapping.get("verified"):
            self.skipTest("the mapping has been verified")
        result, calls, directory = self.run_pack("questionnaire", "caiq-v4", "--output-dir", "out")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", calls)  # Without --live the questionnaire never contacts the cluster.
        self.assertIn("Unverified mapping", result.stdout)
        self.assertIn("unverified", result.stderr)
        [written] = (directory / "out").iterdir()
        self.assertEqual(0o700, stat.S_IMODE(written.stat().st_mode))
        rows = list(csv.DictReader(io.StringIO((written / "caiq-v4.csv").read_text())))
        self.assertTrue(rows and all(row["mapping"] == "unverified" for row in rows))
        self.assertFalse(json.loads((written / "caiq-v4.json").read_text())["verified"])

    def test_annex_lists_every_sub_processor_and_reads_no_secret(self):
        result, calls, directory = self.run_pack("contract-annex", "--format", "json", "--provider-name", "Example SAS",
                                                 "--output-dir", "out")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("secret", calls.lower())
        annex = json.loads(result.stdout)
        inventory = pack.load_script("asset-inventory")
        expected = [s["service"] for s in inventory.external_services(inventory.EXTERNAL_SERVICES.read_text())
                    if s["category"] in pack.SUB_PROCESSOR_SECTIONS]
        self.assertTrue(expected)
        self.assertEqual([pack.plain(s) for s in expected], [s["service"] for s in annex["sub_processors"]])
        self.assertEqual(len(expected) + 1, len(annex["register_of_information"]))
        self.assertEqual("=eu-west-gra", annex["register_of_information"][0]["data_locations"])
        self.assertEqual({"Example SAS"}, {r["subcontractor_of"] for r in annex["register_of_information"][1:]})
        self.assertFalse(any("](" in json.dumps(s) for s in annex["sub_processors"]))  # No dangling guide links.
        [written] = (directory / "out").iterdir()
        for path in written.iterdir():
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode), path)
        markdown = (written / "contract-annex.md").read_text()
        self.assertIn("| node-1 | worker | =eu-west-gra |", markdown)
        register = list(csv.DictReader(io.StringIO((written / "register-of-information.csv").read_text())))
        self.assertEqual("'=eu-west-gra", register[0]["data_locations"])

    def test_generic_annex_never_touches_the_cluster(self):
        result, calls, _ = self.run_pack("contract-annex", "--generic")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", calls)
        self.assertIn("[to complete]: the servers and their locations", result.stdout)

    def test_csv_cells_cannot_run_as_formulas(self):
        sheet = pack.to_csv(["a"], [{"a": "=HYPERLINK(1)"}, {"a": "@SUM(1)"}, {"a": "plain"}])
        self.assertEqual(["'=HYPERLINK(1)", "'@SUM(1)", "plain"], [row["a"] for row in csv.DictReader(io.StringIO(sheet))])


if __name__ == "__main__":
    unittest.main()
