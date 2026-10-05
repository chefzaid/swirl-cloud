#!/usr/bin/env python3
"""Just-in-time platform administration.

  admin-access.py grant --user NAME --hours 4 --reason "..." --approved-by NAME
  admin-access.py revoke --user NAME
  admin-access.py list

A grant adds the user to platform-admins in the platform Keycloak realm for a
limited time, with a reason and an approver who is not the user. Every grant is
recorded in the Elasticsearch index access-grants; the security-detection job
removes the membership when it expires (within five minutes) and records that.
Members added without a grant are permanent and never removed. Keycloak and
Elasticsearch are reached through their pods; no Secret leaves the cluster.
"""
import argparse
from datetime import datetime, timedelta, timezone
import getpass
import json
import socket
import subprocess
import sys
import uuid

MAX_HOURS = 24
KEYCLOAK = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-admin-access.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
user_id="$($kc get users -r "$1" --config "$cfg" -q username="$2" -q exact=true --fields id --format csv --noquotes)"
[ -n "$user_id" ] || { echo "no-user"; exit 0; }
group_id="$($kc get groups -r "$1" --config "$cfg" -q search=platform-admins -q exact=true --fields id --format csv --noquotes | head -n 1)"
member="$($kc get "users/$user_id/groups" -r "$1" --config "$cfg" --fields name --format csv --noquotes | grep -cx platform-admins || true)"
case "$3" in
  status) echo "member=$member" ;;
  add) $kc update "users/$user_id/groups/$group_id" -r "$1" --config "$cfg" \
         -s realm="$1" -s userId="$user_id" -s groupId="$group_id" -n >/dev/null; echo added ;;
  remove) $kc delete "users/$user_id/groups/$group_id" -r "$1" --config "$cfg" >/dev/null 2>&1 || true; echo removed ;;
esac
'''


def run(*command, stdin=None):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise SystemExit((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1])
    return result.stdout


def keycloak(realm, user, action):
    return run("kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-s", "--",
               realm, user, action, stdin=KEYCLOAK).strip().splitlines()[-1]


def elasticsearch(method, path, body=None):
    script = (f'curl -sS -u "elastic:$ELASTIC_PASSWORD" -X {method} -H "Content-Type: application/json" '
              f'--data-binary @- "localhost:9200/{path}"')
    response = json.loads(run("kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/elasticsearch", "--",
                              "sh", "-c", script, stdin=json.dumps(body or {})))
    if isinstance(response, dict) and response.get("error"):
        raise SystemExit(str(response["error"])[:300])
    return response


def active_grant(user):
    hits = elasticsearch("POST", "access-grants/_search?ignore_unavailable=true", {"size": 1, "query": {"bool": {"filter": [
        {"term": {"user": user}}, {"term": {"state": "active"}}]}}})["hits"]["hits"]
    return hits[0] if hits else None


def platform_realm():
    data = json.loads(run("kubectl", "-n", "infra", "get", "configmap", "swirl-cloud-identity", "-o", "json"))["data"]
    return data["KEYCLOAK_REALM"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant")
    grant.add_argument("--user", required=True)
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--reason", required=True)
    grant.add_argument("--approved-by", required=True)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("--user", required=True)
    commands.add_parser("list")
    args = parser.parse_args()
    realm = platform_realm()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    operator = f"{getpass.getuser()}@{socket.gethostname()}"
    if args.command == "grant":
        if not 1 <= args.hours <= MAX_HOURS:
            parser.error(f"--hours must be between 1 and {MAX_HOURS}")
        if args.approved_by == args.user:
            parser.error("the approver must be someone other than the user")
        if len(args.reason.strip()) < 10:
            parser.error("give a reason of at least ten characters (ticket or incident reference)")
        status = keycloak(realm, args.user, "status")
        if status == "no-user":
            raise SystemExit(f"No user {args.user} in realm {realm}.")
        if status == "member=1" and not active_grant(args.user):
            raise SystemExit(f"{args.user} is already a permanent platform administrator.")
        if active_grant(args.user):
            raise SystemExit(f"{args.user} already has an active grant; revoke it first to change it.")
        keycloak(realm, args.user, "add")
        expires = now + timedelta(hours=args.hours)
        elasticsearch("PUT", f"access-grants/_doc/{uuid.uuid4()}?refresh=true", {
            "@timestamp": now.isoformat(), "user": args.user, "realm": realm, "reason": args.reason.strip(),
            "requested_by": operator, "approved_by": args.approved_by, "granted_at": now.isoformat(),
            "expires_at": expires.isoformat(), "state": "active"})
        print(f"{args.user} is a platform administrator until {expires:%Y-%m-%d %H:%M} UTC (approved by {args.approved_by}).")
    elif args.command == "revoke":
        grant = active_grant(args.user)
        if not grant:
            raise SystemExit(f"{args.user} has no active grant; permanent memberships are changed in Keycloak.")
        keycloak(realm, args.user, "remove")
        elasticsearch("POST", f"access-grants/_update/{grant['_id']}?refresh=true",
                      {"doc": {"state": "revoked", "ended_at": now.isoformat(), "ended_by": operator}})
        print(f"Ended {args.user}'s temporary platform administration.")
    else:
        hits = elasticsearch("POST", "access-grants/_search?ignore_unavailable=true",
                             {"size": 50, "sort": [{"granted_at": "desc"}]}).get("hits", {}).get("hits", [])
        if not hits:
            print("No grants recorded.")
        for hit in hits:
            g = hit["_source"]
            print(f"{g['state']:8} {g['user']:20} {g['granted_at'][:16]} -> {g['expires_at'][:16]}  "
                  f"approved by {g['approved_by']}: {g['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
