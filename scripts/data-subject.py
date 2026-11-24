#!/usr/bin/env python3
"""Data subject access requests (GDPR Art. 15 and 20): one person's data, exported.

  data-subject.py export --subject person@example.com [--output-dir DIR] [--reference DSR-12]
  data-subject.py erase --subject person@example.com --plan [--reference DSR-12]
  data-subject.py list

export collects what the platform holds about the person, found by email:
their Keycloak account in the platform realm (profile, groups, linked identity
providers, consents, sessions, and their credentials' types and dates, never
the credentials), their sign-in and administration events, their time-limited
access grants, their Zammad account with the tickets and messages they wrote,
their GitLab account (profile, memberships, keys' titles, and how many issues,
merge requests and comments they wrote), their Odoo contacts and user, and
whether they have a Vaultwarden account (its
vault is encrypted with their master password, which only they can export).
Fields that look like secrets (password, token, secret, hash, key) are removed.
It writes one JSON file per source and a summary to a new private directory,
for the person in a portable format, and records the request (date, reference,
sources, never the email) in the ConfigMap infra/swirl-cloud-data-subject-requests.
erase --plan (Art. 17) finds the same data and prints, per source, what to
erase, what to anonymize and what the law or security lets the company keep,
in the order to follow; it changes nothing. The answer is due within one month
(Art. 12(3)). Applications keep their own
data; each answers through its owner until the onboarding contract has a
privacy hook. Everything is read inside the pods; no Secret leaves the cluster.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SECRET = re.compile(r"(?i)password|token|secret|hash|salt|private|credential_?data|(^|_)key($|_)|otp|akey|security_stamp")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOG = ("infra", "swirl-cloud-data-subject-requests", "requests.json")
LIMIT = 10000

KEYCLOAK = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-dsr.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
IFS= read -r email
users="$($kc get users -r "$1" --config "$cfg" -q email="$email" -q exact=true)"
printf '{"users":%s' "$users"
for id in $(printf '%s' "$users" | sed -n 's/.*"id" *: *"\([0-9a-f-]*\)".*/\1/p'); do
  printf ',"%s":{' "$id"
  first=true
  for part in groups consents federated-identity credentials sessions; do
    $first || printf ','
    first=false
    printf '"%s":%s' "$part" "$($kc get "users/$id/$part" -r "$1" --config "$cfg" 2>/dev/null || echo null)"
  done
  printf '}'
done
printf '}'
'''


def run(*command, stdin=None):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1][:300])
    return result.stdout


def redact(value):
    """The value without fields that look like secrets, at any depth."""
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items() if not SECRET.search(str(key))}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def identity():
    return json.loads(run("kubectl", "-n", "infra", "get", "configmap", "swirl-cloud-identity", "-o", "json"))["data"]


def keycloak(realm, email):
    raw = run("kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-c", KEYCLOAK, "dsr", realm,
              stdin=email + "\n")
    data = json.loads(raw)
    users = data.pop("users")
    return [{"account": user, **data.get(user["id"], {})} for user in users]


GITLAB = r'''
require "json"
email = STDIN.read.strip
user = User.find_by_any_email(email, confirmed: true)
if user.nil?
  puts "SWIRL_JSON[]"
else
  puts "SWIRL_JSON" + [{
    username: user.username, name: user.name, email: user.email, state: user.state, created_at: user.created_at,
    last_sign_in_at: user.last_sign_in_at, last_activity_on: user.last_activity_on, bio: user.bio, location: user.location,
    website_url: user.website_url, organization: user.organization, job_title: user.job_title,
    identities: user.identities.map { |i| { provider: i.provider, created_at: i.created_at } },
    emails: user.emails.map(&:email), ssh_keys: user.keys.map { |k| { title: k.title, created_at: k.created_at } },
    memberships: user.members.map { |m| { source: m.source&.full_path, type: m.source_type, access_level: m.access_level } },
    authored: { issues: Issue.where(author_id: user.id).count, merge_requests: MergeRequest.where(author_id: user.id).count,
                comments: Note.where(author_id: user.id).count }
  }].to_json
end
'''


def gitlab(email):
    output = run("kubectl", "--request-timeout=30s", "-n", "infra", "exec", "-i", "deploy/gitlab", "--", "gitlab-rails", "runner", GITLAB,
                 stdin=email + "\n")
    line = next((line for line in output.splitlines() if line.startswith("SWIRL_JSON")), None)
    if line is None:
        raise RuntimeError("GitLab returned no answer")
    return json.loads(line.removeprefix("SWIRL_JSON"))


def elasticsearch(index, query):
    script = ('curl -sS -u "elastic:$ELASTIC_PASSWORD" -H "Content-Type: application/json" --data-binary @- '
              f'"localhost:9200/{index}/_search?ignore_unavailable=true"')
    response = json.loads(run("kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/elasticsearch", "--",
                              "sh", "-c", script, stdin=json.dumps({"size": LIMIT, "query": query, "sort": [{"@timestamp": "asc"}]})))
    if "error" in response:
        raise RuntimeError(str(response["error"])[:300])
    return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]


def postgres_rows(database, sql, email):
    """Rows as JSON objects from a platform database, the email passed as a psql variable."""
    target = run("bash", "-c", 'source "$1"; postgres_runtime_target infra', "dsr", str(ROOT / "scripts/lib/postgres-access.sh")).strip()
    script = ('IFS= read -r user; IFS= read -r PGPASSWORD; IFS= read -r email; export PGPASSWORD; '
              'exec psql -X -qAt --set=ON_ERROR_STOP=1 -h 127.0.0.1 -U "$user" -d "$1" --set=email="$email"')
    user = run("kubectl", "-n", "infra", "get", "secret", "postgres-secret", "-o", "jsonpath={.data.POSTGRES_USER}")
    password = run("kubectl", "-n", "infra", "get", "secret", "postgres-secret", "-o", "jsonpath={.data.POSTGRES_PASSWORD}")
    payload = "\n".join([base64.b64decode(user).decode(), base64.b64decode(password).decode(), email]) + "\n" + sql + "\n"
    out = run("kubectl", "-n", "infra", "exec", "-i", target, "-c", "postgres", "--", "sh", "-ec", script, "dsr", database, stdin=payload)
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


SQL = {
    "zammad": {
        "users": "SELECT row_to_json(u) FROM users u WHERE lower(u.email) = lower(:'email');",
        "tickets": "SELECT row_to_json(t) FROM tickets t WHERE t.customer_id IN (SELECT id FROM users WHERE lower(email) = lower(:'email')) LIMIT 10000;",
        "messages": "SELECT row_to_json(a) FROM ticket_articles a WHERE a.created_by_id IN (SELECT id FROM users WHERE lower(email) = lower(:'email')) LIMIT 10000;",
    },
    "odoo": {
        "contacts": "SELECT row_to_json(p) FROM res_partner p WHERE lower(p.email) = lower(:'email');",
        "users": "SELECT row_to_json(u) FROM res_users u WHERE u.partner_id IN (SELECT id FROM res_partner WHERE lower(email) = lower(:'email'));",
    },
    "vaultwarden": {
        "account": "SELECT row_to_json(u) FROM (SELECT uuid, email, name, created_at, updated_at FROM users WHERE lower(email) = lower(:'email')) u;",
    },
}


def collect(email, sources=None):
    """{source: data or {"error": text}} for every source; a failing source never stops the others."""
    realm = None
    found = {}

    def attempt(name, function):
        try:
            found[name] = redact(function())
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            found[name] = {"error": str(error)}

    attempt("keycloak", lambda: keycloak(realm or identity()["KEYCLOAK_REALM"], email))
    usernames = [entry["account"].get("username") for entry in found["keycloak"]] if isinstance(found["keycloak"], list) else []
    ids = [entry["account"].get("id") for entry in found["keycloak"]] if isinstance(found["keycloak"], list) else []
    who = [{"terms": {"keycloak.username": usernames}}] if usernames else []
    who += [{"terms": {"keycloak.userId": ids}}] if ids else []
    attempt("identity-events", lambda: elasticsearch("identity-audit-*", {"bool": {"should": who, "minimum_should_match": 1}}) if who else [])
    attempt("access-grants", lambda: elasticsearch("access-grants", {"terms": {"user": usernames}}) if usernames else [])
    attempt("gitlab", lambda: gitlab(email))
    for database, queries in SQL.items():
        for part, sql in queries.items():
            attempt(f"{database}-{part}", lambda database=database, sql=sql: postgres_rows(database, sql, email))
    return found


# What happens to each source on an erasure request: (action, how, and why when kept).
ERASURE = [
    ("gitlab", "erase", "Delete the user in GitLab (Admin, Users, Delete user): their issues, merge requests and comments move"
     " to the Ghost user. Delete the contributions too only where they hold personal data."),
    ("zammad", "erase", "Delete the customer with Zammad's data privacy task (Admin, Data Privacy), which deletes their tickets;"
     " a ticket the company must keep is first cleared of personal data."),
    ("odoo", "anonymize", "Archive the user and anonymize the contact, except where invoices or accounting entries reference it:"
     " those are kept ten years (Code de commerce, art. L123-22; Art. 17(3)(b))."),
    ("vaultwarden", "erase", "The person deletes their own account (Settings, My account); otherwise remove them from the"
     " organizations, then delete the account."),
    ("keycloak", "erase", "Delete the account in the platform realm last (Keycloak console, Users), so it cannot sign in again."),
    ("identity-events", "keep", "Sign-in and administration events are security records kept for legitimate interests"
     " (Art. 17(3)); they expire after 396 days."),
    ("access-grants", "keep", "Access grants are the evidence of who approved which access; kept with the access reviews."),
]


def erasure_plan(email, found, now):
    lines = [f"# Erasure plan for {email}", "", f"Prepared {now:%Y-%m-%d %H:%M} UTC; nothing was changed. Answer due by"
             f" {(now + timedelta(days=30)):%Y-%m-%d} (GDPR Art. 12(3)).", "", "| Step | Source | Records | Action | How |", "|---|---|---|---|---|"]
    for step, (prefix, action, how) in enumerate(ERASURE, 1):
        parts = {source: data for source, data in found.items() if source == prefix or source.startswith(prefix + "-")}
        counts = []
        for source, data in sorted(parts.items()):
            counts.append(f"{source}: not checked" if isinstance(data, dict) and "error" in data else f"{source}: {len(data)}")
        lines.append(f"| {step} | {prefix} | {'; '.join(counts) or 'none'} | {action} | {how} |")
    lines += ["", "Applications keep their own data and answer through their owners. Backups keep copies until they expire;"
              " tell the person, and do not restore their data from a backup after the erasure.", ""]
    return "\n".join(lines)


def configmap_log():
    result = subprocess.run(["kubectl", "--request-timeout=30s", "-n", LOG[0], "get", "configmap", LOG[1], "-o", "json"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        return []
    return json.loads(json.loads(result.stdout).get("data", {}).get(LOG[2]) or "[]")


def save_log(entries):
    document = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": LOG[1], "namespace": LOG[0]},
                "data": {LOG[2]: json.dumps(entries, indent=2)}}
    run("kubectl", "apply", "-f", "-", stdin=json.dumps(document))


def write_export(directory, email, found, reference, now):
    directory.mkdir(parents=True, mode=0o700)
    for source, data in found.items():
        (directory / f"{source}.json").write_text(json.dumps(data, indent=2, default=str, ensure_ascii=False) + "\n")
        os.chmod(directory / f"{source}.json", 0o600)
    lines = [f"# Data held about {email}", "", f"Exported {now:%Y-%m-%d %H:%M} UTC" + (f", request {reference}" if reference else ""),
             f"Answer due by {(now + timedelta(days=30)):%Y-%m-%d} (GDPR Art. 12(3)).", "", "| Source | Records |", "|---|---|"]
    for source, data in found.items():
        count = f"not collected: {data['error']}" if isinstance(data, dict) and "error" in data else str(len(data))
        lines.append(f"| {source} | {count} |")
    lines += ["", "Vaultwarden holds the person's vault encrypted with their master password: they export it themselves",
              "(Tools, Export vault). Applications answer for their own data through their owners."]
    (directory / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    os.chmod(directory / "SUMMARY.md", 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--subject", required=True, help="the person's email address")
    export.add_argument("--output-dir", default=".")
    export.add_argument("--reference", default="", help="the request's ticket or reference")
    erase = commands.add_parser("erase")
    erase.add_argument("--subject", required=True)
    erase.add_argument("--plan", action="store_true", help="print the erasure plan; required, erasure itself is done by hand")
    erase.add_argument("--reference", default="")
    commands.add_parser("list")
    args = parser.parse_args()
    if args.command == "list":
        entries = configmap_log()
        if not entries:
            print("No requests recorded.")
        for entry in entries:
            print(f"{entry['at'][:10]}  {entry['action']:7} {entry.get('reference') or '-':12} due {entry['due']}  "
                  f"{', '.join(entry['collected'])}" + (f"  (not collected: {', '.join(entry['failed'])})" if entry["failed"] else ""))
        return 0
    if not EMAIL.fullmatch(args.subject):
        parser.error("--subject must be an email address")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    found = collect(args.subject.strip())
    if args.command == "erase":
        if not args.plan:
            parser.error("erase needs --plan: it lists the steps, which are done by hand")
        print(erasure_plan(args.subject, found, now), end="")
        try:
            save_log(configmap_log() + [{"at": now.isoformat(), "action": "erase-plan", "reference": args.reference,
                                         "due": f"{now + timedelta(days=30):%Y-%m-%d}", "collected": sorted(found), "failed": []}])
        except RuntimeError as error:
            print(f"[WARN] The request was not recorded: {error}", file=sys.stderr)
        return 0
    directory = Path(args.output_dir) / f"data-subject-{now:%Y%m%d-%H%M%S}"
    write_export(directory, args.subject, found, args.reference, now)
    failed = sorted(source for source, data in found.items() if isinstance(data, dict) and "error" in data)
    entry = {"at": now.isoformat(), "action": "export", "reference": args.reference, "due": f"{now + timedelta(days=30):%Y-%m-%d}",
             # A salted digest lets a repeated request be recognized without keeping the address.
             "subject": hashlib.sha256((identity().get("PUBLIC_DOMAIN", "") + args.subject.lower()).encode()).hexdigest()[:16]
             if "keycloak" not in failed else "",
             "collected": sorted(set(found) - set(failed)), "failed": failed}
    try:
        save_log(configmap_log() + [entry])
    except RuntimeError as error:
        print(f"[WARN] The request was not recorded: {error}", file=sys.stderr)
    print(f"Wrote {directory} ({len(found) - len(failed)} of {len(found)} sources).")
    for source in failed:
        print(f"  not collected: {source}: {found[source]['error']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
