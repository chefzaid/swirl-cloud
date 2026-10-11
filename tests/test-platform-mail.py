#!/usr/bin/env python3
"""Guards for the platform mail relay: the password never reaches process
arguments, output or error messages, nothing is stored when the relay refuses
the login, and Alertmanager's rendered configuration stays valid with mail on
and off."""
import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import smtplib
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("platform_mail_cli", ROOT / "scripts/configure-platform-mail.py")
mail = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mail)
from lib import platform_mail  # noqa: E402
from lib.onboarding_services import Services  # noqa: E402

PASSWORD = 're_"pass\\word with spaces'


class FakeVault:
    def __init__(self, smtp=None):
        self.data = {"infra/keycloak": ({"sso_primary_email": "admin@example.com"}, 1)}
        if smtp:
            self.data["infra/smtp"] = (smtp, 1)
        self.writes = []

    def request(self, method, path, data=None, *, missing=False, **_kwargs):
        key = path.split("/v1/secret/")[1].split("/", 1)[1]
        if method == "POST":
            self.writes.append(data["data"])
            version = self.data.get(key, ({}, 0))[1] + 1
            self.data[key] = (data["data"], version)
            return {}
        if key not in self.data:
            return None
        values, version = self.data[key]
        return {"data": {"data": values, "metadata": {"version": version}}}


class FakeSMTP:
    instances = []

    def __init__(self, host, port, local_hostname=None, timeout=None, context=None):
        self.host, self.port, self.logins, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def login(self, username, password):
        if password != PASSWORD:
            # A server may echo what it received; it must never be shown.
            raise smtplib.SMTPAuthenticationError(535, ("rejected " + password).encode())
        self.logins.append(username)

    def mail(self, sender):
        return 250, b"ok"

    def rset(self):
        pass

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        pass

    def close(self):
        pass


