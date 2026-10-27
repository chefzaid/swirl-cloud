#!/usr/bin/env python3
"""Guards for French console text: every installer message has a French entry
that keeps all of its values, no entry outlives its message, English output is
unchanged, a translation failure falls back to English instead of hiding the
message, run records keep English step names, and --json stays English."""
from collections import Counter
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import console_i18n  # noqa: E402

# step/info/warn/error/fail followed by one double-quoted literal, as the installer writes them.
CALL = re.compile(r'(?:^|[;&|{(]|\bthen|\belse|\bdo|\))[ \t]*(?:step|info|warn|error|fail)[ \t]+"((?:[^"\\]|\\.)*)"', re.M)


def table(name):
    rows = {}
    for line in (ROOT / f"scripts/lib/i18n/{name}.fr.tsv").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            english, french = line.split("\t")
            rows[english] = french
    return rows


def installer_messages():
    sources = [ROOT / "install-control-plane.sh", *sorted((ROOT / "scripts/lib").glob("*.sh"))]
    return {message for path in sources for message in CALL.findall(path.read_text()) if message}


class TablesTest(unittest.TestCase):
    def test_installer_table_matches_the_messages(self):
        rows, messages = table("installer"), installer_messages()
        self.assertEqual([], sorted(messages - set(rows)), "messages without a French entry")
        self.assertEqual([], sorted(set(rows) - messages), "entries whose message no longer exists")

    def test_every_entry_keeps_its_values(self):
        for name in ("installer", "status"):
            for english, french in table(name).items():
                with self.subTest(english):
                    self.assertEqual(Counter(console_i18n.TOKEN.findall(english)), Counter(console_i18n.TOKEN.findall(french)))
                    self.assertTrue(french.strip() or not english.strip())

    def test_status_entries_match_the_report_source(self):
        source = (ROOT / "scripts/verify-platform.py").read_text()
        # A check that raises is reported under its function name, title-cased.
        crashed = {name.replace("_", " ").title() for name in re.findall(r"^def check_(\w+)", source, re.M)}
        for english in set(table("status")) - crashed:
            # Word by word, since a summary can be built from several strings; run-record
            # results (success, failed, interrupted) are values, not report source text.
            for word in re.findall(r"\S{3,}", console_i18n.TOKEN.sub(" ", english)):
                self.assertTrue(word in source or word in ("success", "failed", "interrupted"), f"stale status entry: {english}")


class OutputTest(unittest.TestCase):
    def shell(self, language, message, script_dir=ROOT):
        """The installer's own message functions, extracted from install-control-plane.sh."""
        text = (ROOT / "install-control-plane.sh").read_text()
        functions = text[text.index("localize() {"):text.index("\n", text.index("step()  {"))]
        program = (f'SCRIPT_DIR="$1"; GREEN=; YELLOW=; RED=; BLUE=; NC=\n'
                   f'install_record_step() {{ printf "record: %s\\n" "$*"; }}\n{functions}\nstep "$2"; info "$2"')
        env = {**os.environ, "SWIRL_CLOUD_LANG": language}
        return subprocess.run(["bash", "-c", program, "test", str(script_dir), message], capture_output=True, text=True,
                              env=env, check=True).stdout

    def test_installer_messages(self):
        message = "Installing/upgrading Longhorn with 2 default replica(s) for new volumes..."
        self.assertEqual(f"[STEP]  {message}\nrecord: {message}\n[INFO]  {message}\n", self.shell("en", message))
        french = "Installation ou mise à jour de Longhorn avec 2 réplique(s) par défaut pour les nouveaux volumes..."
        self.assertEqual(f"[STEP]  {french}\nrecord: {message}\n[INFO]  {french}\n", self.shell("fr", message))
        self.assertIn("[INFO]  Not in the table: 10.0.0.1\n", self.shell("fr", "Not in the table: 10.0.0.1"))
        broken = self.shell("fr", "Installing ArgoCD...", script_dir=ROOT / "missing")
        self.assertIn("[INFO]  Installing ArgoCD...\n", broken)  # A failed lookup prints the English message.

    def test_values_survive_translation(self):
        os.environ["SWIRL_CLOUD_LANG"] = "fr"
        try:
            for english, values in (
                    ("Invalid SSH target: $target", {"$target": "admin@10.0.0.5"}),
                    ("Existing node $node_name has role $existing_role and IP $registered_ip; refusing to replace it with "
                     "$ENROLLMENT_ROLE at $node_ip.", {"$node_name": "n2", "$existing_role": "worker",
                                                       "$registered_ip": "10.0.0.2", "$ENROLLMENT_ROLE": "server",
                                                       "$node_ip": "10.0.0.9"}),
                    ("$operation failed${details:+: $details}", {"$operation": "DNS update", "${details:+: $details}": ": 403"})):
                message = english
                for token, value in values.items():
                    message = message.replace(token, value)
                french = console_i18n.translate(message, "installer")
                self.assertNotEqual(message, french)
                for value in values.values():
                    self.assertIn(value.strip(": "), french)
        finally:
            os.environ.pop("SWIRL_CLOUD_LANG")

    def test_report_text_follows_the_language_and_json_stays_english(self):
        spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts/verify-platform.py")
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        report = verify.Report()
        report.add(verify.PASS, "Nodes", "3/3 Ready")
        report.add(verify.WARN, "Last installation", "2026-10-27T10:00:00Z: failed after 12 min, 30 step(s)",
                   ["last step: Deploying platform services..."])
        os.environ["SWIRL_CLOUD_LANG"] = "en"
        english = report.text()
        os.environ["SWIRL_CLOUD_LANG"] = "fr"
        try:
            french = report.text()
        finally:
            os.environ.pop("SWIRL_CLOUD_LANG")
        self.assertIn("[PASS] Nodes: 3/3 Ready", english)
        self.assertIn("last step: Deploying platform services...", english)
        self.assertIn("[PASS] Nœuds : 3/3 prêts", french)
        self.assertIn("2026-10-27T10:00:00Z : échec après 12 min, 30 étape(s)", french)
        self.assertIn("dernière étape : Déploiement des services de la plateforme...", french)
        self.assertIn("Bilan : 1 réussi(s), 1 avertissement(s), 0 en échec", french)
        self.assertEqual("3/3 Ready", report.results[0]["summary"])  # --json prints the results unchanged.


class PortalTest(unittest.TestCase):
    def render(self, language):
        return subprocess.run(["helm", "template", "swirl-cloud", str(ROOT / "k8s"), "--show-only", "templates/resources.yaml",
                               "--set", "publicDomain=example.com,internalDnsZone=internal.example.com,"
                               "gitopsRepositoryURL=https://example.com/repo.git,cloudflareAccessTeamName=example",
                               "--set-string", f"portalLanguage={language}"], capture_output=True, text=True)

    def test_french_portal_and_unknown_language(self):
        english, french = self.render(""), self.render("fr")
        self.assertEqual(0, french.returncode, french.stderr)
        self.assertIn("    language: en\n", english.stdout)
        self.assertIn("    language: fr\n", french.stdout)
        self.assertIn("description: Livraison continue GitOps\n", french.stdout)
        self.assertNotIn("description: GitOps continuous delivery\n", french.stdout)
        rejected = self.render("de")
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("portalLanguage must be en or fr", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
