#!/usr/bin/env python3
"""Guards for the host rules of the security detection job, run against fake
Elasticsearch, Keycloak, Prometheus and Alertmanager: an SSH login after five
failures from the same address alerts and four do not, sudo by a user outside the
learned history alerts while the first run only learns, and a ClamAV detection
names the pod that sent the file. Only findings first stored in the hour are
printed for SOC forwarding."""
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
    const keycloakCalls = [];
    const updates = [];
    const reply = (status, body) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status });
    Date.now = () => Date.parse(fixture.now);
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || "GET";
      if (url.includes("/security-detection-state/_doc/")) {
        const id = url.split("/").pop();
        if (method === "PUT") { state[id] = JSON.parse(options.body); return reply(200, {}); }
        return id in state ? reply(200, { _source: state[id] }) : reply(404, {});
      }
      if (url.includes("/access-grants/_update/")) {
        updates.push({ id: decodeURIComponent(url.split("/").pop()), doc: JSON.parse(options.body).doc });
        return reply(200, {});
      }
      if (url.endsWith("/_search?ignore_unavailable=true&allow_no_indices=true")) {
        const index = url.split("/").slice(-2)[0];
        const body = JSON.parse(options.body);
        if (index === "access-grants") {
          const until = Date.parse(body.query.bool.filter[1].range.expires_at.lte);
          const hits = (fixture.grants || []).filter((grant) => grant.state === "active" && Date.parse(grant.expires_at) <= until);
          return reply(200, { hits: { hits: hits.map((grant) => ({ _id: grant.id, _source: grant })) } });
        }
        if (index === "kubernetes-logs-host-*") {
          const identifiers = body.query.bool.filter[0].terms.syslog_identifier;
          const since = body.query.bool.filter[1].range["@timestamp"].gte;
          const floor = since.startsWith("now-") ? 0 : Date.parse(since);
          const hits = fixture.journal.filter((line) => identifiers.includes(line.syslog_identifier)
            && Date.parse(line["@timestamp"]) >= floor);
          return reply(200, { hits: { hits: hits.map((line) => ({ _source: line })) } });
        }
        if (index === "malware-scans-*") {
          // Infected results since the window's start, grouped by signature and sender.
          const wanted = body.query.bool.filter[0].term["clamav.result"];
          const since = Date.parse(body.query.bool.filter[1].range["@timestamp"].gte);
          const groups = new Map();
          for (const scan of fixture.scans || []) {
            if (scan.clamav.result !== wanted || Date.parse(scan["@timestamp"]) < since) continue;
            const key = [scan.clamav.signature, scan.clamav.client_address];
            const group = groups.get(key.join("|")) || { key, doc_count: 0, category: { buckets: [{ key: scan.clamav.category }] } };
            group.doc_count += 1;
            groups.set(key.join("|"), group);
          }
          return reply(200, { hits: { hits: [] }, aggregations: { found: { buckets: [...groups.values()] } } });
        }
        if (index === "identity-audit-*" && body.size) {
          // Term filters and must_not terms on the fixture's identity events.
          const value = (event, path) => path.split(".").reduce((node, key) => node?.[key], event);
          const terms = (clauses) => (clauses || []).filter((clause) => clause.term).map((clause) => Object.entries(clause.term)[0]);
          const hits = (fixture.identity || []).filter((event) =>
            terms(body.query.bool.filter).every(([path, wanted]) => value(event, path) === wanted)
            && !terms(body.query.bool.must_not).some(([path, wanted]) => value(event, path) === wanted));
          return reply(200, { hits: { hits: hits.map((event) => ({ _source: event })), total: { value: hits.length } } });
        }
        return reply(200, { hits: { hits: [], total: { value: 0 } }, aggregations: {} });
      }
      if (url.includes("/protocol/openid-connect/token")) return reply(200, { access_token: "token" });
      if (url.includes("/admin/realms/")) {
        const path = url.split("/admin/realms/")[1];
        if (fixture.grants) {
          keycloakCalls.push(`${method} ${decodeURIComponent(path)}${options.body ? " " + options.body : ""}`);
          const user = path.match(/users\\?username=([^&]+)/);
          if (user) return reply(200, [{ id: "u-" + user[1], username: user[1] }]);
          const group = path.match(/groups\\?search=([^&]+)/);
          if (group) return reply(200, [{ id: "g-" + decodeURIComponent(group[1]), name: decodeURIComponent(group[1]) }]);
          if (method === "GET" && /users\\/u-[^/]+$/.test(path)) return reply(200, { id: path.split("/").pop(), email: "x@example.com", enabled: true });
          if (method !== "GET") return new Response(null, { status: 204 });
        }
        return reply(200, []);
      }
      if (url.startsWith("http://prom/api/v1/query?")) {
        const address = new URL(url).searchParams.get("query").match(/pod_ip="([^"]+)"/)[1];
        const pod = (fixture.pods || {})[address];
        return reply(200, { status: "success", data: { result: pod ? [{ metric: pod }] : [] } });
      }
      if (url.endsWith("/api/v2/alerts")) { posted.push(...JSON.parse(options.body)); return reply(200, {}); }
      if (url.endsWith("/_bulk")) {
        // A finding already stored this hour conflicts, as Elasticsearch's "create" does.
        const actions = options.body.trim().split("\\n").filter((_, line) => line % 2 === 0).map(JSON.parse);
        return reply(200, { items: actions.map(({ create }) => ({ create: { status: (fixture.stored || []).some(
          (prefix) => create._id.startsWith(prefix)) ? 409 : 201 } })) });
      }
      return reply(404, `unexpected ${method} ${url}`);
    };
    process.on("exit", () => writeFileSync(process.env.RESULT, JSON.stringify({ posted, state, keycloakCalls, updates })));
    """)


def ssh(minute, message, host="node-1"):
    return {"@timestamp": f"2026-10-27T11:{minute:02d}:00.000Z", "hostname": host, "syslog_identifier": "sshd-session",
            "message": message}


def sudo(timestamp, user, host="node-1"):
    return {"@timestamp": timestamp, "hostname": host, "syslog_identifier": "sudo",
            "message": f"    {user} : TTY=pts/0 ; PWD=/home/{user} ; USER=root ; COMMAND=/usr/bin/true"}


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class SecurityDetectionTest(unittest.TestCase):
    def run_detection(self, journal, state=None, stored=(), identity=(), grants=None, scans=(), pods=None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        documents = yaml.safe_load_all((ROOT / "k8s/platform/security-detection.yaml").read_text())
        script = next(d for d in documents if d and d["kind"] == "ConfigMap")["data"]["detect.mjs"]
        (directory / "detect.mjs").write_text(script)
        (directory / "mock.mjs").write_text(MOCK)
        fixture = {"now": NOW, "journal": journal, "state": state or {}, "stored": list(stored), "identity": list(identity),
                   "scans": list(scans), "pods": pods or {}}
        if grants is not None:
            fixture["grants"] = grants
        (directory / "fixture.json").write_text(json.dumps(fixture))
        env = {"PATH": "/usr/bin:/bin", "FIXTURE": str(directory / "fixture.json"), "RESULT": str(directory / "result.json"),
               "ELASTICSEARCH_URL": "http://es", "ALERTMANAGER_URL": "http://am", "PROMETHEUS_URL": "http://prom", "KEYCLOAK_URL": "http://kc",
               "KEYCLOAK_REALM": "example", "RUNBOOK_BASE_URL": "https://runbooks", "ELASTICSEARCH_USERNAME": "u",
               "ELASTICSEARCH_PASSWORD": "p", "KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "x",
               "KC_BOOTSTRAP_ADMIN_USERNAME": "admin", "KC_BOOTSTRAP_ADMIN_PASSWORD": "x"}
        result = subprocess.run([shutil.which("node"), "--import", str(directory / "mock.mjs"), str(directory / "detect.mjs")],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.findings = [json.loads(line)["security_finding"] for line in result.stdout.splitlines() if line.startswith("{")]
        output = json.loads((directory / "result.json").read_text())
        self.output = output
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
        self.assertEqual([("HostSSHBruteForceThenSuccess", "203.0.113.9", "node-1")],
                         [(f["alertname"], f["source_address"], f["node"]) for f in self.findings])
        # A rerun in the same hour alerts again but prints nothing new for the SOC.
        alerts, _ = self.run_detection(journal, stored=["HostSSHBruteForceThenSuccess|"])
        self.assertIn(("HostSSHBruteForceThenSuccess", "203.0.113.9"), alerts)
        self.assertEqual([], self.findings)

    def test_sudo_by_a_user_outside_the_learned_history(self):
        history = [sudo("2026-10-22T09:00:00.000Z", "zaid"), sudo("2026-10-27T11:55:00.000Z", "zaid")]
        alerts, state = self.run_detection(history)
        self.assertFalse([alert for alert in alerts if alert[0] == "HostSudoByNewUser"])  # The first run only learns.
        self.assertEqual(["zaid"], state["sudo-users"]["users"])
        alerts, state = self.run_detection(history + [sudo("2026-10-27T11:58:00.000Z", "mallory")], state)
        self.assertEqual({("HostSudoByNewUser", "mallory")}, {alert for alert in alerts if alert[0] == "HostSudoByNewUser"})
        self.assertIn("mallory", state["sudo-users"]["pending"])

    def test_emergency_account_used_by_a_person(self):
        def login(client, user="admin", realm="master"):
            return {"@timestamp": "2026-10-27T11:55:00.000Z",
                    "keycloak": {"type": "LOGIN", "realmName": realm, "username": user, "clientId": client, "ipAddress": "192.0.2.7"}}
        # Automation through admin-cli, and other people, raise nothing.
        quiet = [login("admin-cli"), login("security-admin-console", user="zaid"), login("account-console", realm="example")]
        alerts, _ = self.run_detection([], identity=quiet)
        self.assertFalse([alert for alert in alerts if alert[0] == "EmergencyAccountUsed"])
        alerts, _ = self.run_detection([], identity=quiet + [login("security-admin-console")])
        self.assertEqual({("EmergencyAccountUsed", "admin")}, {alert for alert in alerts if alert[0] == "EmergencyAccountUsed"})

    def test_malware_found_names_the_sending_pod(self):
        def scan(minute, result, signature=None, address="10.42.0.31", category="malware"):
            fields = {"result": result, "client_address": address}
            if signature:
                fields.update(signature=signature, category=category)
            return {"@timestamp": f"2026-10-27T11:{minute:02d}:00.000Z", "clamav": fields}
        scans = [scan(50, "clean"), scan(52, "infected", "Win.Trojan.Agent-1"), scan(53, "infected", "Win.Trojan.Agent-1"),
                 scan(54, "infected", "Heuristics.OLE2.ContainsMacros", "10.42.0.77", "heuristic"),
                 scan(30, "infected", "Win.Trojan.Older-1")]  # before the 15-minute window
        self.run_detection([], scans=scans, pods={"10.42.0.31": {"namespace": "apps", "pod": "indezy-server-1"}})
        found = {a["labels"]["signature"]: a for a in self.output["posted"] if a["labels"]["alertname"] == "MalwareDetected"}
        self.assertEqual({"Win.Trojan.Agent-1", "Heuristics.OLE2.ContainsMacros"}, set(found))
        trojan = found["Win.Trojan.Agent-1"]
        self.assertEqual(("critical", "apps/indezy-server-1", "security"),
                         (trojan["labels"]["severity"], trojan["labels"]["sender"], trojan["labels"]["owner"]))
        self.assertIn("2 file(s) sent by apps/indezy-server-1 (10.42.0.31)", trojan["annotations"]["description"])
        self.assertTrue(trojan["annotations"]["runbook_url"].endswith("#malwaredetected"))
        # A policy refusal is a warning, and an address no pod held is reported as it is.
        macros = found["Heuristics.OLE2.ContainsMacros"]["labels"]
        self.assertEqual(("warning", "10.42.0.77"), (macros["severity"], macros["sender"]))
        self.assertEqual({"MalwareDetected"}, {f["alertname"] for f in self.findings})

    def test_timed_access_ends_and_warns_a_week_before(self):
        def grant(id, user, expires, granted="2026-09-01T00:00:00+00:00", **fields):
            return {"id": id, "user": user, "state": "active", "granted_at": granted, "expires_at": expires,
                    "approved_by": "bob", **fields}
        grants = [
            grant("a1", "alice", "2026-10-27T11:00:00+00:00", granted="2026-10-27T07:00:00+00:00"),  # administration, older record
            grant("r1", "carol", "2026-10-27T00:00:00+00:00", kind="role", group="access-grafana"),
            grant("g1", "dave", "2026-10-26T00:00:00+00:00", kind="guest", sponsor="erin"),
            grant("w1", "frank", "2026-11-01T00:00:00+00:00", kind="guest", sponsor="erin"),  # ends in five days
            grant("w2", "gina", "2026-11-01T00:00:00+00:00", kind="role", group="access-gitlab", warned_at="2026-10-26T00:00:00Z"),
            grant("s1", "hal", "2026-10-28T00:00:00+00:00", granted="2026-10-27T00:00:00+00:00", kind="role", group="access-kibana"),
        ]
        alerts, _ = self.run_detection([], grants=grants)
        calls = self.output["keycloakCalls"]
        self.assertIn("DELETE example/users/u-alice/groups/g-platform-admins", calls)
        self.assertIn("DELETE example/users/u-carol/groups/g-access-grafana", calls)
        self.assertTrue(any(call.startswith("PUT example/users/u-dave ") and '"enabled":false' in call and '"email":"x@example.com"' in call
                            for call in calls))
        self.assertIn("POST example/users/u-dave/logout", calls)
        self.assertFalse([call for call in calls if "frank" in call or "gina" in call or "hal" in call])
        ended = {update["id"] for update in self.output["updates"] if update["doc"].get("state") == "expired"}
        self.assertEqual({"a1", "r1", "g1"}, ended)
        # Warned once, only for a grant longer than a week that ends within one.
        self.assertEqual({("TimedAccessEndingSoon", "frank")}, {alert for alert in alerts if alert[0] == "TimedAccessEndingSoon"})
        warning = next(a for a in self.output["posted"] if a["labels"]["alertname"] == "TimedAccessEndingSoon")
        self.assertEqual(("account", "erin", "2026-11-01"), (warning["labels"]["access"], warning["labels"]["sponsor"], warning["labels"]["ends"]))
        self.assertEqual([{"id": "w1", "doc": {"warned_at": NOW}}], [u for u in self.output["updates"] if "warned_at" in u["doc"]])


if __name__ == "__main__":
    unittest.main()
