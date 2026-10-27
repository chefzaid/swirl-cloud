#!/usr/bin/env python3
"""Guards for SOC forwarding: each source becomes one RFC 5424 message holding
valid JSON, without collection metadata, with its node, type and severity; lines
that are not audit entries or findings are dropped; invalid settings stop both
renderers; and the configurator records settings where Argo CD and installer
reruns read them, only after a verified TLS connection."""
import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / "k8s/files/soc-forwarding/soc-forwarding.lua"
spec = importlib.util.spec_from_file_location("configure_soc", ROOT / "scripts/configure-soc-forwarding.py")
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


def lua(value):
    """A Python value as a Lua table constructor, as Fluent Bit hands records to Lua."""
    if isinstance(value, dict):
        return "{" + ",".join(f"[{lua(key)}]={lua(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(lua(item) for item in value) + "}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):  # Every byte outside printable ASCII as a decimal escape.
        return '"' + "".join(c if " " <= c <= "~" and c not in '"\\' else "".join(f"\\{b:03d}" for b in c.encode())
                             for c in value) + '"'
    return repr(value) if value == value else "0/0"


@unittest.skipUnless(shutil.which("luajit"), "LuaJIT (Fluent Bit's Lua) is required")
class FormatterTest(unittest.TestCase):
    def format(self, tag, record):
        program = (f'dofile({json.dumps(str(LUA))})\n'
                   f'local code, _, out = soc_record({json.dumps(tag)}, 1.5, {lua(record)})\n'
                   'if code == -1 then print("DROP") else\n'
                   'io.write(code, "\\t", out.soc_appname, "\\t", out.soc_hostname, "\\t", out.soc_msgid, "\\t",'
                   ' out.soc_severity, "\\t", type(out.soc_severity), "\\n", out.soc_message, "\\n") end\n')
        result = subprocess.run(["luajit", "-"], input=program, capture_output=True, text=True, check=True)
        if result.stdout.strip() == "DROP":
            return None
        header, message = result.stdout.rstrip("\n").split("\n")
        code, appname, hostname, msgid, severity, kind = header.split("\t")
        self.assertEqual(("2", "string"), (code, kind))  # The output reads severity only from a string.
        return {"appname": appname, "hostname": hostname, "msgid": msgid, "severity": int(severity),
                "message": json.loads(message)}

    def test_identity_event(self):
        out = self.format("soc.identity", {
            "log": "raw line", "stream": "stdout", "kubernetes": {"host": "node-1", "namespace_name": "infra"},
            "level": "WARN", "event_category": "user",
            "keycloak": {"type": "LOGIN_ERROR", "error": "invalid_user_credentials", "username": 'a"b\\c\n\x01é'}})
        self.assertEqual(("swirl-cloud-identity", "node-1", "LOGIN_ERROR", 5),
                         (out["appname"], out["hostname"], out["msgid"], out["severity"]))
        self.assertEqual({"source": "identity", "node": "node-1", "event": {
            "level": "WARN", "event_category": "user",
            "keycloak": {"type": "LOGIN_ERROR", "error": "invalid_user_credentials", "username": 'a"b\\c\n\x01é'}}},
            out["message"])

    def test_kubernetes_audit_event(self):
        out = self.format("soc.kubernetes-audit", {
            "node": "cp-1", "verb": "delete", "log_source": "kubernetes-audit", "auditID": "abc",
            "responseStatus": {"code": 403}, "sourceIPs": ["10.0.0.1"], "user": {"username": "alice", "groups": []}})
        self.assertEqual(("cp-1", "delete", 5), (out["hostname"], out["msgid"], out["severity"]))
        self.assertEqual(["10.0.0.1"], out["message"]["event"]["sourceIPs"])
        self.assertEqual(403, out["message"]["event"]["responseStatus"]["code"])

    def test_vault_audit_keeps_only_audit_entries(self):
        entry = {"kubernetes": {"host": "node-2", "namespace_name": "infra"}, "type": "response", "time": "t",
                 "request": {"path": "secret/data/x", "operation": "read"}, "auth": {"display_name": "kubernetes-eso"}}
        out = self.format("soc.vault-audit", entry)
        self.assertEqual(("node-2", "response", 6), (out["hostname"], out["msgid"], out["severity"]))
        self.assertNotIn("time", out["message"]["event"])
        self.assertIsNone(self.format("soc.vault-audit", {"kubernetes": entry["kubernetes"], "log": "core: vault is unsealed"}))
        self.assertIsNone(self.format("soc.vault-audit", dict(entry, kubernetes={"namespace_name": "apps"})))

    def test_detection_keeps_only_findings(self):
        finding = {"alertname": "HostSSHBruteForceThenSuccess", "severity": "critical", "node": "node-3",
                   "source_address": "203.0.113.9", "summary": "s"}
        out = self.format("soc.detection", {"kubernetes": {"host": "node-1", "namespace_name": "infra"},
                                            "security_finding": finding})
        self.assertEqual(("node-3", "HostSSHBruteForceThenSuccess", 2), (out["hostname"], out["msgid"], out["severity"]))
        self.assertEqual(finding, out["message"]["event"])
        warning = self.format("soc.detection", {"kubernetes": {"namespace_name": "infra"},
                                                "security_finding": dict(finding, severity="warning", node="")})
        self.assertEqual(("-", 4), (warning["hostname"], warning["severity"]))
        self.assertIsNone(self.format("soc.detection", {"kubernetes": {"namespace_name": "infra"}, "log": "1 finding(s)"}))

    def test_host_journal_and_unknown_sources(self):
        out = self.format("soc.host-journal", {"hostname": "node-4", "syslog_identifier": "sshd session/x",
                                               "message": "Accepted publickey", "pid": "12"})
        self.assertEqual(("node-4", "sshd_session_x"), (out["hostname"], out["msgid"]))
        long = self.format("soc.host-journal", {"hostname": "n", "syslog_identifier": "x" * 40})
        self.assertEqual("x" * 32, long["msgid"])  # RFC 5424 MSGID limit.
        self.assertIsNone(self.format("soc.other", {"message": "m"}))

    def test_numbers(self):
        out = self.format("soc.host-journal", {"hostname": "n", "count": 3, "ratio": 0.25, "big": 2 ** 40,
                                               "nan": float("nan"), "list": [1, "a", True]})
        self.assertEqual({"count": 3, "ratio": 0.25, "big": 2 ** 40, "nan": None, "list": [1, "a", True]},
                         {key: out["message"]["event"][key] for key in ("count", "ratio", "big", "nan", "list")})


