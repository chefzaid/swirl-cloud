#!/usr/bin/env python3
"""Guards for the host rules of the security detection job, run against fake
Elasticsearch, Keycloak and Alertmanager: an SSH login after five failures from
the same address alerts and four do not, and sudo by a user outside the learned
history alerts while the first run only learns."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-27T12:00:00.000Z"

MOCK = textwrap.dedent("""\
    import { readFileSync, writeFileSync } from "node:fs";
    const fixture = JSON.parse(readFileSync(process.env.FIXTURE, "utf8"));
    const state = fixture.state;
    const posted = [];
    const reply = (status, body) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
    Date.now = () => Date.parse(fixture.now);
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || "GET";
      if (url.includes("/security-detection-state/_doc/")) {
        const id = url.split("/").pop();
        if (method === "PUT") { state[id] = JSON.parse(options.body); return reply(200, {}); }
        return id in state ? reply(200, { _source: state[id] }) : reply(404, {});
      }
      if (url.endsWith("/_search?ignore_unavailable=true&allow_no_indices=true")) {
        const index = url.split("/").slice(-2)[0];
        const body = JSON.parse(options.body);
        if (index === "kubernetes-logs-host-*") {
          const identifiers = body.query.bool.filter[0].terms.syslog_identifier;
          const since = body.query.bool.filter[1].range["@timestamp"].gte;
          const floor = since.startsWith("now-") ? 0 : Date.parse(since);
          const hits = fixture.journal.filter((line) => identifiers.includes(line.syslog_identifier)
            && Date.parse(line["@timestamp"]) >= floor);
          return reply(200, { hits: { hits: hits.map((line) => ({ _source: line })) } });
        }
        return reply(200, { hits: { hits: [], total: { value: 0 } }, aggregations: {} });
      }
      if (url.includes("/protocol/openid-connect/token")) return reply(200, { access_token: "token" });
      if (url.includes("/admin/realms/")) return reply(200, []);
      if (url.endsWith("/api/v2/alerts")) { posted.push(...JSON.parse(options.body)); return reply(200, {}); }
      if (url.endsWith("/_bulk")) return reply(200, {});
      return reply(404, `unexpected ${method} ${url}`);
    };
    process.on("exit", () => writeFileSync(process.env.RESULT, JSON.stringify({ posted, state })));
    """)


def ssh(minute, message, host="node-1"):
    return {"@timestamp": f"2026-10-27T11:{minute:02d}:00.000Z", "hostname": host, "syslog_identifier": "sshd-session",
            "message": message}


def sudo(timestamp, user, host="node-1"):
    return {"@timestamp": timestamp, "hostname": host, "syslog_identifier": "sudo",
            "message": f"    {user} : TTY=pts/0 ; PWD=/home/{user} ; USER=root ; COMMAND=/usr/bin/true"}


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class SecurityDetectionTest(unittest.TestCase):
    def run_detection(self, journal, state=None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        documents = yaml.safe_load_all((ROOT / "k8s/platform/security-detection.yaml").read_text())
        script = next(d for d in documents if d and d["kind"] == "ConfigMap")["data"]["detect.mjs"]
        (directory / "detect.mjs").write_text(script)
        (directory / "mock.mjs").write_text(MOCK)
        (directory / "fixture.json").write_text(json.dumps({"now": NOW, "journal": journal, "state": state or {}}))
        env = {"PATH": "/usr/bin:/bin", "FIXTURE": str(directory / "fixture.json"), "RESULT": str(directory / "result.json"),
               "ELASTICSEARCH_URL": "http://es", "ALERTMANAGER_URL": "http://am", "KEYCLOAK_URL": "http://kc",
               "KEYCLOAK_REALM": "example", "RUNBOOK_BASE_URL": "https://runbooks", "ELASTICSEARCH_USERNAME": "u",
               "ELASTICSEARCH_PASSWORD": "p", "KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "x"}
        result = subprocess.run([shutil.which("node"), "--import", str(directory / "mock.mjs"), str(directory / "detect.mjs")],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        output = json.loads((directory / "result.json").read_text())
        return {(alert["labels"]["alertname"], alert["labels"].get("source_address") or alert["labels"].get("user"))
                for alert in output["posted"]}, output["state"]

    def test_ssh_success_after_repeated_failures(self):
        journal = [ssh(50 + i, f"Failed password for invalid user admin from 203.0.113.9 port {40000 + i} ssh2") for i in range(5)]
        journal += [ssh(51 + i, f"Invalid user test from 198.51.100.4 port {50000 + i}") for i in range(4)]
        journal += [ssh(57, "Accepted publickey for zaid from 203.0.113.9 port 40100 ssh2: ED25519 SHA256:abc"),
                    ssh(58, "Accepted publickey for zaid from 198.51.100.4 port 50100 ssh2: ED25519 SHA256:abc"),
                    ssh(59, "Accepted publickey for zaid from 192.0.2.1 port 1 ssh2: ED25519 SHA256:abc")]
        alerts, _ = self.run_detection(journal)
        self.assertEqual({("HostSSHBruteForceThenSuccess", "203.0.113.9")},
                         {alert for alert in alerts if alert[0].startswith("HostSSH")})

    def test_sudo_by_a_user_outside_the_learned_history(self):
        history = [sudo("2026-10-22T09:00:00.000Z", "zaid"), sudo("2026-10-27T11:55:00.000Z", "zaid")]
        alerts, state = self.run_detection(history)
        self.assertFalse([alert for alert in alerts if alert[0] == "HostSudoByNewUser"])  # The first run only learns.
        self.assertEqual(["zaid"], state["sudo-users"]["users"])
        alerts, state = self.run_detection(history + [sudo("2026-10-27T11:58:00.000Z", "mallory")], state)
        self.assertEqual({("HostSudoByNewUser", "mallory")}, {alert for alert in alerts if alert[0] == "HostSudoByNewUser"})
        self.assertIn("mallory", state["sudo-users"]["pending"])


if __name__ == "__main__":
    unittest.main()
