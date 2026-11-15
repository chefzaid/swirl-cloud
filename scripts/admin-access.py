#!/usr/bin/env python3
"""Time-limited access: platform administration, service roles and guest accounts.

  admin-access.py grant --user NAME --hours 4 --reason "..." --approved-by NAME
  admin-access.py grant-role --user NAME --group access-gitlab --until 2027-03-31 --reason "..." --approved-by NAME
  admin-access.py guest --user NAME --until 2027-03-31 --sponsor NAME --reason "..."
  admin-access.py revoke --user NAME [--group GROUP | --guest]
  admin-access.py list

grant adds the user to platform-admins in the platform Keycloak realm for at
most 24 hours. grant-role adds them to an access group (ADR 0031) until a date
at most a year away. guest gives an existing account an end date and a sponsor
among the staff: the account is disabled at the end of that day. A grant needs
an approver who is not the user; a guest's sponsor approves it. Every grant is
recorded in the Elasticsearch index access-grants. The security-detection job
ends grants when they expire (within five minutes), records that, and raises
TimedAccessEndingSoon a week before the end of grants longer than a week.
Members added without a grant are permanent and never removed. Keycloak and
Elasticsearch are reached through their pods; no Secret leaves the cluster.
"""
import argparse
from datetime import date, datetime, time, timedelta, timezone
import getpass
import json
import re
import socket
import subprocess
import sys
import uuid

MAX_HOURS = 24
MAX_DAYS = 366
ADMIN_GROUP = "platform-admins"
ACCESS_GROUP = re.compile(r"access-[a-z0-9][a-z0-9-]{0,60}")
KEYCLOAK = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-admin-access.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
user_id="$($kc get users -r "$1" --config "$cfg" -q username="$2" -q exact=true --fields id --format csv --noquotes)"
[ -n "$user_id" ] || { echo "no-user"; exit 0; }
group_id=""
if [ -n "${4:-}" ]; then
  group_id="$($kc get groups -r "$1" --config "$cfg" -q search="$4" -q exact=true --fields id,name --format csv --noquotes \
    | awk -F, -v name="$4" '$2 == name {print $1; exit}')"
  [ -n "$group_id" ] || { echo "no-group"; exit 0; }
fi
case "$3" in
  status) echo "member=$($kc get "users/$user_id/groups" -r "$1" --config "$cfg" --fields name --format csv --noquotes | grep -cx "$4" || true)" ;;
  add) $kc update "users/$user_id/groups/$group_id" -r "$1" --config "$cfg" \
         -s realm="$1" -s userId="$user_id" -s groupId="$group_id" -n >/dev/null; echo added ;;
  remove) $kc delete "users/$user_id/groups/$group_id" -r "$1" --config "$cfg" >/dev/null 2>&1 || true; echo removed ;;
  exists) echo exists ;;
  disable) $kc update "users/$user_id" -r "$1" --config "$cfg" -s enabled=false >/dev/null
           $kc create "users/$user_id/logout" -r "$1" --config "$cfg" -b '{}' >/dev/null 2>&1 || true; echo disabled ;;