class PlatformMail(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances = []
        self.calls = []

    def kubectl_run(self, args, input=None, **kwargs):
        """Answer the configurator's kubectl calls as an installed platform would."""
        self.calls.append([str(argument) for argument in args])
        command = " ".join(args)
        rendered = base64.b64encode(f"global:\n  smtp_auth_password: {json.dumps(PASSWORD)}\n".encode()).decode()
        answers = {
            "get configmap swirl-cloud-identity": {"data": {"PLATFORM_DOMAIN": "example.com", "ORGANIZATION_NAME": "Example"}},
            "get externalsecret": {"status": {"refreshTime": "2999-01-01T00:00:00Z",
                                              "conditions": [{"type": "Ready", "status": "True"}]}},
            "get secret alertmanager-config": {"data": {"alertmanager.yml": rendered}},
            "get deployment alertmanager": {"metadata": {"name": "alertmanager"}},
        }
        output = next((json.dumps(value) for key, value in answers.items() if key in command),
                      "" if "--ignore-not-found" in args else "{}")
        if isinstance(input, bytes):
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 0, output, "")

    def configure(self, vault, password=PASSWORD, extra=()):
        argv = ["configure-platform-mail.py", "--host", "smtp.example.com", "--username", "resend",
                "--password-stdin", "--test-to", "ops@example.com", *extra]

        @contextmanager
        def vault_session(_self):
            yield vault

        output, errors = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(sys, "stdin", io.StringIO(password + "\n")), \
                patch.object(mail.subprocess, "run", side_effect=self.kubectl_run), \
                patch.object(platform_mail.smtplib, "SMTP_SSL", FakeSMTP), \
                patch.object(Services, "_vault", vault_session), \
                redirect_stdout(output), redirect_stderr(errors):
            try:
                mail.main()
                failure = None
            except mail.ServiceError as error:
                failure = str(error)
        return output.getvalue() + errors.getvalue(), failure

    def test_password_never_reaches_arguments_output_or_errors(self):
        vault = FakeVault()
        output, failure = self.configure(vault)
        self.assertIsNone(failure)
        self.assertEqual(1, len(vault.writes))
        self.assertEqual(PASSWORD, vault.writes[0]["password"])
        self.assertEqual({"enabled": "true", "host": "smtp.example.com", "port": "465", "tls": "ssl",
                          "username": "resend", "from_address": "cloud@example.com", "from_name": "Example Cloud",
                          "alert_to": "admin@example.com"}, {k: v for k, v in vault.writes[0].items() if k != "password"})
        self.assertEqual(1, len(FakeSMTP.instances[-1].sent))
        self.assertTrue(any("rollout" in call for call in self.calls))
        self.assertNotIn(PASSWORD, json.dumps(self.calls))
        self.assertNotIn(PASSWORD, output)

    def test_nothing_is_stored_when_the_relay_refuses_the_login(self):
        vault = FakeVault()
        wrong = PASSWORD + "-revoked"
        output, failure = self.configure(vault, password=wrong)
        self.assertIn("refused the username or password (SMTP 535)", failure)
        self.assertEqual([], vault.writes)
        self.assertFalse(any("rollout" in call for call in self.calls))
        for text in (failure, output, json.dumps(self.calls)):
            self.assertNotIn(wrong, text)

    def test_unchanged_settings_restart_nothing(self):
        vault = FakeVault()
        self.configure(vault)
        self.calls = []
        output, failure = self.configure(vault)
        self.assertIsNone(failure)
        self.assertEqual(1, len(vault.writes))
        self.assertIn("unchanged", output)
        self.assertFalse(any("rollout" in call or "annotate" in call for call in self.calls))

    def test_values_that_would_break_headers_yaml_or_json_are_rejected(self):
        identity = {"PLATFORM_DOMAIN": "example.com", "ORGANIZATION_NAME": "Example"}
        good = dict(host="smtp.example.com", port=None, tls="ssl", username="resend", sender=None,
                    from_name=None, alert_to="ops@example.com")
        self.assertEqual("Example Cloud", mail.settings_from(SimpleNamespace(**good), identity, "")["from_name"])
        for field, value in (("host", "smtp.example.com\nRCPT"), ("host", "-oProxyCommand=x"), ("port", 70000),
                             ("username", "two words"), ("sender", "cloud@example.com\r\nBcc: x@y.z"),
                             ("sender", "not-an-address"), ("from_name", 'Ex" <evil@example.com>'),
                             ("from_name", "line\nbreak"), ("from_name", "back\\slash"),
                             ("alert_to", 'ops"@example.com'), ("alert_to", "ops@example.com\nx"),
                             ("alert_to", ",".join(f"o{n}@example.com" for n in range(11)))):
            with self.subTest(field=field, value=value), self.assertRaises(mail.ServiceError):
                mail.settings_from(SimpleNamespace(**{**good, field: value}), identity, "")

    def test_alertmanager_configuration_renders_for_mail_on_and_off(self):
        """Render the ExternalSecret template with Helm's Go-template engine (as External Secrets does)."""
        documents = yaml.safe_load_all((ROOT / "k8s/platform/monitoring.yaml").read_text())
        resource = next(d for d in documents if d and d.get("kind") == "ExternalSecret"
                        and d["metadata"]["name"] == "alertmanager-config")
        template = resource["spec"]["target"]["template"]["data"]["alertmanager.yml"]
        self.assertEqual([{"extract": {"key": "infra/smtp"}}], resource["spec"]["dataFrom"])
        on = {"enabled": "true", "host": "smtp.example.com", "port": "465", "tls": "ssl", "username": "resend",
              "password": PASSWORD, "from_address": "cloud@example.com", "from_name": "Example Cloud",
              "alert_to": "ops@example.com,lead@example.com"}
        off = {"enabled": "false", "host": "", "port": "465", "tls": "ssl", "username": "", "password": "",
               "from_address": "", "from_name": "", "alert_to": ""}
        configurations = {}
        with tempfile.TemporaryDirectory() as directory:
            chart = Path(directory)
            (chart / "templates").mkdir()
            (chart / "Chart.yaml").write_text("apiVersion: v2\nname: render\nversion: 0.1.0\n")
            (chart / "templates/config.yaml").write_text("{{- with .Values }}\n" + template + "{{- end }}\n")
            for name, values in (("on", on), ("off", off)):
                (chart / "values.yaml").write_text(json.dumps(values))
                result = subprocess.run([shutil.which("helm") or "helm", "template", "render", str(chart)],
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(0, result.returncode, result.stderr)
                configurations[name] = yaml.safe_load(result.stdout)
        enabled, disabled = configurations["on"], configurations["off"]
        self.assertEqual("smtp.example.com:465", enabled["global"]["smtp_smarthost"])
        self.assertEqual('"Example Cloud" <cloud@example.com>', enabled["global"]["smtp_from"])
        self.assertEqual(PASSWORD, enabled["global"]["smtp_auth_password"])
        self.assertIs(True, enabled["global"]["smtp_force_implicit_tls"])
        self.assertEqual("example.com", enabled["global"]["smtp_hello"])
        receivers = {receiver["name"]: receiver for receiver in enabled["receivers"]}
        self.assertEqual("ops@example.com,lead@example.com", receivers["email"]["email_configs"][0]["to"])
        self.assertTrue(receivers["gitlab"]["webhook_configs"])
        # Notifications are paused until the owner approves restoring them (TODO.md §14).
        self.assertEqual({"name": "paused"}, receivers["paused"])
        self.assertEqual("paused", enabled["route"]["receiver"])
        self.assertNotIn("routes", enabled["route"])
        self.assertFalse([key for key in disabled["global"] if key.startswith("smtp_")])
        receivers = {receiver["name"]: receiver for receiver in disabled["receivers"]}
        self.assertEqual({"name": "email"}, receivers["email"])
        self.assertTrue(receivers["gitlab"]["webhook_configs"])
        self.assertEqual(enabled["route"], disabled["route"])


if __name__ == "__main__":
    unittest.main()
