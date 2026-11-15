// Keeps the sign-in probe's Keycloak account as the probe needs it, on every
// sync: a local account in the platform realm, enabled, with no pending action,
// no group, no role beyond the realm's defaults, no lockout, and the password
// from the signin-probe-credentials Secret. Removes any group or role someone
// gave it, so the probe can never reach a tool.
const base = process.env.KEYCLOAK_URL;
const realm = process.env.KEYCLOAK_REALM;
const username = process.env.PROBE_USERNAME;

async function token() {
  const response = await fetch(`${base}/realms/master/protocol/openid-connect/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: "admin-cli", grant_type: "password",
      username: process.env.KC_BOOTSTRAP_ADMIN_USERNAME, password: process.env.KC_BOOTSTRAP_ADMIN_PASSWORD }) });
  if (!response.ok) throw new Error(`Keycloak administrator sign-in failed (HTTP ${response.status})`);
  return (await response.json()).access_token;
}

const access = await token();
async function api(method, path, body) {
  const response = await fetch(`${base}/admin/realms/${realm}${path}`, {
    method, headers: { Authorization: `Bearer ${access}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (!response.ok) throw new Error(`${method} ${path.replace(/[0-9a-f-]{36}/g, "<id>")}: HTTP ${response.status}`);
  return response.status === 204 || response.headers.get("content-length") === "0" ? null : response.json().catch(() => null);
}

const find = async () => (await api("GET", `/users?username=${encodeURIComponent(username)}&exact=true`))?.[0];
const profile = { username, enabled: true, emailVerified: true, email: `${username}@${process.env.PUBLIC_DOMAIN}`,
  firstName: "Sign-in", lastName: "Probe", requiredActions: [] };
let user = await find();
if (!user) {
  await api("POST", "/users", profile);
  user = await find();
  console.log("Created the sign-in probe account.");
}
if (!user) throw new Error("The sign-in probe account was not created");
if (user.federationLink) throw new Error("The sign-in probe account comes from the directory; it must be a local account");
await api("PUT", `/users/${user.id}`, { ...profile, id: user.id });
await api("PUT", `/users/${user.id}/reset-password`, { type: "password", value: process.env.PROBE_PASSWORD, temporary: false });
await api("DELETE", `/attack-detection/brute-force/users/${user.id}`);

for (const group of (await api("GET", `/users/${user.id}/groups`)) || []) {
  await api("DELETE", `/users/${user.id}/groups/${group.id}`);
  console.log(`Removed the probe account from group ${group.name}.`);
}
const mappings = (await api("GET", `/users/${user.id}/role-mappings`)) || {};
const extra = (mappings.realmMappings || []).filter((role) => role.name !== `default-roles-${realm.toLowerCase()}`);
if (extra.length) {
  await api("DELETE", `/users/${user.id}/role-mappings/realm`, extra);
  console.log(`Removed realm roles ${extra.map((role) => role.name).join(", ")} from the probe account.`);
}
for (const [client, entry] of Object.entries(mappings.clientMappings || {})) {
  await api("DELETE", `/users/${user.id}/role-mappings/clients/${entry.id}`, entry.mappings);
  console.log(`Removed ${client} roles from the probe account.`);
}
console.log("The sign-in probe account is ready.");
