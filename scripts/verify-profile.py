#!/usr/bin/env python3
"""What an account of the read-only profile can and cannot do, checked live (TODO §51).

  verify-profile.py [--user readonly-test] [--format text|markdown|json]

Run on a control plane as an administrator. Every check asks the service
itself, read-only, and compares the answer with the profile's table in
docs/security.md#read-only-profile:

  Keycloak        the account's groups are exactly platform-readers and access-portal
  Kubernetes      `kubectl auth can-i` as the account (impersonation): reads, no
                  Secrets, no writes, no exec or port-forward
  Argo CD         `argocd admin settings rbac can` for the group: get, no sync,
                  delete, exec or override
  Grafana         the account's organization role is Viewer, not a server admin
                  (once it has signed in)
  Elasticsearch   the account's Kibana user holds platform_reader only, and its
                  privileges (run as it) read the indices but write nothing
  Kafka UI        the group's role has view actions only
  GitLab          the account is not an administrator and holds Reporter at most
                  in the platform group and its projects (once it has signed in)
  SonarQube       sonar-users, which every account joins, holds no global permission
  Vault           no OIDC role admits the group
  Vaultwarden     the account belongs to no organization but `Read-only demo`

Exit 1 when any check fails; a service it cannot reach is reported and fails too.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
GROUP = "platform-readers"
EXPECTED_GROUPS = {"platform-readers", "access-portal"}
DEMO_ORGANIZATION = "Read-only demo"
TOKEN_FILE = "/var/lib/swirl-cloud/vault-bootstrap-token"
KAFKA_READ_ACTIONS = {"VIEW", "MESSAGES_READ", "ANALYSIS_VIEW"}


def run(*command, stdin=None, timeout=300):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1][:300])
    return result.stdout


def kubectl(*args, stdin=None, timeout=300):
    return run("kubectl", "--request-timeout=60s", *args, stdin=stdin, timeout=timeout)


def secret_value(name, key, namespace="infra"):
    return base64.b64decode(kubectl("-n", namespace, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}")).decode()


def row(service, check, expected, observed):
    return {"service": service, "check": check, "expected": expected, "observed": observed,
            "result": "PASS" if expected == observed else "FAIL"}


KEYCLOAK_GROUPS = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-profile.$$.cfg"
trap 'rm -f "$cfg"' EXIT
IFS= read -r realm
IFS= read -r user
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
id="$($kc get users -r "$realm" --config "$cfg" -q exact=true -q username="$user" --fields id --format csv --noquotes)"
[ -n "$id" ] || { echo "NO_USER"; exit 0; }
$kc get "users/$id/groups" -r "$realm" --config "$cfg" --fields name --format csv --noquotes
'''


def keycloak(user, realm):
    out = kubectl("-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-c", KEYCLOAK_GROUPS, stdin=f"{realm}\n{user}\n")
    if out.strip() == "NO_USER":
        return [row("Keycloak", f"account {user} exists", "yes", "no")]
    groups = {line.strip() for line in out.splitlines() if line.strip()}
    return [row("Keycloak", f"{user}'s groups", ", ".join(sorted(EXPECTED_GROUPS)), ", ".join(sorted(groups)) or "none")]


KUBERNETES_CHECKS = [
    ("list pods in every namespace", ["get", "pods", "--all-namespaces"], "yes"),
    ("read pod logs", ["get", "pods/log", "-n", "infra"], "yes"),
    ("read Secrets", ["get", "secrets", "--all-namespaces"], "no"),
    ("list Secrets in infra", ["list", "secrets", "-n", "infra"], "no"),
    ("create Deployments", ["create", "deployments", "-n", "apps"], "no"),
    ("change Argo CD Applications", ["patch", "applications.argoproj.io", "-n", "infra"], "no"),
    ("delete pods", ["delete", "pods", "-n", "infra"], "no"),
    ("exec into pods", ["create", "pods/exec", "-n", "infra"], "no"),
    ("port-forward to pods", ["create", "pods/portforward", "-n", "infra"], "no"),
    ("change RBAC", ["create", "clusterrolebindings"], "no"),
]


def kubernetes(user):
    rows = []
    for label, args, expected in KUBERNETES_CHECKS:
        result = subprocess.run(["kubectl", "--request-timeout=60s", "auth", "can-i", *args, f"--as=oidc:{user}",
                                 f"--as-group=oidc:{GROUP}", "--as-group=oidc:access-portal"],
                                capture_output=True, text=True, timeout=120)
        observed = result.stdout.strip().split()[0] if result.stdout.strip() else "error: " + result.stderr.strip()[:80]
        rows.append(row("Kubernetes", label, expected, observed))
    return rows


ARGOCD_CHECKS = [("get applications", ["get", "applications", "*/*"], "Yes"),
                 ("sync applications", ["sync", "applications", "*/*"], "No"),
                 ("delete applications", ["delete", "applications", "*/*"], "No"),
                 ("override applications", ["override", "applications", "*/*"], "No"),
                 ("exec into pods", ["create", "exec", "*/*"], "No"),
                 ("change settings", ["update", "projects", "*"], "No")]


def argocd():
    rows = []
    for label, args, expected in ARGOCD_CHECKS:
        out = kubectl("-n", "infra", "exec", "deploy/argocd-server", "--", "argocd", "admin", "settings", "rbac", "can", GROUP, *args,
                      "--namespace", "infra")
        observed = "Yes" if out.strip().endswith("Yes") else "No" if out.strip().endswith("No") else out.strip()[:60]
        rows.append(row("Argo CD", label, expected, observed))
    return rows


def through_forward(service, port, path, headers):
    """GET path on a Service through a short kubectl port-forward."""
    import selectors
    import time
    import urllib.request
    process = subprocess.Popen(["kubectl", "port-forward", "--address=127.0.0.1", "-n", "infra", f"service/{service}", f":{port}"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        local = None
        deadline = time.monotonic() + 20
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline and process.poll() is None and local is None:
                if selector.select(timeout=1):
                    match = re.search(rf"Forwarding from 127\.0\.0\.1:(\d+) -> {port}", process.stdout.readline())
                    local = match and int(match[1])
        if not local:
            raise RuntimeError(f"cannot reach service/{service}")
        request = urllib.request.Request(f"http://127.0.0.1:{local}{path}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None
    finally:
        process.terminate()
        process.wait(timeout=5)


def basic(username, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()}


def grafana(user):
    password = secret_value("grafana-admin-secret", "GF_SECURITY_ADMIN_PASSWORD")
    status, found = through_forward("grafana", 3000, f"/api/users/lookup?loginOrEmail={user}", basic("admin", password))
    if status == 404:
        return [row("Grafana", f"{user}'s role (after a first sign-in)", "Viewer", "not signed in yet")]
    status, orgs = through_forward("grafana", 3000, f"/api/users/{found['id']}/orgs", basic("admin", password))
    roles = sorted({org["role"] for org in orgs or []})
    return [row("Grafana", "organization role", "Viewer", ", ".join(roles) or "none"),
            row("Grafana", "server administrator", "no", "yes" if found.get("isGrafanaAdmin") else "no")]


ELASTIC = r'''
set -eu
IFS= read -r user
api() { curl -sS -u "elastic:$ELASTIC_PASSWORD" -H 'Content-Type: application/json' "$@"; }
echo "USER $(api "localhost:9200/_security/user/$user")"
echo "PRIVILEGES $(api -H "es-security-runas-user: $user" -X POST localhost:9200/_security/user/_has_privileges --data-binary '{
  "cluster": ["manage", "manage_security"],
  "index": [{"names": ["kubernetes-logs-*", "identity-audit-*"], "privileges": ["read", "write", "delete", "manage"]}],
  "application": [{"application": "kibana-.kibana", "privileges": ["all", "read"], "resources": ["*"]}]}')"
'''


def elasticsearch(user):
    out = kubectl("-n", "infra", "exec", "-i", "deploy/elasticsearch", "--", "sh", "-c", ELASTIC, stdin=f"{user}\n")
    lines = dict(line.split(" ", 1) for line in out.splitlines() if " " in line)
    account = json.loads(lines.get("USER", "{}")).get(user)
    if not account:
        return [row("Elasticsearch", f"Kibana user {user}", "platform_reader", "no user yet (the sync runs every 5 minutes)")]
    privileges = json.loads(lines.get("PRIVILEGES", "{}"))
    index = privileges.get("index", {}).get("kubernetes-logs-*", {})
    kibana = privileges.get("application", {}).get("kibana-.kibana", {}).get("*", {})
    cluster = privileges.get("cluster", {})
    return [row("Elasticsearch", "roles", "platform_reader", ", ".join(account.get("roles", []))),
            row("Elasticsearch", "read logs", "yes", "yes" if index.get("read") else "no"),
            row("Elasticsearch", "write or delete logs", "no", "yes" if index.get("write") or index.get("delete") or index.get("manage") else "no"),
            row("Elasticsearch", "manage the cluster or security", "no", "yes" if any(cluster.values()) else "no"),
            row("Kibana", "read every feature", "yes", "yes" if kibana.get("read") else "no"),
            row("Kibana", "change anything", "no", "yes" if kibana.get("all") else "no")]


def kafka_ui():
    deployment = json.loads(kubectl("-n", "infra", "get", "deployment", "kafka-ui", "-o", "json"))
    env = {e["name"]: e.get("value") for e in deployment["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    index = next((key.split("_")[2] for key, value in env.items() if re.fullmatch(r"RBAC_ROLES_\d+_NAME", key) and value == GROUP), None)
    if index is None:
        return [row("Kafka UI", "read-only role", "present", "missing")]
    actions = sorted({value for key, value in env.items() if re.fullmatch(rf"RBAC_ROLES_{index}_PERMISSIONS_\d+_ACTIONS_\d+", key)})
    return [row("Kafka UI", "actions beyond viewing", "none", ", ".join(a for a in actions if a not in KAFKA_READ_ACTIONS) or "none")]


GITLAB = r'''
user = Identity.find_by(provider: "openid_connect", extern_uid: ARGV[0])&.user
group = Group.find_by_full_path(ARGV[1])
if user.nil?
  puts "SWIRL_JSON" + {missing: true}.to_json
else
  ids = group.self_and_descendants.pluck(:id)
  projects = Project.where(namespace_id: ids).pluck(:id)
  levels = GroupMember.where(user_id: user.id, source_id: ids).pluck(:access_level) +
           ProjectMember.where(user_id: user.id, source_id: projects).pluck(:access_level)
  puts "SWIRL_JSON" + {admin: user.admin?, state: user.state, highest: levels.max}.to_json
end
'''


def gitlab(user, group_path):
    out = kubectl("-n", "infra", "exec", "-i", "deploy/gitlab", "--", "gitlab-rails", "runner", GITLAB, user, group_path, timeout=600)
    line = next((line for line in out.splitlines() if line.startswith("SWIRL_JSON")), None)
    if line is None:
        raise RuntimeError("GitLab returned no answer")
    found = json.loads(line.removeprefix("SWIRL_JSON"))
    if found.get("missing"):
        return [row("GitLab", f"{user}'s access (after a first sign-in)", "Reporter", "not signed in yet")]
    names = {None: "none", 10: "Guest", 15: "Planner", 20: "Reporter", 30: "Developer", 40: "Maintainer", 50: "Owner"}
    return [row("GitLab", "administrator", "no", "yes" if found["admin"] else "no"),
            row("GitLab", "account approved", "active", found["state"]),
            row("GitLab", "highest role in the platform group", "Reporter", names.get(found["highest"], str(found["highest"])))]


def sonarqube():
    token = secret_value("sonarqube-sso-admin-token", "admin-token")
    status, found = through_forward("sonarqube", 9000, "/api/permissions/groups?ps=100", {"Authorization": f"Bearer {token}"})
    if status != 200:
        raise RuntimeError(f"SonarQube answered {status}")
    group = next((g for g in found.get("groups", []) if g["name"] == "sonar-users"), {"permissions": []})
    return [row("SonarQube", "global permissions of sonar-users (every account)", "none", ", ".join(group["permissions"]) or "none")]


VAULT_ROLES = r'''
set -eu
IFS= read -r VAULT_TOKEN
export VAULT_TOKEN
for role in $(vault list -format=json auth/oidc/role 2>/dev/null | tr -d '[]",' ); do
  echo "ROLE $role $(vault read -format=json "auth/oidc/role/$role" | tr -d '\n')"
done
'''


def vault():
    token = run("sudo", "-n", "cat", TOKEN_FILE).strip()
    pod = run("bash", "-c", f"source {ROOT / 'scripts/lib/vault-access.sh'}; vault_runtime_pod").strip()
    out = kubectl("-n", "infra", "exec", "-i", pod, "-c", "vault", "--", "sh", "-c", VAULT_ROLES, stdin=token + "\n")
    admitting = []
    for line in out.splitlines():
        if line.startswith("ROLE "):
            _, name, body = line.split(" ", 2)
            claims = json.dumps(json.loads(body).get("data", {}).get("bound_claims") or {})
            if GROUP in claims:
                admitting.append(name)
    return [row("Vault", "OIDC roles that admit the group", "none", ", ".join(admitting) or "none")]


def vaultwarden(email):
    target = run("bash", "-c", 'source "$1"; postgres_runtime_target infra', "profile", str(ROOT / "scripts/lib/postgres-access.sh")).strip()
    sql = ("SELECT o.name FROM organizations o JOIN users_organizations uo ON uo.org_uuid = o.uuid "
           "JOIN users u ON u.uuid = uo.user_uuid WHERE lower(u.email) = lower(:'email') ORDER BY 1;")
    script = ('IFS= read -r email; exec psql -X -qAt --set=ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d vaultwarden --set=email="$email"')
    out = kubectl("-n", "infra", "exec", "-i", target, "-c", "postgres", "--", "sh", "-c", script, stdin=f"{email}\n{sql}\n")
    organizations = [line for line in out.splitlines() if line.strip()]
    return [row("Vaultwarden", "organizations shared with the account", DEMO_ORGANIZATION, ", ".join(organizations) or "none")]


def platform_settings():
    application = json.loads(kubectl("-n", "infra", "get", "application", "swirl-cloud", "-o", "json"))
    helm = application["spec"]["source"].get("helm", {})
    values = {item["name"]: item.get("value") for item in helm.get("parameters", [])}
    values.update(helm.get("valuesObject") or {})
    return values.get("keycloakRealm") or values.get("organizationSlug"), values.get("publicDomain"), values.get("gitlabGroupPath")


def verify(user, checks=None):
    realm, domain, group_path = platform_settings() if checks is None else ("realm", "example.com", "group")
    checks = checks or [("Keycloak", lambda: keycloak(user, realm)), ("Kubernetes", lambda: kubernetes(user)), ("Argo CD", argocd),
                        ("Grafana", lambda: grafana(user)), ("Elasticsearch", lambda: elasticsearch(user)), ("Kafka UI", kafka_ui),
                        ("GitLab", lambda: gitlab(user, group_path)), ("SonarQube", sonarqube), ("Vault", vault),
                        ("Vaultwarden", lambda: vaultwarden(f"{user}@{domain}"))]
    rows = []
    for service, check in checks:
        try:
            rows += check()
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            rows.append({"service": service, "check": "reachable", "expected": "yes", "observed": str(error)[:120], "result": "FAIL"})
    return rows


def render(rows, user, markdown=False):
    if markdown:
        lines = [f"# Read-only profile: {user}", "", "| Service | Check | Expected | Observed | Result |", "|---|---|---|---|---|"]
        lines += [f"| {r['service']} | {r['check']} | {r['expected']} | {r['observed']} | {r['result']} |" for r in rows]
    else:
        width = max(len(r["service"]) for r in rows)
        lines = [f"{r['result']:<4}  {r['service']:<{width}}  {r['check']}: {r['observed']} (expected {r['expected']})" for r in rows]
    failed = sum(r["result"] == "FAIL" for r in rows)
    lines += ["", f"{len(rows) - failed} of {len(rows)} checks as the profile requires" + (f"; {failed} failed." if failed else ".")]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", default="readonly-test")
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}", args.user):
        parser.error("--user is a Keycloak username")
    rows = verify(args.user)
    print(json.dumps(rows, indent=2) if args.format == "json" else render(rows, args.user, args.format == "markdown"), end="")
    return 1 if any(r["result"] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
