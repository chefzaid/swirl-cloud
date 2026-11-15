#!/usr/bin/env python3
"""Notices on the portal for a maintenance window or an incident.

  portal-notice.py add --service GitLab --from 2026-11-20T22:00 --until 2026-11-20T23:00 \\
      --en "Upgrade to GitLab 19.4; GitLab is unavailable." [--fr "Mise à jour vers GitLab 19.4 ; GitLab est indisponible."]
  portal-notice.py list
  portal-notice.py remove ID

A notice names a service as the portal lists it (or * for the whole platform).
The portal shows it from now until it ends, to the people who can open that
service and to administrators, in English or French by their browser, with the
times in their own time zone; it disappears by itself when it ends. Times
without a zone are UTC. Notices live in the ConfigMap infra/portal-notices
(not in Git), and adding one removes those that have ended.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
import uuid

import yaml

NAMESPACE, NAME = "infra", "portal-notices"
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
        save([n for n in notices if datetime.fromisoformat(n["ends"]) > now] + [notice])
        print(f"Notice {notice['id']} for {args.service} shows until {ends:%Y-%m-%d %H:%M} UTC.")
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
