#!/usr/bin/env python3
"""Rotation of Keycloak's realm keys, in the master and platform realms.

  rotate-keycloak-keys.py status
  rotate-keycloak-keys.py rotate [--realm NAME] [--dry-run]
  rotate-keycloak-keys.py retire [--realm NAME] [--after-days 31] [--dry-run]

Keycloak signs tokens with each realm's generated keys: RSA (RS256) for ID and
access tokens, HMAC (HS512) for refresh and offline tokens, AES for its own
cookies and action tokens. rotate adds a new generated key of each kind with a
higher priority, so new tokens use it, and makes the previous ones passive:
they no longer sign, but still verify the tokens already issued, so nobody is
signed out. retire deletes passive keys rotated out more than --after-days ago
(31 by default, beyond the 30-day offline session idle time), after which tokens
they signed stop working. A rotation's date is in the new key's name
(rsa-generated-20261115), so retire counts from the newest rotation of that kind. Keys from other providers (imported, Java keystore)
are listed but never changed. Keycloak is reached through its pod; no Secret
leaves the cluster.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

PROVIDER_TYPE = "org.keycloak.keys.KeyProvider"
GENERATED = {"rsa-generated": {"algorithm": ["RS256"], "keySize": ["2048"]},
             "hmac-generated-hs512": {"algorithm": ["HS512"], "secretSize": ["64"]},
             "aes-generated": {"secretSize": ["16"]}}
DATED = re.compile(r"-(\d{8})$")
KCADM = r'''
set -eu
kc=/opt/keycloak/bin/kcadm.sh
cfg="/tmp/swirl-cloud-keys.$$.cfg"
trap 'rm -f "$cfg"' EXIT
$kc config credentials --config "$cfg" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
$kc "$@" --config "$cfg"
'''


def kcadm(*args, stdin=None):
    command = ["kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-c", KCADM, "kcadm", *args]
    result = subprocess.run(command, input=stdin if stdin is not None else "", capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise SystemExit((result.stderr.strip() or result.stdout.strip() or "kcadm failed").splitlines()[-1])
    return result.stdout


def platform_realm():
    result = subprocess.run(["kubectl", "-n", "infra", "get", "configmap", "swirl-cloud-identity", "-o", "json"],
                            capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)["data"]["KEYCLOAK_REALM"]


def first(config, key, default=""):
    return (config.get(key) or [default])[0]


def kind(component):
    """Which generated kind a key provider is (hmac-generated covers HS256 too), or None."""
    provider = component.get("providerId", "")
    if provider in ("hmac-generated", "hmac-generated-hs512"):
        return "hmac-generated-hs512" if first(component.get("config", {}), "algorithm", "HS256") == "HS512" else None
    return provider if provider in GENERATED else None


def plan_rotation(components, realm_id, today):
    """New providers to add and existing ones to make passive, per generated kind."""
    additions, passive = [], []
    for provider, extra in GENERATED.items():
        current = [c for c in components if kind(c) == provider]
        top = max([int(first(c.get("config", {}), "priority", "0")) for c in current] or [0])
        additions.append({"name": f"{provider}-{today}", "providerId": provider, "providerType": PROVIDER_TYPE, "parentId": realm_id,
                          "config": {"priority": [str(top + 100)], "enabled": ["true"], "active": ["true"], **extra}})
        for component in current:
            if first(component.get("config", {}), "active", "true") == "true":
                passive.append(component)
    return additions, passive


def rotated_on(component):
    match = DATED.search(component.get("name", ""))
    return datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc) if match else None


def plan_retirement(components, now, after_days):
    """Passive generated keys of a kind whose newest rotation is older than the cut-off.

    Every passive key of a kind has been passive at least since the newest active
    key of that kind was added; without a dated active key nothing is retired."""
    cutoff = now - timedelta(days=after_days)
    retire = []
    for provider in GENERATED:
        current = [c for c in components if kind(c) == provider]
        active = [c for c in current if first(c.get("config", {}), "active", "true") == "true"]
        dates = [rotated_on(c) for c in active]
        if not active or None in dates or max(dates) > cutoff:
            continue
        retire += [c for c in current if c not in active]
    return retire


def realm_components(realm):
    realm_id = json.loads(kcadm("get", f"realms/{realm}", "--fields", "id"))["id"]
    components = json.loads(kcadm("get", "components", "-r", realm, "-q", f"type={PROVIDER_TYPE}"))
    return realm_id, components


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("status", "rotate", "retire"))
    parser.add_argument("--realm", action="append", help="only this realm (repeatable; default master and the platform realm)")
    parser.add_argument("--after-days", type=int, default=31)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.after_days < 31:
        parser.error("--after-days must be at least 31, or offline sessions signed with the old key would end early")
    realms = args.realm or ["master", platform_realm()]
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for realm in realms:
        realm_id, components = realm_components(realm)
        if args.command == "status":
            print(f"Realm {realm}:")
            for c in sorted(components, key=lambda c: (c.get("providerId", ""), -int(first(c.get("config", {}), "priority", "0")))):
                config = c.get("config", {})
                state = "active" if first(config, "active", "true") == "true" else "passive"
                managed = "" if kind(c) else "  (not managed here)"
                print(f"  {c.get('name', ''):32} {c.get('providerId', ''):22} priority {first(config, 'priority', '0'):>6}  {state}{managed}")
        elif args.command == "rotate":
            additions, passive = plan_rotation(components, realm_id, now.strftime("%Y%m%d"))
            names = {c.get("name") for c in components}
            if any(component["name"] in names for component in additions):
                raise SystemExit(f"{realm}: keys were already rotated today; retire or wait until tomorrow.")
            for component in additions:
                print(f"{realm}: add {component['name']} (priority {component['config']['priority'][0]})")
                if not args.dry_run:
                    kcadm("create", "components", "-r", realm, "-f", "-", stdin=json.dumps(component))
            for component in passive:
                print(f"{realm}: make {component.get('name', component['id'])} passive")
                if not args.dry_run:
                    config = {**component.get("config", {}), "active": ["false"]}
                    kcadm("update", f"components/{component['id']}", "-r", realm, "-f", "-", stdin=json.dumps({**component, "config": config}))
        else:
            retire = plan_retirement(components, now, args.after_days)
            if not retire:
                print(f"{realm}: no passive key older than {args.after_days} days.")
            for component in retire:
                print(f"{realm}: delete {component.get('name', component['id'])}")
                if not args.dry_run:
                    kcadm("delete", f"components/{component['id']}", "-r", realm)
    if args.command == "rotate" and not args.dry_run and not args.realm:
        record_duty(f"realm keys rotated in {', '.join(realms)}")
    return 0


def record_duty(note):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import duties
    try:
        duties.record("signing-key-rotation", note)
    except (RuntimeError, ValueError) as error:
        print(f"[WARN] Not recorded in the control calendar: {error}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
