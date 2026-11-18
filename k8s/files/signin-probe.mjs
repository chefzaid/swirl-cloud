// Sign-in probe: every few minutes an account with no rights signs in through
// Keycloak's login page, as a person would, on a tool behind OAuth2 Proxy and on
// Vault's own OIDC sign-in. A broken client, a wrong client secret, a broken
// login page or a stuck account fails the probe, which the availability probes
// cannot see. The account belongs to no group, so a working sign-in ends with
// the tool admitting it and refusing access (OAuth2 Proxy sets its session
// cookie; Vault answers that the account's groups do not match the role).
// Exposes Prometheus metrics on METRICS_PORT; never logs or exports secrets.
import { createServer } from "node:http";

const domain = process.env.PUBLIC_DOMAIN;
const realm = process.env.KEYCLOAK_REALM;
const username = process.env.PROBE_USERNAME;
const password = process.env.PROBE_PASSWORD;
const interval = Number(process.env.PROBE_INTERVAL_SECONDS || 300) * 1000;
const timeout = Number(process.env.PROBE_TIMEOUT_SECONDS || 20) * 1000;
const results = new Map();

class ProbeError extends Error {
  constructor(step, reason) { super(`${step}: ${reason}`); this.step = step; this.reason = reason; }
}

// One cookie jar per run, for the installation's own hosts only.
function jar() {
  const cookies = new Map();
  return {
    header(url) {
      const host = new URL(url).hostname;
      if (host !== domain && !host.endsWith("." + domain)) return undefined;
      return [...cookies].map(([name, value]) => `${name}=${value}`).join("; ") || undefined;
    },
    store(response) {
      for (const line of response.headers.getSetCookie?.() || []) {
        const [pair] = line.split(";");
        const index = pair.indexOf("=");
        if (index > 0) cookies.set(pair.slice(0, index).trim(), pair.slice(index + 1).trim());
      }
    },
    has(name) { return Boolean(cookies.get(name)); },
  };
}

async function request(cookies, url, options = {}) {
  const headers = { "User-Agent": "swirl-cloud-signin-probe", ...(options.headers || {}) };
  const cookie = cookies.header(url);
  if (cookie) headers.Cookie = cookie;
  const response = await fetch(url, { ...options, headers, redirect: "manual", signal: AbortSignal.timeout(timeout) });
  cookies.store(response);
  return response;
}

function location(response, base, step) {
  const target = response.headers.get("location");
  if (![301, 302, 303, 307, 308].includes(response.status) || !target) {
    throw new ProbeError(step, `expected a redirect, got HTTP ${response.status}`);
  }
  return new URL(target, base).toString();
}

function decode(value) {
  return value.replace(/&amp;/g, "&").replace(/&#x3D;/gi, "=").replace(/&#61;/g, "=").replace(/&quot;/g, '"');
}

// Keycloak's login page, from its authorization URL to the client's redirect.
async function keycloakLogin(cookies, authUrl, redirectPrefix) {
  const expected = `https://keycloak.${domain}/auth/realms/${realm}/`;
  if (!authUrl.startsWith(expected)) throw new ProbeError("authorization", "the client did not send the probe to the platform realm");
  const page = await request(cookies, authUrl);
  if (page.status !== 200) throw new ProbeError("login page", `HTTP ${page.status}`);
  const html = await page.text();
  const form = html.match(/<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"/) || html.match(/<form[^>]*action="([^"]+)"[^>]*id="kc-form-login"/);
  if (!form) throw new ProbeError("login page", "no login form");
  const action = new URL(decode(form[1]), authUrl).toString();
  if (!action.startsWith(expected)) throw new ProbeError("login page", "the login form posts outside the platform realm");
  const submitted = await request(cookies, action, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username, password, credentialId: "" }).toString(),
  });
  if (submitted.status === 200) {
    const body = await submitted.text();
    const reason = /kc-form-login/.test(body) ? "the password was refused or the account is locked"
      : "Keycloak asked for an action (terms, profile or password update)";
    throw new ProbeError("sign-in", reason);
  }
  const next = location(submitted, action, "sign-in");
  if (!next.startsWith(redirectPrefix)) throw new ProbeError("sign-in", "Keycloak redirected elsewhere than the client");
  const query = new URL(next).searchParams;
  if (query.get("error")) throw new ProbeError("sign-in", `Keycloak returned ${query.get("error")}`);
  if (!query.get("code")) throw new ProbeError("sign-in", "no authorization code");
  return next;
}

// A tool behind OAuth2 Proxy: the proxy exchanges the code with its client
// secret and sets its session cookie, whatever the account may then reach.
async function oauth2Proxy() {
  const cookies = jar();
  const base = `https://keycloak.${domain}`;
  const start = await request(cookies, `${base}/oauth2/start?rd=${encodeURIComponent(base + "/")}`);
  const authUrl = location(start, base, "start");
  const callback = await keycloakLogin(cookies, authUrl, `${base}/oauth2/callback`);
  const finished = await request(cookies, callback);
  if (finished.status >= 500) throw new ProbeError("callback", `OAuth2 Proxy failed the code exchange (HTTP ${finished.status})`);
  location(finished, callback, "callback");
  if (!cookies.has("_oauth2_proxy")) throw new ProbeError("callback", "OAuth2 Proxy set no session");
  return {};
}

