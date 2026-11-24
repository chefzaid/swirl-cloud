#!/usr/bin/env python3
"""What the platform holds for one application, before removing it (TODO §46).

  offboarding-plan.py --application shop [--format markdown|json]

Read-only. It lists, for the application as onboarding named it: its Argo CD
Applications and their namespaces, the hostnames of their Ingresses (the DNS
records to remove), their volume claims, the Vault paths its External Secrets
read, its PostgreSQL roles and databases, its Kafka users and topics, and the
Keycloak clients named after it. It then prints the export to take first and the
removal steps in order. Nothing is changed; a source it cannot read is listed as
not checked. Docs: docs/guides/application-onboarding.md#offboarding.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[a-z][a-z0-9-]{0,30}")


def run(*command, stdin=None):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1][:300])
    return result.stdout


def kubectl_json(*args):
    return json.loads(run("kubectl", "--request-timeout=60s", *args, "-o", "json"))


def applications(app):
    """Argo CD Applications of the application: named <app>-<environment> or <app>, or built from its repository."""
    found = []
    for item in kubectl_json("-n", "infra", "get", "applications.argoproj.io")["items"]:
        name = item["metadata"]["name"]
        source = item["spec"].get("source") or (item["spec"].get("sources") or [{}])[0]
        repository = re.sub(r"\.git$", "", source.get("repoURL", "")).rsplit("/", 1)[-1]
        if name == app or name.startswith(app + "-") and name.rsplit("-", 1)[-1] in ("int", "uat", "prod") or repository == app:
            found.append({"name": name, "namespace": item["spec"].get("destination", {}).get("namespace", ""),
                          "repository": source.get("repoURL", "")})
    return found


def namespaced(namespaces, app):
    hosts, claims, vault = set(), [], set()
    for namespace in sorted(namespaces):
        shared = namespace in ("apps", "corp", "infra")
        for ingress in kubectl_json("-n", namespace, "get", "ingress")["items"]:
            if shared and not ingress["metadata"]["name"].startswith(app):
                continue
            hosts |= {rule["host"] for rule in ingress["spec"].get("rules", []) if rule.get("host")}
        for claim in kubectl_json("-n", namespace, "get", "pvc")["items"]:
            if shared and not claim["metadata"]["name"].startswith(app):
                continue
            claims.append(f"{namespace}/{claim['metadata']['name']} ({claim['spec'].get('resources', {}).get('requests', {}).get('storage', '?')})")
        for secret in kubectl_json("-n", namespace, "get", "externalsecrets.external-secrets.io")["items"]:
            for entry in secret["spec"].get("data", []) + secret["spec"].get("dataFrom", []):
                key = (entry.get("remoteRef") or entry.get("extract") or {}).get("key")
                if key and (not shared or key.startswith(f"apps/{app}/")):
                    vault.add(key)
    return sorted(hosts), claims, sorted(vault)


def postgres(app):
    # Underscores are LIKE wildcards; match them literally.
    prefix = app.replace("-", "_").replace("_", "\\_") + "\\_"
    target = run("bash", "-c", 'source "$1"; postgres_runtime_target infra', "plan", str(ROOT / "scripts/lib/postgres-access.sh")).strip()
    sql = (f"SELECT 'role ' || rolname FROM pg_roles WHERE rolname LIKE '{prefix}%' ESCAPE '\\' "
           f"UNION ALL SELECT 'database ' || datname FROM pg_database WHERE datname LIKE '{prefix}%' ESCAPE '\\' ORDER BY 1;")
    out = run("kubectl", "-n", "infra", "exec", "-i", target, "-c", "postgres", "--", "sh", "-c",
              'exec psql -X -qAt -U "$POSTGRES_USER" -d postgres', stdin=sql)
    return [line for line in out.splitlines() if line.strip()]


def kafka(app):
    users = run("kubectl", "-n", "infra", "exec", "kafka-0", "--", "kafka-configs", "--bootstrap-server", "localhost:9092",
                "--describe", "--entity-type", "users")
    topics = run("kubectl", "-n", "infra", "exec", "kafka-0", "--", "kafka-topics", "--bootstrap-server", "localhost:9092", "--list")
    prefix = app.replace("-", "_") + "_"
    found = sorted({f"user {match}" for match in re.findall(r"user-principal '([^']+)'", users) if match.startswith(prefix)})
    return found + sorted(f"topic {topic}" for topic in topics.split() if topic.startswith(app + "."))


def keycloak_clients(app):
    script = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-plan.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
for realm in $($kc get realms --config "$cfg" --fields realm --format csv --noquotes); do
  $kc get clients -r "$realm" --config "$cfg" --fields clientId --format csv --noquotes | sed "s|^|$realm |"
done
'''
    out = run("kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-s", stdin=script)
    return [f"{line.split(' ', 1)[0]}: {line.split(' ', 1)[1]}" for line in out.splitlines()
            if " " in line and line.split(" ", 1)[1].startswith(app)]


def plan(app, sources=None):
    sources = sources or {"applications": applications, "namespaced": namespaced, "postgres": postgres, "kafka": kafka,
                          "keycloak": keycloak_clients}
    result = {"application": app, "argocd": [], "hosts": [], "volumes": [], "vault": [], "postgres": [], "kafka": [],
              "keycloak": [], "not_checked": []}

    def attempt(label, function, *args):
        try:
            return function(*args)
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            result["not_checked"].append(f"{label}: {error}")
            return None
    result["argocd"] = attempt("Argo CD", sources["applications"], app) or []
    found = attempt("namespaces", sources["namespaced"], {a["namespace"] for a in result["argocd"] if a["namespace"]}, app)
    if found:
        result["hosts"], result["volumes"], result["vault"] = found
    for key, label in (("postgres", "PostgreSQL"), ("kafka", "Kafka"), ("keycloak", "Keycloak")):
        result[key] = attempt(label, sources[key], app) or []
    return result


def render(result):
    app = result["application"]
    section = lambda title, items: [f"## {title}", ""] + ([f"- {item}" for item in items] or ["None found."]) + [""]  # noqa: E731
    lines = [f"# Offboarding plan: {app}", "", "Read-only inventory; nothing was changed.", ""]
    lines += section("Argo CD Applications", [f"{a['name']} -> {a['namespace']} ({a['repository']})" for a in result["argocd"]])
    lines += section("Public hostnames (DNS records to remove)", result["hosts"])
    lines += section("Volumes (export, then delete)", result["volumes"])
    lines += section("Vault paths", result["vault"])
    lines += section("PostgreSQL roles and databases", result["postgres"])
    lines += section("Kafka users and topics", result["kafka"])
    lines += section("Keycloak clients", result["keycloak"])
    if result["not_checked"]:
        lines += section("Not checked", result["not_checked"])
    lines += ["## Order", "",
              "1. Export: a dump of each database above, a copy of each volume (checkpoint or Longhorn backup), and the GitLab"
              " project export with its Registry images. Keep them as long as the processing records say.",
              "2. Delete the Argo CD Applications (their resources and namespaces go with them) and the runners.",
              "3. Remove the DNS records above, the Keycloak clients, the Vault paths, the database roles and databases, and the"
              " Kafka users and topics.",
              "4. Archive the GitLab project, then remove its dashboards, probes and alert rules from this repository.",
              "5. Check: `./install.sh --status`, `./install.sh --access-review` and the DNS zone show nothing of it, and"
              " rerun this plan, which should find nothing.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--application", required=True)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    if not NAME.fullmatch(args.application):
        parser.error("--application is the application's DNS label, as onboarding named it")
    result = plan(args.application)
    print(json.dumps(result, indent=2) if args.format == "json" else render(result), end="" if args.format == "markdown" else "\n")
    return 1 if result["not_checked"] else 0


if __name__ == "__main__":
    sys.exit(main())
