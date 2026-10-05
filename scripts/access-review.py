#!/usr/bin/env python3
"""Periodic access review: who can do what across the platform.

Collects, read-only, every Keycloak user with groups, effective roles and last
recorded login (all realms); every GitLab user with administrator flag, 2FA,
last activity and group/project memberships; Kubernetes subjects bound to
cluster-admin, admin or edit; Argo CD role mappings and local accounts; and the
local break-glass administrator accounts. Findings flag administrators, disabled
or blocked accounts that keep access, accounts without recent activity, missing
two-factor authentication and non-system cluster-admin bindings.

Owners mark each identity Keep or Revoke in the Markdown or CSV sheet and sign
it; the signed sheet is the review evidence (docs/guides/compliance-evidence.md).
Keycloak and GitLab are queried with their own in-pod administrator tools, so
this command never reads Kubernetes Secrets.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "swirl-cloud"
INACTIVE_DAYS = 90
ADMIN_GROUPS = {"platform-admins"}
ADMIN_ROLES = {"platform-admin", "admin", "realm-admin"}
IGNORED_ROLES = {"offline_access", "uma_authorization"}
ACCESS_LEVELS = {10: "guest", 15: "planner", 20: "reporter", 30: "developer", 40: "maintainer", 50: "owner"}
# Local administrators that exist outside single sign-on (rotated by
# scripts/rotate-local-admin-passwords.sh; credentials are kept in Vault).
LOCAL_ADMINS = [
    ("PostgreSQL", "postgres superuser"), ("MongoDB", "root"), ("GitLab", "root"), ("Grafana", "admin"),
    ("Keycloak", "master realm bootstrap administrator"), ("Portainer", "admin"), ("SonarQube", "admin"),
    ("Elasticsearch", "elastic and admin superusers"), ("Argo CD", "admin (when enabled)"),
]

# kcadm prints JSON without a final newline; every marker starts on a new line.
KEYCLOAK_SCRIPT = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-access-review.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
for realm in $($kc get realms --config "$cfg" --fields realm --format csv --noquotes); do
  echo; echo "##REALM $realm"
  $kc get "realms/$realm/events/config" --config "$cfg"
  echo; echo "##OLDEST"
  $kc get "realms/$realm/events" --config "$cfg" -q type=LOGIN -q first=0 -q max=1 -q direction=asc 2>/dev/null || echo "[]"
  echo; echo "##USERS"
  $kc get users -r "$realm" --config "$cfg" -q max=10000 -q briefRepresentation=true
  for id in $($kc get users -r "$realm" --config "$cfg" -q max=10000 --fields id --format csv --noquotes); do
    echo; echo "##USER $id"
    $kc get "users/$id/groups" -r "$realm" --config "$cfg" --fields name,path
    echo; echo "##ROLES"
    $kc get "users/$id/role-mappings/realm/composite" -r "$realm" --config "$cfg" --fields name
    echo; echo "##LOGIN"
    $kc get events -r "$realm" --config "$cfg" -q user="$id" -q type=LOGIN -q max=1 --fields time
  done
done
'''

GITLAB_SCRIPT = r'''
require "json"
users = User.where(user_type: [nil, 0, "human"]).order(:id).map do |u|
  { username: u.username, name: u.name, email: u.email, state: u.state, admin: u.admin?,
    two_factor: u.two_factor_enabled?, created_at: u.created_at, last_sign_in_at: u.last_sign_in_at,
    last_activity_on: u.last_activity_on, identities: u.identities.map(&:provider),
    memberships: u.members.map { |m| { source: m.source&.full_path, type: m.source_type, access: m.access_level } } }
end
puts "SWIRL_JSON" + { users: users, require_two_factor: Gitlab::CurrentSettings.require_two_factor_authentication }.to_json
'''


def run(*command, timeout=300, stdin=None):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, input=stdin)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip()).splitlines()[-1] if (result.stderr + result.stdout).strip()
                           else f"{command[0]} failed")
    return result.stdout


def kubectl_json(*args):
    return json.loads(run("kubectl", "--request-timeout=30s", *args, "-o", "json", timeout=60))


def parse_sections(text):
    """Split kcadm output into (marker, argument, json) sections."""
    sections, marker, argument, buffer = [], None, None, []
    for line in text.splitlines():
        if line.startswith("##"):
            if marker:
                sections.append((marker, argument, "\n".join(buffer)))
            parts = line[2:].split(" ", 1)
            marker, argument, buffer = parts[0], parts[1] if len(parts) > 1 else None, []
        else:
            buffer.append(line)
    if marker:
        sections.append((marker, argument, "\n".join(buffer)))
    return sections


def loads(text, default):
    try:
        return json.loads(text) if text.strip() else default
    except ValueError:
        return default


def iso(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="minutes") if milliseconds else ""


def keycloak(now):
    output = run("kubectl", "--request-timeout=30s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-s",
                 stdin=KEYCLOAK_SCRIPT, timeout=900)
    realms, identities, realm, user = {}, [], None, None
    users_by_id = {}
    for marker, argument, body in parse_sections(output):
        if marker == "REALM":
            realm = argument
            config = loads(body, {})
            realms[realm] = {"events_stored": bool(config.get("eventsEnabled")), "event_days": config.get("eventsExpiration", 0) // 86400}
        elif marker == "OLDEST":
            oldest = loads(body, [])
            realms[realm]["oldest_login_event"] = iso(oldest[0]["time"]) if oldest else ""
        elif marker == "USERS":
            users_by_id = {u["id"]: u for u in loads(body, [])}
        elif marker == "USER":
            source = users_by_id.get(argument, {"id": argument})
            user = {"system": "Keycloak", "scope": realm, "identity": source.get("username", argument),
                    "email": source.get("email", ""), "active": bool(source.get("enabled", True)),
                    "created": iso(source.get("createdTimestamp")), "groups": [g.get("path", g.get("name")) for g in loads(body, [])],
                    "roles": [], "last_activity": "", "federated": bool(source.get("federationLink"))}
            identities.append(user)
        elif marker == "ROLES":
            user["roles"] = sorted(r["name"] for r in loads(body, [])
                                   if r["name"] not in IGNORED_ROLES and not r["name"].startswith("default-roles-"))
        elif marker == "LOGIN":
            events = loads(body, [])
            user["last_activity"] = iso(events[0]["time"]) if events else ""
    for identity in identities:
        identity["admin"] = bool(ADMIN_ROLES & set(identity["roles"])) or any(
            g.strip("/") in ADMIN_GROUPS for g in identity["groups"])
        identity["access"] = ", ".join(identity["groups"] + identity["roles"]) or "sign-in only"
        coverage = realms[identity["scope"]]
        oldest = coverage.get("oldest_login_event")
        covered = oldest and datetime.fromisoformat(oldest) <= now - timedelta(days=INACTIVE_DAYS)
        identity["inactive"] = (not identity["last_activity"] and bool(covered)) or bool(
            identity["last_activity"] and datetime.fromisoformat(identity["last_activity"]) < now - timedelta(days=INACTIVE_DAYS))
    return realms, identities


def gitlab(now):
    output = run("kubectl", "--request-timeout=30s", "-n", "infra", "exec", "deploy/gitlab", "--", "gitlab-rails", "runner",
                 GITLAB_SCRIPT, timeout=900)
    line = next(line for line in output.splitlines() if line.startswith("SWIRL_JSON"))
    data = json.loads(line.removeprefix("SWIRL_JSON"))
    identities = []
    for user in data["users"]:
        memberships = [f"{m['source']} ({ACCESS_LEVELS.get(m['access'], m['access'])})" for m in user["memberships"] if m["source"]]
        last = user.get("last_activity_on") or (user.get("last_sign_in_at") or "")[:10]
        identities.append({
            "system": "GitLab", "scope": "instance", "identity": user["username"], "email": user.get("email", ""),
            "active": user["state"] == "active", "created": (user.get("created_at") or "")[:16], "admin": user["admin"],
            "groups": memberships, "roles": ["administrator"] if user["admin"] else [], "last_activity": last,
            "two_factor": user["two_factor"], "federated": bool(user["identities"]),
            "access": ", ".join((["administrator"] if user["admin"] else []) + memberships) or "no memberships",
            "inactive": bool(last) and datetime.fromisoformat(last).replace(tzinfo=timezone.utc) < now - timedelta(days=INACTIVE_DAYS)})
    return data["require_two_factor"], identities


def kubernetes():
    identities = []
    bindings = kubectl_json("get", "clusterrolebindings")["items"] + kubectl_json("get", "rolebindings", "-A")["items"]
    for binding in bindings:
        role = binding["roleRef"]["name"]
        if role not in ("cluster-admin", "admin", "edit"):
            continue
        scope = binding["metadata"].get("namespace") or "cluster"
        for subject in binding.get("subjects") or []:
            name = subject["name"]
            if subject["kind"] == "ServiceAccount":
                name = f"{subject.get('namespace', scope)}/{name}"
            system = name.startswith("system:") or subject.get("namespace") == "kube-system"
            identities.append({"system": "Kubernetes", "scope": scope, "identity": f"{subject['kind']} {name}", "email": "",
                               "active": True, "created": binding["metadata"].get("creationTimestamp", "")[:16],
                               "admin": role == "cluster-admin", "groups": [], "roles": [role],
                               "access": f"{role} via {binding['kind']} {binding['metadata']['name']}",
                               "last_activity": "", "inactive": False, "kubernetes_system": system})
    return identities


def argocd():
    rbac = kubectl_json("-n", "infra", "get", "configmap", "argocd-rbac-cm")["data"]
    settings = kubectl_json("-n", "infra", "get", "configmap", "argocd-cm")["data"]
    identities = []
    for line in rbac.get("policy.csv", "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if parts and parts[0] == "g" and len(parts) >= 3:
            identities.append({"system": "Argo CD", "scope": "sso group", "identity": parts[1], "email": "", "active": True,
                               "created": "", "admin": parts[2] == "role:admin", "groups": [], "roles": [parts[2]],
                               "access": parts[2], "last_activity": "", "inactive": False})
    accounts = {key.split(".", 1)[1].split(".")[0] for key in settings if key.startswith("accounts.")}
    for account in sorted(accounts):
        identities.append({"system": "Argo CD", "scope": "local account", "identity": account, "email": "",
                           "active": settings.get(f"accounts.{account}.enabled", "true") != "false", "created": "",
                           "admin": False, "groups": [], "roles": [settings.get(f"accounts.{account}", "")],
                           "access": settings.get(f"accounts.{account}", ""), "last_activity": "", "inactive": False})
    default = rbac.get("policy.default", "")
    return identities, settings.get("admin.enabled", "true") != "false", default


def findings(review):
    found = []
    for item in review["identities"]:
        label = f"{item['system']} {item['scope']}: {item['identity']}"
        if not item["active"] and (item["groups"] or item["roles"]):
            found.append(f"{label} is disabled or blocked but keeps {item['access']}; remove the memberships.")
        if item["inactive"]:
            found.append(f"{label} has no recorded activity in {INACTIVE_DAYS} days; confirm it is still needed.")
        if item["system"] == "GitLab" and item["active"] and not item.get("two_factor") and not review["gitlab_requires_2fa"]:
            found.append(f"{label} has no two-factor authentication and GitLab does not require it"
                         f"{' (administrator)' if item['admin'] else ''}.")
        if item["system"] == "Kubernetes" and item["admin"] and not item.get("kubernetes_system"):
            found.append(f"{label} holds cluster-admin; narrow it to the permissions it needs.")
    if review["argocd_admin_enabled"]:
        found.append("Argo CD's local admin account is enabled; disable it once SSO administration is verified.")
    for realm, coverage in review["keycloak_realms"].items():
        if not coverage.get("events_stored"):
            found.append(f"Keycloak realm {realm} does not store login events, so inactivity cannot be checked.")
    return found


def rows(review):
    return [{"system": i["system"], "scope": i["scope"], "identity": i["identity"], "email": i["email"],
             "status": "active" if i["active"] else "disabled", "administrator": "yes" if i["admin"] else "",
             "access": i["access"], "last_activity": i["last_activity"] or "not recorded", "decision": "", "reviewer": ""}
            for i in review["identities"]]


def render_markdown(review):
    lines = ["# Access review", "", f"Generated {review['generated_at']} UTC. Mark each identity **Keep** or **Revoke**,"
             " apply the revocations, then sign below.", "", "## Findings", ""]
    lines += [f"- [ ] {f}" for f in review["findings"]] or ["- None."]
    coverage = [f"{realm}: since {c.get('oldest_login_event') or 'no login recorded yet'} ({c['event_days']}-day retention)"
                for realm, c in review["keycloak_realms"].items()]
    lines += ["", "Keycloak login history: " + "; ".join(coverage) + ".", "", "## Identities", "",
              "| System | Scope | Identity | Status | Admin | Access | Last activity | Keep / Revoke |", "|---|---|---|---|---|---|---|---|"]
    for row in rows(review):
        lines.append(f"| {row['system']} | {row['scope']} | {row['identity']} | {row['status']} | {row['administrator']} | "
                     f"{row['access']} | {row['last_activity']} | |")
    lines += ["", "## Local administrator accounts", "",
              "These accounts sit outside single sign-on for break-glass use; their passwords are in Vault and rotated with"
              " `scripts/rotate-local-admin-passwords.sh`. Confirm who can read them.", ""]
    lines += [f"- {service}: {account}" for service, account in LOCAL_ADMINS]
    lines += ["", f"Argo CD default role for signed-in users: `{review['argocd_default_role'] or 'none'}`.", "",
              "## Sign-off", "", "Reviewer: ____________________  Date: ____________  Signature: ____________________", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=("text", "markdown", "json", "csv"), default="text")
    parser.add_argument("--output-dir", type=Path, help="also write Markdown, CSV and JSON files (mode 0600) here")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    review = {"generated_at": now.isoformat(timespec="minutes"), "identities": [], "errors": []}
    print("Collecting identities (GitLab takes about a minute)...", file=sys.stderr)
    review["keycloak_realms"] = {}
    for name, collect in (("Keycloak", lambda: keycloak(now)), ("GitLab", lambda: gitlab(now)),
                          ("Kubernetes", lambda: (None, kubernetes())), ("Argo CD", argocd)):
        try:
            result = collect()
        except (RuntimeError, OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
            review["errors"].append(f"{name}: {error}")
            continue
        if name == "Keycloak":
            review["keycloak_realms"], identities = result
        elif name == "GitLab":
            review["gitlab_requires_2fa"], identities = result
        elif name == "Argo CD":
            identities, review["argocd_admin_enabled"], review["argocd_default_role"] = result
        else:
            identities = result[1]
        review["identities"] += identities
    review.setdefault("gitlab_requires_2fa", True)
    review.setdefault("argocd_admin_enabled", False)
    review.setdefault("argocd_default_role", "")
    review["findings"] = findings(review) + [f"Not collected: {e}" for e in review["errors"]]
    markdown = render_markdown(review)
    table = io.StringIO()
    writer = csv.DictWriter(table, fieldnames=list(rows(review)[0]) if review["identities"] else ["system"])
    writer.writeheader()
    writer.writerows(rows(review))
    if args.format == "json":
        print(json.dumps(review, indent=2))
    elif args.format == "markdown":
        print(markdown)
    elif args.format == "csv":
        print(table.getvalue(), end="")
    else:
        counts = {}
        for item in review["identities"]:
            counts[item["system"]] = counts.get(item["system"], 0) + 1
        print(f"Access review - {review['generated_at']} UTC")
        print("Identities: " + ", ".join(f"{system} {count}" for system, count in counts.items()))
        print(f"Administrators: {sum(i['admin'] for i in review['identities'])}")
        print("Findings:" if review["findings"] else "Findings: none")
        for finding in review["findings"]:
            print(f"  - {finding}")
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        for suffix, content in (("md", markdown), ("csv", table.getvalue()), ("json", json.dumps(review, indent=2))):
            path = args.output_dir / f"access-review-{stamp}.{suffix}"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write(content)
        print(f"Review sheets: {args.output_dir}/access-review-{stamp}.{{md,csv,json}}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