esac
'''


def run(*command, stdin=None):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise SystemExit((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1])
    return result.stdout


def keycloak(realm, user, action, group=""):
    return run("kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-s", "--",
               realm, user, action, group, stdin=KEYCLOAK).strip().splitlines()[-1]


def elasticsearch(method, path, body=None):
    script = (f'curl -sS -u "elastic:$ELASTIC_PASSWORD" -X {method} -H "Content-Type: application/json" '
              f'--data-binary @- "localhost:9200/{path}"')
    response = json.loads(run("kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/elasticsearch", "--",
                              "sh", "-c", script, stdin=json.dumps(body or {})))
    if isinstance(response, dict) and response.get("error"):
        raise SystemExit(str(response["error"])[:300])
    return response


def kind_of(grant):
    return grant.get("kind") or "admin"


def group_of(grant):
    return grant.get("group") or ADMIN_GROUP


def active_grants(user):
    return elasticsearch("POST", "access-grants/_search?ignore_unavailable=true", {"size": 100, "query": {"bool": {"filter": [
        {"term": {"user": user}}, {"term": {"state": "active"}}]}}})["hits"]["hits"]


def find_grant(user, kind, group=None):
    return next((hit for hit in active_grants(user) if kind_of(hit["_source"]) == kind
                 and (group is None or group_of(hit["_source"]) == group)), None)


def platform_realm():
    data = json.loads(run("kubectl", "-n", "infra", "get", "configmap", "swirl-cloud-identity", "-o", "json"))["data"]
    return data["KEYCLOAK_REALM"]


def end_of_day(text, today):
    """The end (UTC) of a date at most MAX_DAYS away, as an aware datetime."""
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"--until must be a date such as {today + timedelta(days=90)}")
    if not today <= day <= today + timedelta(days=MAX_DAYS):
        raise SystemExit(f"--until must be between today and {today + timedelta(days=MAX_DAYS)}")
    return datetime.combine(day + timedelta(days=1), time(), timezone.utc)


def check_reason(parser, reason):
    if len(reason.strip()) < 10:
        parser.error("give a reason of at least ten characters (ticket or incident reference)")


def record(now, expires, **fields):
    elasticsearch("PUT", f"access-grants/_doc/{uuid.uuid4()}?refresh=true", {
        "@timestamp": now.isoformat(), "granted_at": now.isoformat(), "expires_at": expires.isoformat(), "state": "active", **fields})


def describe(grant):
    kind = kind_of(grant)
    if kind == "guest":
        return f"guest account, sponsor {grant.get('sponsor', '')}"
    return f"{group_of(grant)} ({'administration' if kind == 'admin' else 'role'})"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant", help="platform administration for up to 24 hours")
    grant.add_argument("--user", required=True)
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--reason", required=True)
    grant.add_argument("--approved-by", required=True)
    role = commands.add_parser("grant-role", help="an access group until a date")
    role.add_argument("--user", required=True)
    role.add_argument("--group", required=True)
    role.add_argument("--until", required=True)
    role.add_argument("--reason", required=True)
    role.add_argument("--approved-by", required=True)
    guest = commands.add_parser("guest", help="an end date and a sponsor for an existing account")
    guest.add_argument("--user", required=True)
    guest.add_argument("--until", required=True)
    guest.add_argument("--sponsor", required=True)
    guest.add_argument("--reason", required=True)
    revoke = commands.add_parser("revoke", help="end a grant now")
    revoke.add_argument("--user", required=True)
    target = revoke.add_mutually_exclusive_group()
    target.add_argument("--group")
    target.add_argument("--guest", action="store_true")
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
        check_reason(parser, args.reason)
        status = keycloak(realm, args.user, "status", ADMIN_GROUP)
        if status == "no-user":
            raise SystemExit(f"No user {args.user} in realm {realm}.")
        existing = find_grant(args.user, "admin")
        if status == "member=1" and not existing:
            raise SystemExit(f"{args.user} is already a permanent platform administrator.")
        if existing:
            raise SystemExit(f"{args.user} already has an active grant; revoke it first to change it.")
        keycloak(realm, args.user, "add", ADMIN_GROUP)
        expires = now + timedelta(hours=args.hours)
        record(now, expires, user=args.user, realm=realm, kind="admin", group=ADMIN_GROUP, reason=args.reason.strip(),
               requested_by=operator, approved_by=args.approved_by)
        print(f"{args.user} is a platform administrator until {expires:%Y-%m-%d %H:%M} UTC (approved by {args.approved_by}).")
    elif args.command == "grant-role":
        if not ACCESS_GROUP.fullmatch(args.group):
            parser.error("--group must be an access group such as access-gitlab; platform administration uses grant")
        if args.approved_by == args.user:
            parser.error("the approver must be someone other than the user")
        check_reason(parser, args.reason)
        expires = end_of_day(args.until, now.date())
        status = keycloak(realm, args.user, "status", args.group)
        if status in ("no-user", "no-group"):
            raise SystemExit(f"No {'user ' + args.user if status == 'no-user' else 'group ' + args.group} in realm {realm}.")
        existing = find_grant(args.user, "role", args.group)
        if status == "member=1" and not existing:
            raise SystemExit(f"{args.user} is already a permanent member of {args.group}.")
        if existing:
            raise SystemExit(f"{args.user} already has an active grant for {args.group}; revoke it first to change it.")
        keycloak(realm, args.user, "add", args.group)
        record(now, expires, user=args.user, realm=realm, kind="role", group=args.group, reason=args.reason.strip(),
               requested_by=operator, approved_by=args.approved_by)
        print(f"{args.user} is in {args.group} until {expires - timedelta(days=1):%Y-%m-%d} (end of day UTC, approved by {args.approved_by}).")
    elif args.command == "guest":
        if args.sponsor == args.user:
            parser.error("the sponsor must be someone other than the guest")
        check_reason(parser, args.reason)
        expires = end_of_day(args.until, now.date())
        for name in (args.user, args.sponsor):
            if keycloak(realm, name, "exists") == "no-user":
                raise SystemExit(f"No user {name} in realm {realm}.")
        if find_grant(args.user, "guest"):
            raise SystemExit(f"{args.user} already has an end date; revoke it first to change it.")
        record(now, expires, user=args.user, realm=realm, kind="guest", sponsor=args.sponsor, reason=args.reason.strip(),
               requested_by=operator, approved_by=args.sponsor)
        print(f"{args.user}'s account ends on {expires - timedelta(days=1):%Y-%m-%d} (end of day UTC), sponsored by {args.sponsor}.")
    elif args.command == "revoke":
        kind = "guest" if args.guest else ("role" if args.group else "admin")
        grant = find_grant(args.user, kind, args.group)
        if not grant:
            raise SystemExit(f"{args.user} has no active {kind} grant; permanent memberships are changed in Keycloak.")
        if kind == "guest":
            keycloak(realm, args.user, "disable")
        else:
            keycloak(realm, args.user, "remove", group_of(grant["_source"]))
        elasticsearch("POST", f"access-grants/_update/{grant['_id']}?refresh=true",
                      {"doc": {"state": "revoked", "ended_at": now.isoformat(), "ended_by": operator}})
        print(f"Ended {args.user}'s {describe(grant['_source'])}.")
    else:
        hits = elasticsearch("POST", "access-grants/_search?ignore_unavailable=true",
                             {"size": 50, "sort": [{"granted_at": "desc"}]}).get("hits", {}).get("hits", [])
        if not hits:
            print("No grants recorded.")
        for hit in hits:
            g = hit["_source"]
            print(f"{g['state']:8} {g['user']:20} {g['granted_at'][:16]} -> {g['expires_at'][:16]}  {describe(g)}, "
                  f"approved by {g['approved_by']}: {g['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