class FakeCluster:
    """kubectl stand-in holding the Application and identity ConfigMap."""

    def __init__(self, directory):
        self.directory = directory
        (directory / "application.json").write_text(json.dumps({"spec": {"source": {"helm": {"parameters": [
            {"name": "organizationName", "value": "Example", "forceString": True},
            {"name": "socSyslogPort", "value": "514", "forceString": True}]}}}}))
        (directory / "identity.json").write_text(json.dumps({"data": {"PLATFORM_DOMAIN": "example.com"}}))
        script = directory / "kubectl"
        script.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
args = [a for a in sys.argv[1:] if not a.startswith("--request-timeout")]
store = Path({str(directory)!r}) / ("application.json" if "application.argoproj.io" in args else "identity.json")
if args[0] == "get":
    print(store.read_text())
elif args[0] == "patch":
    def merge(target, patch):
        for key, value in patch.items():
            target[key] = merge(target.get(key, {{}}), value) if isinstance(value, dict) else value
        return target
    store.write_text(json.dumps(merge(json.loads(store.read_text()), json.loads(args[args.index("-p") + 1]))))
""")
        script.chmod(0o755)

    def parameters(self):
        helm = json.loads((self.directory / "application.json").read_text())["spec"]["source"]["helm"]
        return {parameter["name"]: parameter["value"] for parameter in helm["parameters"]}

    def identity(self):
        return json.loads((self.directory / "identity.json").read_text())["data"]


class ConfiguratorTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.cluster = FakeCluster(self.directory)
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.directory}:{path}"
        self.addCleanup(os.environ.__setitem__, "PATH", path)

    def test_records_and_removes_settings(self):
        ca = self.directory / "ca.pem"
        ca.write_text((ROOT / "config/cloudflare-origin-ca.pem").read_text())
        self.assertEqual(0, configure.main(["--host", "siem.example.net", "--sources", "identity,detection,identity",
                                            "--ca-file", str(ca), "--skip-check"]))
        expected = {"socSyslogHost": "siem.example.net", "socSyslogPort": "6514", "socSyslogSources": "identity,detection",
                    "socSyslogCa": base64.b64encode(ca.read_bytes()).decode()}
        parameters = self.cluster.parameters()
        self.assertEqual(expected, {key: parameters[key] for key in expected})
        self.assertEqual("Example", parameters["organizationName"])
        self.assertEqual("siem.example.net", self.cluster.identity()["SOC_SYSLOG_HOST"])
        self.assertEqual("example.com", self.cluster.identity()["PLATFORM_DOMAIN"])
        self.assertEqual(0, configure.main(["--remove"]))
        self.assertEqual({"": 4}, {value: 4 for key, value in self.cluster.parameters().items() if key.startswith("soc")})
        self.assertEqual("", self.cluster.identity()["SOC_SYSLOG_CA"])

    def test_rejects_invalid_settings_without_recording(self):
        for argv in (["--host", "bad host"], ["--host", "siem.example.net", "--port", "0"],
                     ["--host", "siem.example.net", "--sources", "identity,everything"],
                     ["--host", "siem.example.net", "--ca-file", str(ROOT / "README.md")]):
            with self.subTest(argv=argv):
                self.assertEqual(1, configure.main(argv + ["--skip-check"]))
        self.assertNotIn("socSyslogHost", self.cluster.parameters())

    @unittest.skipUnless(shutil.which("openssl"), "openssl is required")
    def test_records_only_after_a_verified_tls_connection(self):
        key, certificate = self.directory / "key.pem", self.directory / "cert.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost", "-keyout", str(key), "-out", str(certificate)],
                       check=True, capture_output=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        listener = socket.create_server(("127.0.0.1", 0))
        port = listener.getsockname()[1]

        def serve():
            while True:
                try:
                    connection, _ = listener.accept()
                except OSError:
                    return
                try:
                    with context.wrap_socket(connection, server_side=True):
                        pass
                except (ssl.SSLError, OSError):
                    pass
        threading.Thread(target=serve, daemon=True).start()
        self.addCleanup(listener.close)
        # The system CAs do not trust the receiver: nothing is recorded.
        self.assertEqual(1, configure.main(["--host", "localhost", "--port", str(port)]))
        self.assertNotIn("socSyslogHost", self.cluster.parameters())
        self.assertEqual(0, configure.main(["--host", "localhost", "--port", str(port), "--ca-file", str(certificate)]))
        self.assertEqual("localhost", self.cluster.parameters()["socSyslogHost"])


@unittest.skipUnless(shutil.which("helm"), "Helm is required")
class ValidationTest(unittest.TestCase):
    BASE = ["helm", "template", "swirl-cloud", str(ROOT / "k8s"), "--namespace", "infra",
            "--set-string", "publicDomain=example.com", "--set-string", "internalDnsZone=internal.example.com",
            "--set-string", "gitopsRepositoryURL=https://github.com/example/swirl-cloud.git",
            "--set-string", "cloudflareAccessTeamName=example",
            "--set-string", "trivy-operator.image.repository=example/trivy-operator",
            "--set-string", "trivy-operator.trivy.image.repository=example/trivy"]

    def test_both_renderers_reject_invalid_settings(self):
        pem = base64.b64encode(b"not a certificate").decode()
        cases = [({"socSyslogHost": "bad_host"}, "SOC_SYSLOG_HOST=bad_host", "host name or IPv4"),
                 ({"socSyslogHost": "siem.example.net", "socSyslogPort": "70000"},
                  "SOC_SYSLOG_HOST=siem.example.net SOC_SYSLOG_PORT=70000", "TCP port"),
                 ({"socSyslogHost": "siem.example.net", "socSyslogSources": "identity,all"},
                  "SOC_SYSLOG_HOST=siem.example.net SOC_SYSLOG_SOURCES=identity,all", "separated by commas"),
                 ({"socSyslogHost": "siem.example.net", "socSyslogCa": pem},
                  f"SOC_SYSLOG_HOST=siem.example.net SOC_SYSLOG_CA={pem}", "PEM certificate")]
        for values, environment, message in cases:
            with self.subTest(values=values):
                options = [item for key, value in values.items()
                           for item in ("--set-string", f"{key}={value}".replace(",", "\\,"))]
                helm = subprocess.run(self.BASE + options, capture_output=True, text=True)
                self.assertNotEqual(0, helm.returncode)
                self.assertIn(message, helm.stderr)
                shell = subprocess.run(["bash", "-c", f"source {ROOT}/scripts/lib/platform-identity.sh; "
                                        "platform_identity_defaults && platform_identity_validate"],
                                       env={"PATH": os.environ["PATH"], "PLATFORM_DOMAIN": "example.com",
                                            **dict(pair.split("=", 1) for pair in environment.split())},
                                       capture_output=True, text=True)
                self.assertNotEqual(0, shell.returncode)
                self.assertIn(message, shell.stderr)

    def test_installer_defaults_follow_the_host(self):
        script = (f"source {ROOT}/scripts/lib/platform-identity.sh; platform_identity_defaults && platform_identity_validate"
                  ' && printf "%s|%s|%s" "$SOC_SYSLOG_PORT" "$SOC_SYSLOG_SOURCES" "${SOC_SYSLOG_CA:0:10}"')
        base = {"PATH": os.environ["PATH"], "PLATFORM_DOMAIN": "example.com"}
        off = subprocess.run(["bash", "-c", script], env=base, capture_output=True, text=True, check=True)
        self.assertEqual("||", off.stdout)
        on = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                            env=dict(base, SOC_SYSLOG_HOST="siem.example.net",
                                     SOC_SYSLOG_CA_FILE=str(ROOT / "config/cloudflare-origin-ca.pem")))
        self.assertEqual("6514|identity,kubernetes-audit,vault-audit,detection|LS0tLS1CRU", on.stdout)


if __name__ == "__main__":
    unittest.main()
