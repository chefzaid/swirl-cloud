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

    def run_tui(self, answers):
        """Drive the setup hub with a fake whiptail that records every argument list."""
        fake = self.dir / "bin/whiptail"
        if not fake.exists():
            fake.parent.mkdir()
            fake.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import json, os, sys
                args, queue_path = sys.argv[1:], os.environ["QUEUE"]
                shown = ""
                if "--textbox" in args:
                    shown = open(args[args.index("--textbox") + 1]).read()
                with open(os.environ["ARGV_LOG"], "a") as log:
                    log.write(json.dumps({"args": args, "textbox": shown}) + "\\n")
                if "--msgbox" in args or "--textbox" in args:
                    sys.exit(0)
                queue = json.load(open(queue_path))
                code, answer = queue.pop(0)
                json.dump(queue, open(queue_path, "w"))
                os.write(int(args[args.index("--output-fd") + 1]), answer.encode())
                sys.exit(code)
                """))
            fake.chmod(0o700)
        (self.dir / "queue.json").write_text(json.dumps(answers))
        output = self.dir / "out/install.env"
        subprocess.run([sys.executable, str(ROOT / "scripts/install-tui.py"), "--output", str(output)], cwd=ROOT,
                       check=True, capture_output=True, timeout=120,
                       env={**os.environ, "PATH": f"{fake.parent}:{os.environ['PATH']}", "KUBECONFIG": "/nonexistent",
                            "QUEUE": str(self.dir / "queue.json"), "ARGV_LOG": str(self.dir / "argv.log"),
                            "XDG_STATE_HOME": str(self.dir / "state")})
        self.assertEqual("[]", (self.dir / "queue.json").read_text())
        calls = [json.loads(line) for line in (self.dir / "argv.log").read_text().splitlines()]
        return install_inputs.read(output), [call["args"] for call in calls], [call["textbox"] for call in calls]

    def express_answers(self, token, password_answers):
        return ([[0, "express"], [0, "Example Co"], [0, "example.com"], [0, VALID["GITOPS_REPOSITORY_URL"]], [1, ""],
                 [0, "internet"], [1, ""], [1, ""], [0, "enter"], [0, token], [0, token], [0, "admin@example.com"],
                 [0, "platform-admin"]] + password_answers + [[0, "save"], [1, ""]])

    def test_tui_never_passes_secrets_as_process_arguments(self):
        token = "cfut_" + "s" * 30
        saved, arguments, _ = self.run_tui(self.express_answers(token, [[0, "enter"], [0, PASSWORD], [0, PASSWORD]]))
        self.assertNotIn(token, json.dumps(arguments))
        self.assertNotIn(PASSWORD, json.dumps(arguments))
        self.assertEqual(token, saved["CLOUDFLARE_API_TOKEN"])
        self.assertEqual(PASSWORD, saved["KEYCLOAK_SSO_BOOTSTRAP_PASSWORD"])

    def test_generated_passwords_meet_policy_and_are_shown_only_from_a_private_file(self):
        token = "cfut_" + "t" * 30
        saved, arguments, shown = self.run_tui(self.express_answers(token, [[0, "generate"]]))
        generated = saved["KEYCLOAK_SSO_BOOTSTRAP_PASSWORD"]
        self.assertTrue(len(generated) >= 16 and any(c.isupper() for c in generated) and any(c.isdigit() for c in generated)
                        and any(not c.isalnum() for c in generated))
        self.assertNotIn(generated, json.dumps(arguments))
        self.assertTrue(any(generated in text for text in shown))
        textbox = next(args[args.index("--textbox") + 1] for args in arguments if "--textbox" in args)
        self.assertFalse(Path(textbox).exists())
    def test_hub_commands_receive_passwords_on_stdin_without_logging_them(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("install_tui", ROOT / "scripts/install-tui.py")
        hub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hub)
        reader = [sys.executable, "-c", "import sys; secret = sys.stdin.read(); print('received', len(secret))"]
        with patch.object(hub, "STATE", self.dir / "state"), patch("builtins.input"), \
                patch.object(hub.subprocess, "run"), patch("sys.stdout"):
            code, log = hub.run_logged("Rotate", reader, stdin_data=PASSWORD + "\n")
        self.assertEqual(0, code)
        self.assertIn("received 18", log.read_text())
        self.assertNotIn(PASSWORD, log.read_text())
        self.assertEqual(0o600, stat.S_IMODE(log.stat().st_mode))
        self.assertEqual(0, json.loads((self.dir / "state/last-run.json").read_text())["code"])

    def test_hub_can_reference_a_secret_file_instead_of_storing_the_secret(self):
        secret = self.dir / "admin.password"
        secret.write_text(PASSWORD + "\n")
        secret.chmod(0o600)
        token = "cfut_" + "u" * 30
        saved, arguments, _ = self.run_tui(self.express_answers(token, [[0, "file"], [0, str(secret)]]))
        self.assertEqual(str(secret), saved["KEYCLOAK_SSO_BOOTSTRAP_PASSWORD_FILE"])
        self.assertNotIn("KEYCLOAK_SSO_BOOTSTRAP_PASSWORD", saved)
        self.assertNotIn(PASSWORD, (self.dir / "out/install.env").read_text())

    def test_secret_file_references_keep_credentials_out_of_the_input_file(self):
        secret = self.dir / "admin.password"
        secret.write_text(PASSWORD + "\n")
        secret.chmod(0o600)
        values = {k: v for k, v in VALID.items() if k != "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD"}
        path = self.dir / "install.env"
        install_inputs.write(path, {**values, "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD_FILE": str(secret)})
        self.assertNotIn(PASSWORD, path.read_text())
        script = ('source scripts/lib/install-env.sh; install_env_import "$1"; '
                  'printf "%s" "${KEYCLOAK_SSO_BOOTSTRAP_PASSWORD-<unset>}"')
        imported = subprocess.run(["bash", "-c", script, "import", str(path)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(PASSWORD, imported.stdout)
        result = subprocess.run([str(ROOT / "install-control-plane.sh"), "--env-file", str(path), "--validate-only"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        secret.chmod(0o640)
        problems = install_inputs.validate({**values, "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD_FILE": str(secret),
                                            install_inputs.VERSION_KEY: "1"})
        self.assertEqual(["KEYCLOAK_SSO_BOOTSTRAP_PASSWORD_FILE: file must not be accessible by group or others (chmod 600)"],
                         problems)
        both = install_inputs.validate({**VALID, "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD_FILE": str(secret),
                                        install_inputs.VERSION_KEY: "1"})
        self.assertTrue(any("not both" in problem for problem in both))

    def test_secrets_on_stdin_complete_the_file_without_being_written(self):
        path = self.dir / "install.env"
        install_inputs.write(path, {k: v for k, v in VALID.items() if k != "KEYCLOAK_SSO_BOOTSTRAP_PASSWORD"})
        command = [str(ROOT / "install-control-plane.sh"), "--env-file", str(path), "--secrets-stdin", "--validate-only"]
        accepted = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, start_new_session=True,
                                  input=f"KEYCLOAK_SSO_BOOTSTRAP_PASSWORD={PASSWORD}\n")
        self.assertEqual(0, accepted.returncode, accepted.stderr)
        self.assertNotIn(PASSWORD, path.read_text())
        rejected = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, start_new_session=True,
                                  input="PLATFORM_DOMAIN=evil.example\n")
        self.assertEqual(1, rejected.returncode)
        self.assertIn("PLATFORM_DOMAIN: only secret inputs may be supplied on stdin", rejected.stderr)

    def test_run_logs_redact_secrets_split_across_output_chunks(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("install_tui", ROOT / "scripts/install-tui.py")
        hub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hub)
        redactor = hub.Redactor([PASSWORD])
        text = "".join(redactor.feed(part) for part in ("before Str0ng!Pa", "ssw0rd#1 after")) + redactor.feed("", final=True)
        self.assertEqual("before [redacted] after", text)
        writer = [sys.executable, "-c", f"import sys; [sys.stdout.write(c) or sys.stdout.flush() for c in {PASSWORD!r} * 2]"]
        with patch.object(hub, "STATE", self.dir / "state"), patch.object(hub, "REDACT", [PASSWORD]), \
                patch("builtins.input"), patch.object(hub.subprocess, "run"), patch("sys.stdout"):
            _, log = hub.run_logged("Echo", writer)
        self.assertEqual("[redacted][redacted]", log.read_text())


if __name__ == "__main__":
    unittest.main()
