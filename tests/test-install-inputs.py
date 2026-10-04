#!/usr/bin/env python3
"""Guards for the installation input file: data-only import, credential exposure
and complete up-front validation before the installer changes anything."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import install_inputs  # noqa: E402

PASSWORD = "Str0ng!Passw0rd#1"
VALID = {"ORGANIZATION_NAME": "Example Co", "PLATFORM_DOMAIN": "example.com", "SERVER_EXPOSURE": "local",
         "GITOPS_REPOSITORY_URL": "https://github.com/example/swirl-cloud.git",
         "KEYCLOAK_SSO_BOOTSTRAP_USERNAME": "platform-admin", "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD": PASSWORD}


class InstallInputs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def file(self, text, mode=0o600):
        path = self.dir / "install.env"
        path.write_text(textwrap.dedent(text))
        path.chmod(mode)
        return path

    def bash_import(self, path, environment=None):
        script = 'source scripts/lib/install-env.sh; install_env_import "$1"; ' \
                 'printf "%s\\0%s\\0%s\\0" "${ORGANIZATION_NAME-<unset>}" "${GITLAB_GROUP_NAME-<unset>}" "${#INSTALL_ENV_PROBLEMS[@]}"'
        result = subprocess.run(["bash", "-c", script, "import", str(path)], cwd=ROOT, capture_output=True,
                                env={**os.environ, **(environment or {})}, check=True)
        return result.stdout.decode().split("\0")[:3]

    def test_values_are_imported_as_data_and_never_executed(self):
        marker = self.dir / "executed"
        hostile = f"$(touch {marker})`touch {marker}`;touch {marker}"
        path = self.file(f"""\
            SWIRL_CLOUD_INSTALL_INPUTS="1"
            ORGANIZATION_NAME='{hostile}'
            GITLAB_GROUP_NAME="quote \\" and \\\\ $HOME"
            """)
        name, group, _ = self.bash_import(path)
        self.assertEqual(hostile, name)
        self.assertEqual('quote " and \\ $HOME', group)
        self.assertFalse(marker.exists())

    def test_calling_environment_cannot_supply_installation_inputs(self):
        path = self.dir / "install.env"
        install_inputs.write(path, VALID)
        _, group, problems = self.bash_import(path, {"GITLAB_GROUP_NAME": "from-shell", "ORGANIZATION_NAME": "from-shell"})
        self.assertEqual("<unset>", group)
        self.assertEqual("0", problems)

    def test_every_problem_is_reported_before_the_installer_acts(self):
        path = self.file("""\
            SWIRL_CLOUD_INSTALL_INPUTS="1"
            PLATFORM_DOMAIN="not a domain"
            CONTROL_PLANE_COUNT="2"
            CONFIGURE_OFFSITE_BACKUPS="true"
            K3S_INSTALL_VERSION="v1.0.0"
            KEYCLOAK_SSO_BOOTSTRAP_USERNAME="platform-admin"
            KEYCLOAK_SSO_BOOTSTRAP_PASSWORD="leakcheck"
            """, mode=0o640)
        result = subprocess.run([str(ROOT / "install-control-plane.sh"), "--env-file", str(path), "--validate-only"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(1, result.returncode)
        for expected in ("chmod 600", "ORGANIZATION_NAME: required", "PLATFORM_DOMAIN: has an invalid format",
                         "CONTROL_PLANE_COUNT: must be odd", "BACKUP_S3_BUCKET: required", "K3S_INSTALL_VERSION: unknown",
                         "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD: The administrator password"):
            self.assertIn(expected, result.stderr)
        self.assertNotIn("leakcheck", result.stdout + result.stderr)

    def test_written_files_are_private_and_round_trip(self):
        values = {**VALID, "ORGANIZATION_NAME": 'A "quoted" \\ name $(x)'}
        path = self.dir / "nested/install.env"
        install_inputs.write(path, values)
        self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
        self.assertEqual(values, install_inputs.read(path))
        result = subprocess.run([str(ROOT / "install-control-plane.sh"), "--env-file", str(path), "--validate-only"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_tui_never_passes_secrets_as_process_arguments(self):
        fake = self.dir / "bin/whiptail"
        fake.parent.mkdir()
        fake.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, os, sys
            args, queue_path = sys.argv[1:], os.environ["QUEUE"]
            with open(os.environ["ARGV_LOG"], "a") as log:
                log.write(json.dumps(args) + "\\n")
            if "--msgbox" in args:
                sys.exit(0)
            queue = json.load(open(queue_path))
            code, answer = queue.pop(0)
            json.dump(queue, open(queue_path, "w"))
            os.write(int(args[args.index("--output-fd") + 1]), answer.encode())
            sys.exit(code)
            """))
        fake.chmod(0o700)
        token = "cfut_" + "s" * 30
        answers = [[0, "express"], [0, "Example Co"], [0, "example.com"], [0, VALID["GITOPS_REPOSITORY_URL"]], [1, ""],
                   [0, "internet"], [1, ""], [1, ""], [0, token], [0, token], [0, "admin@example.com"],
                   [0, "platform-admin"], [0, PASSWORD], [0, PASSWORD], [0, "save"], [1, ""]]
        (self.dir / "queue.json").write_text(json.dumps(answers))
        output = self.dir / "out/install.env"
        subprocess.run([sys.executable, str(ROOT / "scripts/install-tui.py"), "--output", str(output)], cwd=ROOT,
                       check=True, capture_output=True, env={**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}",
                                                             "QUEUE": str(self.dir / "queue.json"),
                                                             "ARGV_LOG": str(self.dir / "argv.log")})
        arguments = (self.dir / "argv.log").read_text()
        self.assertNotIn(token, arguments)
        self.assertNotIn(PASSWORD, arguments)
        saved = install_inputs.read(output)
        self.assertEqual(token, saved["CLOUDFLARE_API_TOKEN"])
        self.assertEqual(PASSWORD, saved["KEYCLOAK_SSO_BOOTSTRAP_PASSWORD"])


if __name__ == "__main__":
    unittest.main()
