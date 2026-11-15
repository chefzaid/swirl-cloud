#!/usr/bin/env python3
"""Guards for the platform token expiry check, run against a fake GitLab and
Alertmanager: a token 20 days from expiry warns, one 5 days away or rejected is
critical, a distant one raises nothing, a missing token is skipped, and token
values never reach the output or the alert."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
MOCK = textwrap.dedent("""\
    import { readFileSync, writeFileSync } from "node:fs";
    const fixture = JSON.parse(readFileSync(process.env.FIXTURE, "utf8"));
    const posted = [];
    Date.now = () => Date.parse("2026-11-15T06:17:00Z");
    globalThis.fetch = async (url, options = {}) => {
      if (url.endsWith("/personal_access_tokens/self")) {
        const answer = fixture.tokens[options.headers["PRIVATE-TOKEN"]];
        return new Response(JSON.stringify(answer || {}), { status: answer ? 200 : 401 });
      }
      if (url.endsWith("/api/v2/alerts")) { posted.push(...JSON.parse(options.body)); return new Response("{}"); }
      return new Response("", { status: 404 });
    };
    process.on("exit", () => writeFileSync(process.env.RESULT, JSON.stringify(posted)));
    """)


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class TokenExpiryTest(unittest.TestCase):
    def test_alerts_by_days_left(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "mock.mjs").write_text(MOCK)
        (directory / "fixture.json").write_text(json.dumps({"tokens": {
            "secret-soon": {"active": True, "revoked": False, "expires_at": "2026-12-05"},
            "secret-urgent": {"active": True, "revoked": False, "expires_at": "2026-11-20"},
            "secret-later": {"active": True, "revoked": False, "expires_at": "2027-10-01"}}}))
        env = {"PATH": "/usr/bin:/bin", "FIXTURE": str(directory / "fixture.json"), "RESULT": str(directory / "result.json"),
               "GITLAB_API_URL": "http://gitlab/api/v4", "ALERTMANAGER_URL": "http://am", "RUNBOOK_BASE_URL": "https://runbooks",
               "TOKEN_REGISTRY_RETENTION": "secret-soon", "TOKEN_SONAR_DISCOVERY": "secret-urgent",
               "TOKEN_LATER": "secret-later", "TOKEN_REVOKED": "secret-gone", "TOKEN_MISSING": ""}
        result = subprocess.run([shutil.which("node"), "--import", str(directory / "mock.mjs"), str(ROOT / "k8s/files/token-expiry.mjs")],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        posted = json.loads((directory / "result.json").read_text())
        self.assertEqual({("registry-retention", "warning"), ("sonar-discovery", "critical"), ("revoked", "critical")},
                         {(alert["labels"]["token"], alert["labels"]["severity"]) for alert in posted})
        self.assertIn("later: expires 2027-10-01", result.stdout)
        self.assertNotIn("missing", result.stdout)
        self.assertNotIn("secret-", result.stdout + json.dumps(posted))


if __name__ == "__main__":
    unittest.main()
