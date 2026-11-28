#!/usr/bin/env python3
"""Notices on the portal for a maintenance window or an incident.

  portal-notice.py add --service GitLab --from 2026-11-20T22:00 --until 2026-11-20T23:00 \\
      --en "Upgrade to GitLab 19.4; GitLab is unavailable." [--fr "Mise à jour vers GitLab 19.4 ; GitLab est indisponible."]
      [--email] [--maintenance]
  portal-notice.py list
  portal-notice.py remove ID

A notice names a service as the portal lists it (or * for the whole platform).
The portal shows it from now until it ends, to the people who can open that
service and to administrators, in English or French by their browser, with the
times in their own time zone; it disappears by itself when it ends. Times
without a zone are UTC. Notices live in the ConfigMap infra/portal-notices
(not in Git), and adding one removes those that have ended. --email also
sends the notice to the people who can open the service (its access group in
portal-access.json, everyone in the realm for an open service or *, and the
platform administrators), in one message with the recipients in Bcc, through
the platform mail relay. --maintenance also records it as a declared maintenance
window (infra/swirl-cloud-maintenance-windows, kept 400 days), which the monthly
service report leaves out of availability.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
import json
from pathlib import Path
import subprocess
import sys
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import platform_mail  # noqa: E402

NAMESPACE, NAME = "infra", "portal-notices"
WINDOWS = "swirl-cloud-maintenance-windows"
KEEP_WINDOWS = timedelta(days=400)
ADMINISTRATORS = "platform-admins"
MEMBERS = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-notice.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
for group in "$@"; do
  [ "$group" = "*" ] && { $kc get users -r "$REALM" --config "$cfg" -q enabled=true -q max=10000 --fields email,enabled; continue; }
  id="$($kc get groups -r "$REALM" --config "$cfg" -q search="$group" -q exact=true --fields id,name --format csv --noquotes \
    | awk -F, -v name="$group" '$2 == name {print $1; exit}')"
  [ -n "$id" ] && $kc get "groups/$id/members" -r "$REALM" --config "$cfg" -q max=10000 --fields email,enabled
done
'''
MAX_TEXT = 300
MAX_DAYS = 31


def kubectl(*args, stdin=None, check=True):
    result = subprocess.run(["kubectl", "--request-timeout=60s", *args], input=stdin, capture_output=True, text=True, timeout=120)
    if check and result.returncode:
        raise SystemExit((result.stderr.strip() or "kubectl failed").splitlines()[-1])
    return result


def load():
    result = kubectl("-n", NAMESPACE, "get", "configmap", NAME, "-o", "json", check=False)
    if result.returncode:
        return []
    return json.loads(json.loads(result.stdout).get("data", {}).get("notices.json", "[]"))


def load_windows():
    result = kubectl("-n", NAMESPACE, "get", "configmap", WINDOWS, "-o", "json", check=False)
    if result.returncode:
        return []
    return json.loads(json.loads(result.stdout).get("data", {}).get("windows.json", "[]"))


def save_windows(windows):
    document = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": WINDOWS, "namespace": NAMESPACE, "labels": {"app.kubernetes.io/part-of": "swirl-cloud"}},
                "data": {"windows.json": json.dumps(windows, ensure_ascii=False, indent=2)}}
    kubectl("apply", "-f", "-", stdin=json.dumps(document))


def save(notices):
    document = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": NAME, "namespace": NAMESPACE, "labels": {"app.kubernetes.io/part-of": "swirl-cloud"}},
                "data": {"notices.json": json.dumps(notices, ensure_ascii=False, indent=2)}}
    kubectl("apply", "-f", "-", stdin=json.dumps(document))


def services():
    """Service names as Homepage lists them, from its services.yaml."""
    result = kubectl("-n", NAMESPACE, "get", "configmap", "homepage-config", "-o", "json", check=False)
    if result.returncode:
        return None
    groups = yaml.safe_load(json.loads(result.stdout).get("data", {}).get("services.yaml", "[]")) or []
    return {name for group in groups for entries in group.values() for entry in entries or [] for name in entry}


def audience(service):
    """The Keycloak groups whose members may open service, '*' meaning everyone."""
    rules = json.loads((ROOT / "k8s/files/portal-access.json").read_text())
    if service == "*" or service in rules.get("open", []):
        return ["*"]
    # A rule names one group, or several (an access group and a profile).
    groups = rules.get("groups", {}).get(service) or []
    return [*([groups] if isinstance(groups, str) else groups), ADMINISTRATORS]


def recipients(groups):
    realm = json.loads(kubectl("-n", NAMESPACE, "get", "configmap", "swirl-cloud-identity", "-o", "json").stdout)["data"]["KEYCLOAK_REALM"]
    script = f"REALM={json.dumps(realm)}\n" + MEMBERS
    out = kubectl("-n", NAMESPACE, "exec", "-i", "deploy/keycloak", "--", "sh", "-s", "--", *groups, stdin=script).stdout
    found = set()
    decoder, position = json.JSONDecoder(), 0
    while position < len(out):
        while position < len(out) and out[position].isspace():
            position += 1
        if position >= len(out):
            break
        block, position = decoder.raw_decode(out, position)
        found |= {user["email"].strip().lower() for user in block if user.get("email") and user.get("enabled", True)}
    return sorted(found)


def mail_settings():
    data = json.loads(kubectl("-n", NAMESPACE, "get", "secret", "platform-mail", "-o", "json").stdout).get("data", {})
    value = {key: base64.b64decode(item).decode() for key, item in data.items()}
    if value.get("PLATFORM_MAIL_ENABLED") != "true":
        raise SystemExit("Platform mail is not configured (docs/guides/platform-mail.md); the notice is on the portal only.")
    relay = {"host": value["PLATFORM_MAIL_HOST"], "port": value["PLATFORM_MAIL_PORT"], "tls": value.get("PLATFORM_MAIL_TLS", "ssl"),
             "username": value["PLATFORM_MAIL_USERNAME"], "sender": value["PLATFORM_MAIL_FROM"],
             "name": value.get("PLATFORM_MAIL_FROM_NAME", "")}
    return relay, value["PLATFORM_MAIL_PASSWORD"]


def message(notice, relay, to):
    service = "the platform" if notice["service"] == "*" else notice["service"]
    when = f"{notice['starts'][:16].replace('T', ' ')} - {notice['ends'][:16].replace('T', ' ')} UTC"
    mail = EmailMessage()
    mail["Subject"] = f"{'Platform' if notice['service'] == '*' else notice['service']}: {notice['en'][:80]}"
    mail["From"] = formataddr((relay["name"], relay["sender"])) if relay["name"] else relay["sender"]
    mail["To"] = relay["sender"]
    mail["Bcc"] = ", ".join(to)
    body = [f"{service}, {when}:", "", notice["en"]]
    if notice.get("fr"):
        body += ["", "---", "", f"{'La plateforme' if notice['service'] == '*' else notice['service']}, {when} :", "", notice["fr"]]
    body += ["", "This notice is also on the portal until it ends."]
    mail.set_content("\n".join(body))
    return mail


def moment(text, label):
    try:
        value = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        raise SystemExit(f"{label} must be a time such as 2026-11-20T22:00 (UTC) or 2026-11-20T23:00+01:00")
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def check_text(label, text):
    text = " ".join(text.split())
    if not 3 <= len(text) <= MAX_TEXT:
        raise SystemExit(f"{label} must hold 3 to {MAX_TEXT} characters")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("--service", required=True, help="the service as the portal names it, or * for the whole platform")
    add.add_argument("--from", dest="starts", required=True)
    add.add_argument("--until", dest="ends", required=True)
    add.add_argument("--en", required=True)
    add.add_argument("--fr", default="")
    add.add_argument("--email", action="store_true", help="also email the people who can open the service")
    add.add_argument("--maintenance", action="store_true", help="record it as a declared maintenance window")
    commands.add_parser("list")
    remove = commands.add_parser("remove")
    remove.add_argument("id")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    notices = load()
    if args.command == "add":
        starts, ends = moment(args.starts, "--from"), moment(args.ends, "--until")
        if ends <= max(starts, now):
            raise SystemExit("--until must be after --from and in the future")
        if ends - now > timedelta(days=MAX_DAYS):
            raise SystemExit(f"a notice may end at most {MAX_DAYS} days from now")
        known = services()
        if args.service != "*" and known is not None and args.service not in known:
            raise SystemExit(f"No service {args.service!r} on the portal; use one of: {', '.join(sorted(known))}, or *")
        notice = {"id": uuid.uuid4().hex[:8], "service": args.service, "starts": starts.isoformat(), "ends": ends.isoformat(),
                  "en": check_text("--en", args.en)}
        if args.fr:
            notice["fr"] = check_text("--fr", args.fr)
        if args.email:
            relay, password = mail_settings()
            to = recipients(audience(args.service))
            if not to:
                raise SystemExit(f"Nobody who can open {args.service} has an email address; the notice was not saved.")
            try:
                platform_mail.send(relay, password, message(notice, relay, to))
            except platform_mail.MailError as error:
                raise SystemExit(f"Not emailed: {error}. The notice was not saved; fix the relay or add it without --email.")
            notice["emailed"] = len(to)
        save([n for n in notices if datetime.fromisoformat(n["ends"]) > now] + [notice])
        if args.maintenance:
            kept = [w for w in load_windows() if datetime.fromisoformat(w["ends"]) > now - KEEP_WINDOWS]
            save_windows(kept + [{key: notice[key] for key in ("id", "service", "starts", "ends", "en")}])
        print(f"Notice {notice['id']} for {args.service} shows until {ends:%Y-%m-%d %H:%M} UTC"
              + (f"; emailed to {notice['emailed']} people." if args.email else "."))
    elif args.command == "remove":
        if not any(n["id"] == args.id for n in notices):
            raise SystemExit(f"No notice {args.id} (portal-notice list).")
        save([n for n in notices if n["id"] != args.id])
        print(f"Removed notice {args.id}.")
    else:
        current = [n for n in notices if datetime.fromisoformat(n["ends"]) > now]
        if not current:
            print("No notices.")
        for n in sorted(current, key=lambda n: n["starts"]):
            print(f"{n['id']}  {n['service']:16} {n['starts'][:16].replace('T', ' ')} - {n['ends'][:16].replace('T', ' ')} UTC  {n['en']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
