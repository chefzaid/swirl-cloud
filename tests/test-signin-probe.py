#!/usr/bin/env python3
"""Guards for the sign-in probe, run against a fake Keycloak, OAuth2 Proxy and
Vault: a working sign-in passes on both targets; a wrong client secret, a refused
password and a pending Keycloak action fail with the step that broke; a Vault
token the account should not get is revoked and reported; and the password never
reaches the output."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Probe-Secret-0123456789"
MOCK = textwrap.dedent("""\
    import { readFileSync, appendFileSync } from "node:fs";
    const scenario = process.env.SCENARIO;
    const log = (line) => appendFileSync(process.env.CALLS, line + "\\n");
    const kc = "https://keycloak.example.test/auth/realms/staff";
    const redirect = (to, cookies = []) => {
      const headers = new Headers({ location: to });
      for (const cookie of cookies) headers.append("set-cookie", cookie);
      return new Response(null, { status: 302, headers });
    };
    const loginPage = (redirectUri, state) => new Response(
      `<html><form id="kc-form-login" action="${kc}/login-actions/authenticate?session_code=a&amp;redirect_uri=${encodeURIComponent(redirectUri)}&amp;state=${state}" method="post"></form></html>`,
      { status: 200, headers: { "set-cookie": "AUTH_SESSION_ID=s1; Path=/auth" } });
    globalThis.fetch = async (url, options = {}) => {
      const target = new URL(url);
      const cookie = options.headers?.Cookie || "";
      log(`${options.method || "GET"} ${target.origin}${target.pathname} cookie=${cookie.replace(/=[^;]*/g, "")}`);
      if (target.pathname === "/oauth2/start") {
        return redirect(`${kc}/protocol/openid-connect/auth?client_id=oauth2-proxy&redirect_uri=${encodeURIComponent("https://keycloak.example.test/oauth2/callback")}&state=p1`,
          ["_oauth2_proxy_csrf=c1; Path=/; Secure"]);
      }
      if (target.pathname.endsWith("/protocol/openid-connect/auth")) {
        return loginPage(target.searchParams.get("redirect_uri"), target.searchParams.get("state"));
      }
      if (target.pathname.endsWith("/login-actions/authenticate")) {
        const form = new URLSearchParams(options.body);
        if (!cookie.includes("AUTH_SESSION_ID")) return new Response("no session", { status: 400 });
        if (scenario === "wrong-password" || form.get("password") !== process.env.PROBE_PASSWORD) {
          return new Response('<form id="kc-form-login" action="x"></form>', { status: 200 });
        }
        if (scenario === "terms") return new Response("<form id=\\"kc-terms-form\\"></form>", { status: 200 });
        return redirect(`${target.searchParams.get("redirect_uri")}?state=${target.searchParams.get("state")}&code=k1`);
      }
      if (target.pathname === "/oauth2/callback") {
        if (!cookie.includes("_oauth2_proxy_csrf")) return new Response("csrf", { status: 403 });
        if (scenario === "bad-secret") return new Response("Internal Error", { status: 500 });
        return redirect("https://keycloak.example.test/", ["_oauth2_proxy=t1; Path=/; Secure; HttpOnly"]);
      }
      if (target.pathname === "/v1/auth/oidc/oidc/auth_url") {
        const body = JSON.parse(options.body);
        return Response.json({ data: { auth_url: `${kc}/protocol/openid-connect/auth?client_id=vault&redirect_uri=${encodeURIComponent(body.redirect_uri)}&state=v1` } });
      }
      if (target.pathname === "/v1/auth/oidc/oidc/callback") {
        if (target.searchParams.get("code") !== "k1") return Response.json({ errors: ["no code"] }, { status: 400 });
        if (scenario === "bad-secret") return Response.json({ errors: ["Error exchanging oidc code: oauth2: \\"unauthorized_client\\" \\"Invalid client or Invalid client credentials\\""] }, { status: 400 });
        if (scenario === "vault-token") return Response.json({ auth: { client_token: "hvs.leaked" } });
        return Response.json({ errors: ["error validating claims: claim \\"groups\\" does not match any associated bound claim values"] }, { status: 400 });
      }
      if (target.pathname === "/v1/auth/token/revoke-self") { log("revoked " + options.headers["X-Vault-Token"]); return new Response(null, { status: 204 }); }
      return new Response("", { status: 404 });
    };
    """)


def probe_script():
    return (ROOT / "k8s/files/signin-probe.mjs").read_text()


@unittest.skipUnless(shutil.which("node"), "node is required")
class SignInProbe(unittest.TestCase):
    def run_probe(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "mock.mjs").write_text(MOCK)
            (directory / "probe.mjs").write_text(probe_script())
            calls = directory / "calls.log"
            calls.touch()
            env = dict(os.environ, SCENARIO=scenario, CALLS=str(calls), PUBLIC_DOMAIN="example.test", KEYCLOAK_REALM="staff",
                       PROBE_USERNAME="signin-probe", PROBE_PASSWORD=PASSWORD, PROBE_ONCE="true", METRICS_PORT="0",
                       VAULT_ADDR="http://vault.cluster.test:8200")
            result = subprocess.run(["node", "--import", str(directory / "mock.mjs"), str(directory / "probe.mjs")],
                                    env=env, capture_output=True, text=True, timeout=60, check=True)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
            metrics = {line.split(" ")[0]: float(line.split(" ")[1]) for line in result.stdout.splitlines() if line.startswith("swirl_")}
            logs = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            return metrics, {entry["target"]: entry for entry in logs}, calls.read_text()

    def test_working_sign_in_passes_on_both_targets(self):
        metrics, logs, calls = self.run_probe("ok")
        self.assertEqual(metrics['swirl_signin_probe_success{target="oauth2-proxy"}'], 1)
        self.assertEqual(metrics['swirl_signin_probe_success{target="vault"}'], 1)
        self.assertEqual(metrics['swirl_signin_probe_unexpected_access{target="vault"}'], 0)
        self.assertIn("POST https://keycloak.example.test/auth/realms/staff/login-actions/authenticate cookie=AUTH_SESSION_ID", calls)
        self.assertTrue(logs["vault"]["success"])
        # Vault's public host is behind Cloudflare Access: its API goes through the cluster address.
        self.assertIn("POST http://vault.cluster.test:8200/v1/auth/oidc/oidc/auth_url", calls)
        self.assertNotIn("https://vault.example.test/v1/", calls)

    def test_wrong_client_secret_fails_at_the_callback(self):
        metrics, logs, _ = self.run_probe("bad-secret")
        self.assertEqual(metrics['swirl_signin_probe_success{target="oauth2-proxy"}'], 0)
        self.assertEqual(metrics['swirl_signin_probe_success{target="vault"}'], 0)
        self.assertEqual(logs["oauth2-proxy"]["step"], "callback")
        self.assertIn("Invalid client", logs["vault"]["reason"])

    def test_refused_password_and_pending_action_fail_at_sign_in(self):
        _, logs, _ = self.run_probe("wrong-password")
        self.assertEqual(logs["vault"]["step"], "sign-in")
        self.assertIn("password was refused", logs["vault"]["reason"])
        _, logs, _ = self.run_probe("terms")
        self.assertIn("asked for an action", logs["oauth2-proxy"]["reason"])

    def test_vault_token_is_revoked_and_reported(self):
        metrics, _, calls = self.run_probe("vault-token")
        self.assertEqual(metrics['swirl_signin_probe_unexpected_access{target="vault"}'], 1)
        self.assertIn("revoked hvs.leaked", calls)


ACCOUNT_MOCK = textwrap.dedent("""\
    import { appendFileSync } from "node:fs";
    const log = (line) => appendFileSync(process.env.CALLS, line + "\\n");
    let created = process.env.EXISTING === "true";
    globalThis.fetch = async (url, options = {}) => {
      const path = new URL(url).pathname.replace("/auth/admin/realms/staff", "");
      const method = options.method || "GET";
      log(`${method} ${path} ${method === "GET" ? "" : options.body || ""}`);
      if (path.endsWith("/protocol/openid-connect/token")) return Response.json({ access_token: "admin-token" });
      if (method === "GET" && path === "/users") return Response.json(created ? [{ id: "u1", username: "signin-probe" }] : []);
      if (method === "POST" && path === "/users") { created = true; return new Response(null, { status: 201 }); }
      if (method === "GET" && path === "/users/u1/groups") return Response.json([{ id: "g1", name: "platform-admins" }]);
      if (method === "GET" && path === "/users/u1/role-mappings") return Response.json({
        realmMappings: [{ id: "r0", name: "default-roles-staff" }, { id: "r1", name: "admin" }],
        clientMappings: { "realm-management": { id: "c1", mappings: [{ id: "r2", name: "realm-admin" }] } } });
      return new Response(null, { status: 204 });
    };
    """)


@unittest.skipUnless(shutil.which("node"), "node is required")
class SignInProbeAccount(unittest.TestCase):
    def run_account(self, existing):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            (directory / "mock.mjs").write_text(ACCOUNT_MOCK)
            calls = directory / "calls.log"
            calls.touch()
            env = dict(os.environ, CALLS=str(calls), EXISTING=existing, KEYCLOAK_URL="http://keycloak/auth", KEYCLOAK_REALM="staff",
                       PUBLIC_DOMAIN="example.test", PROBE_USERNAME="signin-probe", PROBE_PASSWORD=PASSWORD,
                       KC_BOOTSTRAP_ADMIN_USERNAME="admin", KC_BOOTSTRAP_ADMIN_PASSWORD="admin-password")
            result = subprocess.run(["node", "--import", str(directory / "mock.mjs"), str(ROOT / "k8s/files/signin-probe-account.mjs")],
                                    env=env, capture_output=True, text=True, timeout=60, check=True)
            self.assertNotIn(PASSWORD, result.stdout + result.stderr)
            return result.stdout, calls.read_text()

    def test_account_is_created_reset_and_stripped_of_access(self):
        output, calls = self.run_account("false")
        self.assertIn("Created the sign-in probe account.", output)
        self.assertIn('"requiredActions":[]', calls)
        self.assertIn("PUT /users/u1/reset-password", calls)
        self.assertIn("DELETE /attack-detection/brute-force/users/u1", calls)
        self.assertIn("DELETE /users/u1/groups/g1", calls)
        self.assertIn('DELETE /users/u1/role-mappings/realm [{"id":"r1","name":"admin"}]', calls)
        self.assertIn("DELETE /users/u1/role-mappings/clients/c1", calls)
        self.assertNotIn("default-roles-staff\"}]", calls.split("DELETE /users/u1/role-mappings/realm")[1].split("\n")[0])

    def test_existing_account_is_updated_in_place(self):
        output, calls = self.run_account("true")
        self.assertNotIn("Created", output)
        self.assertNotIn("POST /users ", calls)
        self.assertIn("PUT /users/u1 ", calls)


class SignInProbeWiring(unittest.TestCase):
    def test_alert_and_scrape_job_exist(self):
        rules = (ROOT / "k8s/files/prometheus/rules.yaml").read_text()
        for alert in ("SignInProbeFailing", "SignInProbeStale", "SignInProbeUnexpectedAccess"):
            self.assertIn(f"alert: {alert}", rules)
        self.assertIn("job_name: signin-probe", (ROOT / "k8s/files/prometheus/prometheus.yml").read_text())


if __name__ == "__main__":
    unittest.main()
