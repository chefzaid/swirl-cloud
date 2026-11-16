#!/usr/bin/env python3
"""Guards for the database audit trail: the logging agent's Lua filter marks
PostgreSQL sign-ins, sign-outs, refused sign-ins and schema and role changes
from the platform database (single or CloudNativePG) as database-audit records
and leaves other lines and other containers alone; the settings Job turns the
logging on with the prefix the filter reads; and every script that sets a role
password turns statement logging off for its session first."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "2026-11-15 09:12:01.123 UTC [4242] "
SESSION = "[user=admin db=sonarqube client=10.42.0.17 app=DbGate] "
CASES = [
    ("statement", {"log": PREFIX + SESSION + "LOG:  statement: ALTER TABLE projects ADD COLUMN owner text"},
     {"event": "statement", "user": "admin", "database": "sonarqube", "client": "10.42.0.17", "application": "DbGate"}),
    ("connection", {"log": PREFIX + SESSION + "LOG:  connection authorized: user=admin database=sonarqube application_name=DbGate"},
     {"event": "connection", "user": "admin"}),
    ("disconnection", {"log": PREFIX + "[user=sonar db=sonarqube client=10.42.0.9 app=] LOG:  disconnection: session time: 0:00:01.002"},
     {"event": "disconnection", "user": "sonar", "application": ""}),
    ("refused", {"log": PREFIX + "[user=mallory db=postgres client=10.42.0.66 app=[unknown]] FATAL:  password authentication failed for user \"mallory\""},
     {"event": "authentication_failure", "user": "mallory", "severity": "FATAL"}),
    ("application name with spaces", {"log": PREFIX + "[user=admin db=postgres client=[local] app=psql tool] LOG:  statement: DROP ROLE old"},
     {"event": "statement", "application": "psql tool", "client": "[local]"}),
    ("cloudnativepg", {"logger": "postgres", "log": "{}", "record": {
        "user_name": "admin", "database_name": "keycloak", "remote_host": "10.42.1.4",
        "application_name": "psql", "error_severity": "LOG", "message": "statement: GRANT SELECT ON users TO auditor"}},
     {"event": "statement", "user": "admin", "database": "keycloak", "client": "10.42.1.4"}),
]
IGNORED = [
    ("checkpoint", {"log": PREFIX + "LOG:  checkpoint starting: time"}),
    ("query error", {"log": PREFIX + SESSION + "ERROR:  relation \"x\" does not exist"}),
    ("statement after an error", {"log": PREFIX + SESSION + "STATEMENT:  SELECT * FROM x"}),
    ("continuation line", {"log": "  ADD COLUMN owner text;"}),
]


def lua_script():
    for document in yaml.safe_load_all((ROOT / "k8s/platform/logging-agent.yaml").read_text().replace("__INTERNAL_DNS_ZONE__", "zone")):
        if document and document.get("kind") == "ConfigMap" and "enrich-kubernetes.lua" in document.get("data", {}):
            return document["data"]["enrich-kubernetes.lua"]
    raise AssertionError("enrich-kubernetes.lua not found")


def lua_table(value):
    if isinstance(value, dict):
        return "{" + ", ".join(f"[{json.dumps(key)}] = {lua_table(item)}" for key, item in value.items()) + "}"
    return json.dumps(value)


class DatabaseAudit(unittest.TestCase):
    @unittest.skipUnless(shutil.which("luajit") or shutil.which("lua"), "no Lua interpreter")
    def test_filter_marks_only_database_audit_lines(self):
        records = [(name, record, "infra", "postgres") for name, record, _ in CASES]
        records += [(name, record, "infra", "postgres") for name, record in IGNORED]
        records += [("other container", CASES[0][1], "infra", "postgres-exporter"),
                    ("application database", CASES[0][1], "apps", "postgres")]
        calls = []
        for name, record, namespace, container in records:
            record = dict(record, kubernetes={"namespace_name": namespace, "container_name": container})
            calls.append(textwrap.dedent(f"""\
                do
                  local _, _, out = enrich_kubernetes("kube.x", 0, {lua_table(record)})
                  local audit = out["database_audit"] or {{}}
                  local fields = {{}}
                  for _, key in ipairs({{"event", "user", "database", "client", "application", "severity"}}) do
                    fields[#fields + 1] = key .. "=" .. tostring(audit[key])
                  end
                  print({json.dumps(name)} .. "|" .. tostring(out["log_source"]) .. "|" .. table.concat(fields, ";"))
                end"""))
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "run.lua"
            script.write_text(lua_script() + "\n" + "\n".join(calls) + "\n")
            output = subprocess.run([shutil.which("luajit") or shutil.which("lua"), str(script)],
                                    check=True, capture_output=True, text=True).stdout
        results = {}
        for line in output.splitlines():
            name, source, fields = line.split("|", 2)
            results[name] = (source, dict(item.split("=", 1) for item in fields.split(";")))
        for name, _, expected in CASES:
            source, fields = results[name]
            self.assertEqual(source, "database-audit", name)
            for key, value in expected.items():
                self.assertEqual(fields[key], value, (name, key))
        for name in [name for name, _ in IGNORED] + ["other container", "application database"]:
            self.assertEqual(results[name][0], "nil", name)

    def test_settings_job_matches_the_filter(self):
        text = (ROOT / "k8s/datastores/postgres.yaml").read_text()
        for setting in ("log_connections = 'authorization'", "log_disconnections = on", "log_statement = 'ddl'",
                        "log_line_prefix = '%m [%p] %q[user=%u db=%d client=%h app=%a] '", "pg_reload_conf()"):
            self.assertIn(setting, text)
        self.assertIn("database-audit", (ROOT / "k8s/platform/elk.yaml").read_text())
        ha = (ROOT / "k8s/templates/postgres-ha.yaml").read_text()
        self.assertIn("log_statement: ddl", ha)

    def test_password_writers_turn_statement_logging_off(self):
        writers = ["k8s/platform/sonarqube.yaml", "k8s/corp/odoo.yaml", "k8s/files/zammad/database.sh",
                   "scripts/configure-application-data.py", "scripts/rotate-local-admin-passwords.sh",
                   "scripts/configure-postgres-ha.py", "k8s/platform/vaultwarden.yaml"]
        found = set()
        for path in list((ROOT / "k8s").rglob("*.yaml")) + list((ROOT / "k8s/files").rglob("*.sh")) + list((ROOT / "scripts").glob("*.*")):
            text = path.read_text(errors="ignore")
            if re.search(r"(?i)(alter|create) role[^\n]*password", text) or "pg_dumpall\", \"--globals-only" in text:
                found.add(str(path.relative_to(ROOT)))
        self.assertEqual(found, set(writers), "a new script sets role passwords; turn statement logging off in it")
        for writer in writers:
            text = (ROOT / writer).read_text()
            self.assertRegex(text, r"SET log_statement = (\\'|'\\''|')none", writer)


if __name__ == "__main__":
    unittest.main()
