// Platform GitLab tokens expire (at most a year) and the jobs that use them then
// fail. Once a day, ask GitLab when each token expires and raise
// PlatformTokenExpiring in Alertmanager 30 days ahead (critical within 7 days,
// or when GitLab no longer accepts it). Token values never leave this process.
const gitlab = process.env.GITLAB_API_URL;
const alertmanager = process.env.ALERTMANAGER_URL;
const runbooks = process.env.RUNBOOK_BASE_URL;
const now = Date.now();
const DAY = 86400000;

// Each token comes from an environment variable TOKEN_<NAME>; a missing one is skipped.
const tokens = Object.entries(process.env)
  .filter(([key, value]) => key.startsWith("TOKEN_") && value)
  .map(([key, value]) => ({ name: key.slice(6).toLowerCase().replaceAll("_", "-"), value }));

async function check({ name, value }) {
  const response = await fetch(`${gitlab}/personal_access_tokens/self`, { headers: { "PRIVATE-TOKEN": value } });
  if (response.status === 401) return { name, days: null, state: "rejected by GitLab (expired or revoked)" };
  if (!response.ok) throw new Error(`${name}: GitLab answered ${response.status}`);
  const token = await response.json();
  if (!token.active || token.revoked) return { name, days: null, state: "no longer active" };
  if (!token.expires_at) return { name, days: Infinity, state: "never expires" };
  const days = Math.floor((Date.parse(`${token.expires_at}T00:00:00Z`) - now) / DAY);
  return { name, days, state: `expires ${token.expires_at}` };
}

const results = [];
const failures = [];
for (const token of tokens) {
  try {
    results.push(await check(token));
  } catch (error) {
    failures.push(error.message);
  }
}
const alerts = results.filter((result) => result.days === null || result.days <= 30).map((result) => ({
  labels: { alertname: "PlatformTokenExpiring", severity: result.days === null || result.days <= 7 ? "critical" : "warning",
            token: result.name, owner: "platform", source: "token-expiry" },
  annotations: {
    summary: "A platform GitLab token expires soon",
    description: result.days === null ? `The ${result.name} token is ${result.state}; the job that uses it fails until it is replaced.`
      : `The ${result.name} token ${result.state} (${result.days} days); rerun scripts/configure-gitlab-ci.sh to replace it.`,
    runbook_url: `${runbooks}#platformtokenexpiring` },
  // Until the next daily run, with an hour to spare.
  startsAt: new Date(now).toISOString(), endsAt: new Date(now + 25 * 3600000).toISOString() }));
if (alerts.length) {
  const response = await fetch(`${alertmanager}/api/v2/alerts`, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(alerts) });
  if (!response.ok) failures.push(`Alertmanager: HTTP ${response.status}`);
}
for (const result of results) console.log(`${result.name}: ${result.state}`);
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