// Vault's OIDC sign-in: Vault exchanges the code with its client secret, then
// refuses the account because its groups do not match the administrators' role.
// Vault's public host sits behind Cloudflare Access, which turns the probe away,
// so its API calls go to Vault's cluster address (VAULT_ADDR), as the platform's
// other automation does; the redirect URI stays the public one people use.
async function vault() {
  const cookies = jar();
  const base = `https://vault.${domain}`;
  const api = process.env.VAULT_ADDR || base;
  const redirect = `${base}/ui/vault/auth/oidc/oidc/callback`;
  const started = await request(cookies, `${api}/v1/auth/oidc/oidc/auth_url`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role: process.env.VAULT_OIDC_ROLE || "platform-admin", redirect_uri: redirect }),
  });
  if (started.status !== 200) throw new ProbeError("start", `Vault answered HTTP ${started.status}`);
  const authUrl = (await started.json())?.data?.auth_url;
  if (!authUrl) throw new ProbeError("start", "Vault returned no authorization URL (is OIDC sign-in configured?)");
  const callback = new URL(await keycloakLogin(cookies, authUrl, redirect));
  const answer = await request(cookies, `${api}/v1/auth/oidc/oidc/callback?${callback.searchParams}`);
  const body = await answer.json().catch(() => ({}));
  if (answer.status === 200 && body?.auth?.client_token) {
    // Never keep a token: the account should have none.
    await request(cookies, `${api}/v1/auth/token/revoke-self`, { method: "POST", headers: { "X-Vault-Token": body.auth.client_token } }).catch(() => {});
    return { unexpectedAccess: 1 };
  }
  const errors = (body?.errors || []).join(" ");
  if (/claim|bound/i.test(errors) && !/exchang|invalid client|unauthorized_client/i.test(errors)) return {};
  throw new ProbeError("callback", `Vault refused the sign-in: ${errors.slice(0, 200) || "HTTP " + answer.status}`);
}

const targets = { "oauth2-proxy": oauth2Proxy, vault };

async function run(name) {
  const started = Date.now();
  let entry;
  try {
    const extra = await targets[name]();
    entry = { success: 1, unexpectedAccess: extra.unexpectedAccess || 0 };
    console.log(JSON.stringify({ target: name, success: true, unexpected_access: Boolean(extra.unexpectedAccess) }));
  } catch (error) {
    const step = error instanceof ProbeError ? error.step : "request";
    const reason = error instanceof ProbeError ? error.reason : error.name === "TimeoutError" ? "timed out" : "connection failed";
    entry = { success: 0, unexpectedAccess: 0 };
    console.log(JSON.stringify({ target: name, success: false, step, reason }));
  }
  results.set(name, { ...entry, duration: (Date.now() - started) / 1000, finished: Date.now() / 1000 });
}

function metrics() {
  const lines = [
    "# HELP swirl_signin_probe_success Whether the probe account's last sign-in through Keycloak worked (1 or 0).",
    "# TYPE swirl_signin_probe_success gauge",
    "# HELP swirl_signin_probe_duration_seconds How long the last sign-in took.",
    "# TYPE swirl_signin_probe_duration_seconds gauge",
    "# HELP swirl_signin_probe_last_run_timestamp_seconds When the last sign-in finished.",
    "# TYPE swirl_signin_probe_last_run_timestamp_seconds gauge",
    "# HELP swirl_signin_probe_unexpected_access Whether the tool gave the probe account access it should not have (1 or 0).",
    "# TYPE swirl_signin_probe_unexpected_access gauge",
  ];
  for (const [name, entry] of results) {
    const label = `{target="${name}"}`;
    lines.push(`swirl_signin_probe_success${label} ${entry.success}`, `swirl_signin_probe_duration_seconds${label} ${entry.duration}`,
      `swirl_signin_probe_last_run_timestamp_seconds${label} ${entry.finished}`, `swirl_signin_probe_unexpected_access${label} ${entry.unexpectedAccess}`);
  }
  return lines.join("\n") + "\n";
}

async function loop() {
  for (const name of (process.env.PROBE_TARGETS || "oauth2-proxy,vault").split(",").filter((item) => targets[item])) await run(name);
  // One round for tests: print the metrics and stop.
  if (process.env.PROBE_ONCE === "true") { process.stdout.write(metrics()); server.close(); return; }
  setTimeout(loop, interval);
}

const server = createServer((req, res) => {
  if (req.url === "/metrics") { res.writeHead(200, { "Content-Type": "text/plain; version=0.0.4" }); res.end(metrics()); return; }
  if (req.url === "/healthz") { res.writeHead(200); res.end("ok\n"); return; }
  res.writeHead(404); res.end();
});
server.listen(Number(process.env.METRICS_PORT || 9464), loop);
