#!/usr/bin/env python3
"""Second factor for platform administrators: optional by default.

  admin-mfa.py status     which platform-admins members have a one-time-password authenticator
  admin-mfa.py require    ask every member without one to set it up at their next sign-in

Any user can add an authenticator app in the Keycloak account console
(Account security -> Signing in); from then on Keycloak's browser flow asks for
a code at every sign-in. `require` adds Keycloak's "Configure OTP" required
action to members who have none, so enrolment happens at their next sign-in; it
does not change the sign-in flow, and members who already use a second factor
are unaffected. Run it again after adding administrators.
"""
import argparse
import json
import subprocess
import sys

SCRIPT = r'''
set -eu
k=/opt/keycloak/bin/kcadm.sh
c="/tmp/swirl-cloud-admin-mfa.$$.cfg"
trap 'rm -f "$c"' EXIT
$k config credentials --config "$c" --server http://localhost:8080/auth --realm master \
  --user "$KC_BOOTSTRAP_ADMIN_USERNAME" --password "$KC_BOOTSTRAP_ADMIN_PASSWORD" >/dev/null 2>&1
group=$($k get groups -r "$1" --config "$c" -q search=platform-admins -q exact=true --fields id --format csv --noquotes | head -n 1)
for user in $($k get "groups/$group/members" -r "$1" --config "$c" -q max=1000 --fields id --format csv --noquotes); do
  name=$($k get "users/$user" -r "$1" --config "$c" --fields username --format csv --noquotes)
  actions=$($k get "users/$user" -r "$1" --config "$c" --fields requiredActions --format csv --noquotes)
  otp=$($k get "users/$user/credentials" -r "$1" --config "$c" --fields type --format csv --noquotes | grep -cx otp || true)
  if [ "$2" = require ] && [ "$otp" = 0 ] && ! echo "$actions" | grep -q CONFIGURE_TOTP; then
    # Only set it when no other required action is pending, so none is dropped.
    if echo "$actions" | grep -q '[A-Za-z]'; then
      actions="OTHER_ACTIONS_PENDING"
    else
      $k update "users/$user" -r "$1" --config "$c" -s 'requiredActions=["CONFIGURE_TOTP"]' >/dev/null
      actions=CONFIGURE_TOTP
    fi
  fi
  echo "$name $otp $actions"
done
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("status", "require"))
    args = parser.parse_args()
    identity = json.loads(subprocess.run(["kubectl", "-n", "infra", "get", "configmap", "swirl-cloud-identity", "-o", "json"],
                                         capture_output=True, text=True, check=True).stdout)["data"]
    realm = identity["KEYCLOAK_REALM"]
    result = subprocess.run(["kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", "deploy/keycloak", "--", "sh", "-s",
                             "--", realm, args.action], input=SCRIPT, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "Keycloak query failed")
    print(f"platform-admins in realm {realm}:")
    for line in result.stdout.splitlines():
        name, otp, *actions = line.split(" ", 2) + [""]
        pending = " ".join(actions)
        state = "second factor set up" if otp != "0" else (
            "must set up a second factor at next sign-in" if "CONFIGURE_TOTP" in pending else
            "no second factor; other required actions pending, add Configure OTP in the console" if "OTHER_ACTIONS" in pending
            else "no second factor")
        print(f"  {name}: {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
