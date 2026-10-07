#!/usr/bin/env python3
"""Provision the website's scoped Odoo CRM intake integration.

Commands use the current central-platform kubeconfig. The integration user can
only submit leads through crm.lead/platform_intake_submit, in the main company,
with the application name as UTM source. Its API key crosses process boundaries
only through stdin/stdout and is stored at Vault apps/<application>/odoo.
See docs/guides/odoo-website.md.
"""
import argparse
import json
import re
import subprocess
import sys

from lib.onboarding_services import ServiceError, Services

APPLICATION = re.compile(r"[a-z][a-z0-9-]{0,30}\Z")
API_KEY = re.compile(r"[0-9a-f]{40}\Z")
MARKER = "PLATFORM_ODOO_RESULT "
FAILURE = "PLATFORM_ODOO_ERROR "
LOCAL_URL = "http://odoo.corp.svc.cluster.local:8069"

# Runs inside `odoo shell`; PARAMS is substituted as a JSON string literal.
PROGRAM = r'''
import json, sys
def fail(reason):
    print(FAILURE + reason, file=sys.stderr)
    sys.exit(1)
p = json.loads(PARAMS)
Users = env['res.users'].sudo().with_context(no_reset_password=True, active_test=False)
group = env.ref('platform_crm_intake.group_crm_intake')
company = env.ref('base.main_company')
Source = env['utm.source'].sudo()
source = Source.search([('name', '=', p['source'])], limit=1) or Source.create({'name': p['source']})
values = {'name': p['displayName'], 'login': p['login'], 'active': True,
          'company_id': company.id, 'company_ids': [(6, 0, [company.id])],
          'group_ids': [(6, 0, [env.ref('base.group_portal').id, group.id])],
          'platform_intake_source_id': source.id}
user = Users.search([('login', '=', p['login'])], limit=1)
if user and group not in user.group_ids:
    fail('the login belongs to a user that is not a CRM intake integration')
if user:
    user.write(values)
else:
    user = Users.create(values)
Keys = env['res.users.apikeys'].sudo()
current = p.get('key')
valid = bool(current) and Keys._check_credentials(scope='rpc', key=current) == user.id
if p['revokePrevious'] and not valid:
    fail('the stored key is not valid; run without --revoke-previous to replace it')
key = None
keep = {current[:8]} if valid else set()
if p['rotate'] or not valid:
    # A system (sudo) caller may issue a non-expiring key; rotation is explicit.
    key = Keys.with_user(user).sudo()._generate(None, p['keyName'], None)
    keep = {key[:8]} | (keep if p['rotate'] else set())
env.cr.execute('SELECT id, index FROM res_users_apikeys WHERE user_id = %s', [user.id])
stale = [row[0] for row in env.cr.fetchall() if row[1] not in keep]
Keys.browse(stale)._remove()
env.cr.commit()
print(MARKER + json.dumps({'userId': user.id, 'company': company.name, 'key': key, 'revoked': len(stale)}))
'''

# Runs inside the Odoo container; the key arrives on stdin.
VERIFY = r'''
import json, sys, urllib.error, urllib.request
key = sys.stdin.readline().strip()
def call(model, method, body):
    request = urllib.request.Request(f"http://127.0.0.1:8069/json/2/{model}/{method}", data=json.dumps(body).encode(),
        headers={"Authorization": "bearer " + key, "X-Odoo-Database": "odoo", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
print(call("res.users", "context_get", {}), call("crm.lead", "search_read", {"domain": [], "fields": ["name"], "limit": 1}))
'''


def run(args, payload=None, *, failure="An Odoo operation failed; check the Odoo pod and its logs"):
    try:
        result = subprocess.run(args, input=payload, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        raise ServiceError(failure) from None
    if result.returncode:
        # stdout may carry a generated key; surface only a recognized reason.
        reason = next((line[len(FAILURE):] for line in result.stderr.splitlines() if line.startswith(FAILURE)), "")
        raise ServiceError(failure + (": " + reason if reason else ""))
    return result.stdout


def kubectl(*args):
    value = run(["kubectl", "--request-timeout=30s", *args], failure="A Kubernetes API call failed")
    return json.loads(value) if value else {}


def parameters(application, stored, *, rotate=False, revoke_previous=False):
    if not APPLICATION.fullmatch(application):
        raise ServiceError("Use the website's application name (a DNS label)")
    login = f"integration-{application}"
    key = stored.get("apiKey") if stored else None
    if stored and (stored.get("login") != login or not API_KEY.fullmatch(key or "")):
        raise ServiceError("Existing Vault Odoo credentials belong to another integration or are malformed")
    return {"login": login, "source": application, "displayName": f"{application} CRM intake",
            "keyName": f"platform {application}", "key": key, "rotate": rotate, "revokePrevious": revoke_previous}


def odoo_program(params):
    return (PROGRAM.replace("PARAMS", repr(json.dumps(params)), 1)
            .replace("FAILURE + reason", repr(FAILURE) + " + reason", 1).replace("MARKER", repr(MARKER), 1))


def provision(params):
    output = run(["kubectl", "-n", "corp", "exec", "-i", "deploy/odoo", "-c", "odoo", "--",
                  "odoo", "shell", "--config=/etc/odoo/odoo.conf", "--database=odoo", "--no-http"],
                 odoo_program(params))
    lines = [line for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ServiceError("Odoo did not report the integration result")
    result = json.loads(lines[0][len(MARKER):])
    if result["key"] is not None and not API_KEY.fullmatch(result["key"]):
        raise ServiceError("Odoo returned a malformed API key")
    return result


def verify(key):
    output = run(["kubectl", "-n", "corp", "exec", "-i", "deploy/odoo", "-c", "odoo", "--", "python3", "-c", VERIFY],
                 key + "\n", failure="The integration key could not be verified")
    if output.split() != ["200", "403"]:
        raise ServiceError("The integration key must authenticate and must not read CRM records")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", required=True, help="The website's application name, as in its Vault apps/<name>/ paths")
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--rotate", action="store_true", help="Issue a new key; the previous key stays valid until --revoke-previous")
    keys.add_argument("--revoke-previous", action="store_true", help="Revoke every key except the one stored in Vault")
    args = parser.parse_args()
    if kubectl("-n", "corp", "get", "deployment", "odoo", "-o", "json").get("status", {}).get("readyReplicas") != 1:
        raise ServiceError("Odoo must be installed and Ready")
    path = f"apps/{args.application}/odoo"
    services = Services(None, kubectl, {"APPLICATION_NAME": args.application,
                        "GITLAB_PROJECT_ID": "odoo", "GITLAB_PROJECT_PATH": "odoo-intake"})
    with services._vault() as api:
        stored, version = Services._kv_get(api, path)
        params = parameters(args.application, stored, rotate=args.rotate, revoke_previous=args.revoke_previous)
        result = provision(params)
        key = result["key"] or params["key"]
        verify(key)
        if result["key"]:
            Services._kv_write(api, path, {"url": LOCAL_URL, "database": "odoo", "login": params["login"], "apiKey": key}, version)
    action = "issued a new key" if result["key"] else "kept the stored key"
    print(f"Odoo CRM intake for {args.application}: company {result['company']}, "
          f"{action}, revoked {result['revoked']} other key(s). Vault: {path}")


if __name__ == "__main__":
    try:
        main()
    except (ServiceError, ValueError, KeyError) as error:
        print("ERROR: " + (str(error) if isinstance(error, ServiceError) else "Malformed Odoo integration configuration"), file=sys.stderr)
        sys.exit(1)
