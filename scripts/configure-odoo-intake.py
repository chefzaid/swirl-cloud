#!/usr/bin/env python3
"""Provision the website's scoped Odoo CRM intake integration.

Commands use the current central-platform kubeconfig. The integration user can
only submit leads through crm.lead/platform_intake_submit, in the main company,
with the application name as UTM source. Its API key crosses process boundaries
only through stdin/stdout and is stored at Vault apps/<application>/odoo.
The website origin is the only page allowed to frame the Live Chat window.

Chat options also dress the chatbot as the website's persona, email new chat
messages to the team, send Odoo's mail through the website's Resend account,
and (with CLOUDFLARE_API_TOKEN) deploy the Cloudflare Email Worker that relays
email replies back into the chats. See docs/guides/odoo-website.md.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lib.onboarding_services import ServiceError, Services

APPLICATION = re.compile(r"[a-z][a-z0-9-]{0,30}\Z")
ORIGIN = re.compile(r"https://[a-z0-9.-]+(:[0-9]{1,5})?\Z")
API_KEY = re.compile(r"[0-9a-f]{40}\Z")
MARKER = "PLATFORM_ODOO_RESULT "
FAILURE = "PLATFORM_ODOO_ERROR "
LOCAL_URL = "http://odoo.corp.svc.cluster.local:8069"
EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\Z")
STEP_TYPES = {"text", "question_email", "free_input_multi", "create_lead"}
WORKER_NAME = "platform-chat-email"
WORKER_SOURCE = Path(__file__).resolve().parent / "lib" / "chat_email_worker.js"
ROUTING_RULE = "Website chat replies to Odoo"

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
env['ir.config_parameter'].sudo().set_param('platform_crm_intake.website_origin', p['websiteOrigin'])
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

# Runs inside `odoo shell`: chat notifications, persona and outgoing mail.
CHAT_PROGRAM = r'''
import json, secrets, sys
def fail(reason):
    print(FAILURE + reason, file=sys.stderr)
    sys.exit(1)
p = json.loads(PARAMS)
params = env['ir.config_parameter'].sudo()
for name in ('reply_secret', 'inbound_secret'):
    if not params.get_param('platform_crm_intake.' + name):
        params.set_param('platform_crm_intake.' + name, secrets.token_hex(32))
params.set_param('platform_crm_intake.chat_from', p['chatFrom'])
params.set_param('platform_crm_intake.chat_notify_emails', ','.join(p['notify']))
params.set_param('platform_crm_intake.chat_reply_senders', ','.join(p['replySenders']))
params.set_param('platform_crm_intake.chat_notify_lang', p['notifyLang'])
params.set_param('platform_crm_intake.chat_timezone', p['timezone'])
# Odoo's own emails need a sender domain; reuse the chat sender's.
domain = p['chatFrom'].partition('@')[2]
Domains = env['mail.alias.domain'].sudo()
alias_domain = Domains.search([('name', '=', domain)], limit=1) or Domains.create({'name': domain})
company = env.ref('base.main_company')
if not company.alias_domain_id:
    company.alias_domain_id = alias_domain
if p.get('smtpPassword'):
    Server = env['ir.mail_server'].sudo()
    server_values = {'name': 'Website mail (Resend)', 'smtp_host': 'smtp.resend.com', 'smtp_port': 465,
                     'smtp_encryption': 'ssl', 'smtp_authentication': 'login', 'smtp_user': 'resend',
                     'smtp_pass': p['smtpPassword'], 'from_filter': p['chatFrom'].partition('@')[2], 'sequence': 10}
    server = Server.with_context(active_test=False).search([('name', '=', server_values['name'])], limit=1)
    if server:
        server.write(dict(server_values, active=True))
    else:
        Server.create(server_values)
persona = p.get('persona')
if persona:
    script = env.ref('im_livechat.im_livechat_channel_data').rule_ids.chatbot_script_id[:1]
    if not script:
        fail('the Live Chat channel has no chatbot to present as the persona')
    steps = script.script_step_ids.sorted('sequence')
    if [step.step_type for step in steps] != [message['type'] for message in persona['messages']]:
        fail('the chatbot steps differ from the persona messages')
    script.write({'title': persona['name'], 'image_1920': persona['avatar']})
    for step, message in zip(steps, persona['messages']):
        step.with_context(lang='en_US').message = message['en']
        step.update_field_translations('message', {'fr_FR': {message['en']: message['fr']}})
env.cr.commit()
print(MARKER + json.dumps({'inboundSecret': params.get_param('platform_crm_intake.inbound_secret'),
                           'baseUrl': params.get_param('web.base.url')}))
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


def parameters(application, website_origin, stored, *, rotate=False, revoke_previous=False):
    if not APPLICATION.fullmatch(application):
        raise ServiceError("Use the website's application name (a DNS label)")
    if not ORIGIN.fullmatch(website_origin):
        raise ServiceError("Use the website's HTTPS origin, such as https://example.com")
    login = f"integration-{application}"
    key = stored.get("apiKey") if stored else None
    if stored and (stored.get("login") != login or not API_KEY.fullmatch(key or "")):
        raise ServiceError("Existing Vault Odoo credentials belong to another integration or are malformed")
    return {"login": login, "source": application, "websiteOrigin": website_origin, "displayName": f"{application} CRM intake",
            "keyName": f"platform {application}", "key": key, "rotate": rotate, "revokePrevious": revoke_previous}


def odoo_program(params, program=PROGRAM):
    return (program.replace("PARAMS", repr(json.dumps(params)), 1)
            .replace("FAILURE + reason", repr(FAILURE) + " + reason", 1).replace("MARKER", repr(MARKER), 1))


def odoo_shell(program):
    output = run(["kubectl", "-n", "corp", "exec", "-i", "deploy/odoo", "-c", "odoo", "--",
                  "odoo", "shell", "--config=/etc/odoo/odoo.conf", "--database=odoo", "--no-http"], program)
    lines = [line for line in output.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ServiceError("Odoo did not report the integration result")
    return json.loads(lines[0][len(MARKER):])


def chat_parameters(args, smtp_password=None):
    """Validate chat options; the persona file's avatar is embedded as base64."""
    addresses = [args.chat_from, *args.notify, *args.reply_sender]
    if not all(EMAIL.fullmatch(address) for address in addresses):
        raise ServiceError("Chat addresses must be plain lowercase email addresses")
    try:
        ZoneInfo(args.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise ServiceError(f"Unknown time zone: {args.timezone}") from None
    if "+" in args.chat_from:
        raise ServiceError("--chat-from must not contain '+': replies use its plus-addresses")
    params = {"chatFrom": args.chat_from, "notify": args.notify, "replySenders": args.reply_sender,
              "notifyLang": args.notify_lang, "timezone": args.timezone, "smtpPassword": smtp_password}
    if args.chat_persona:
        path = Path(args.chat_persona)
        persona = json.loads(path.read_text())
        messages = persona.get("messages")
        if (not isinstance(persona.get("name"), str) or not persona["name"].strip() or not isinstance(messages, list)
                or not all(isinstance(m, dict) and m.get("type") in STEP_TYPES and isinstance(m.get("en"), str)
                           and isinstance(m.get("fr"), str) for m in messages)):
            raise ServiceError("The persona file needs a name and messages with type, en and fr")
        avatar = (path.parent / persona["avatar"]).read_bytes()
        if not avatar.startswith(b"\x89PNG\r\n\x1a\n") or len(avatar) > 1024 * 1024:
            raise ServiceError("The persona avatar must be a PNG of at most 1 MiB")
        params["persona"] = {"name": persona["name"].strip(), "messages": messages,
                             "avatar": base64.b64encode(avatar).decode()}
    return params


def cloudflare(token, method, path, body=None, content_type="application/json"):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    request = Request("https://api.cloudflare.com/client/v4" + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        try:
            result = json.loads(error.read())
        except ValueError:
            result = {}
    except (URLError, OSError, ValueError):
        raise ServiceError("Cloudflare could not be reached") from None
    if not result.get("success"):
        codes = ", ".join(str(item.get("code")) for item in result.get("errors", []))
        raise ServiceError(f"Cloudflare refused {method} {path.split('?')[0]} ({codes or 'no details'}); "
                           "the token needs Workers Scripts: Edit and Email Routing Rules: Edit")
    return result["result"]


def deploy_email_worker(token, chat_from, inbound_url, inbound_secret):
    """Relay chat replies (chat+...@domain) through an Email Worker to Odoo."""
    domain = chat_from.partition("@")[2]
    zones = cloudflare(token, "GET", f"/zones?name={domain}")
    if len(zones) != 1:
        raise ServiceError(f"Cloudflare has no zone for {domain}")
    zone, account = zones[0]["id"], zones[0]["account"]["id"]
    boundary = uuid.uuid4().hex
    metadata = {"main_module": "worker.js", "compatibility_date": "2026-10-01", "bindings": [
        {"type": "plain_text", "name": "ODOO_INBOUND_URL", "text": inbound_url},
        {"type": "secret_text", "name": "INBOUND_SECRET", "text": inbound_secret}]}
    parts = [("metadata", "metadata.json", "application/json", json.dumps(metadata).encode()),
             ("worker.js", "worker.js", "application/javascript+module", WORKER_SOURCE.read_bytes())]
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {kind}\r\n\r\n".encode() + content + b"\r\n" for name, filename, kind, content in parts
    ) + f"--{boundary}--\r\n".encode()
    cloudflare(token, "PUT", f"/accounts/{account}/workers/scripts/{WORKER_NAME}", body,
               f"multipart/form-data; boundary={boundary}")
    rule = {"name": ROUTING_RULE, "enabled": True, "priority": 0,
            "matchers": [{"type": "literal", "field": "to", "value": chat_from}],
            "actions": [{"type": "worker", "value": [WORKER_NAME]}]}
    existing = [item for item in cloudflare(token, "GET", f"/zones/{zone}/email/routing/rules?per_page=50")
                if item.get("name") == ROUTING_RULE]
    if existing:
        cloudflare(token, "PUT", f"/zones/{zone}/email/routing/rules/{existing[0]['id']}", rule)
    else:
        cloudflare(token, "POST", f"/zones/{zone}/email/routing/rules", rule)


def provision(params):
    result = odoo_shell(odoo_program(params))
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
    parser.add_argument("--website-origin", required=True, help="The website's public HTTPS origin, allowed to frame the chat")
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--rotate", action="store_true", help="Issue a new key; the previous key stays valid until --revoke-previous")
    keys.add_argument("--revoke-previous", action="store_true", help="Revoke every key except the one stored in Vault")
    chat = parser.add_argument_group("website chat by email")
    chat.add_argument("--notify", action="append", default=[], help="Email new chat messages to this address (repeatable)")
    chat.add_argument("--reply-sender", action="append", default=[], help="Address allowed to answer chats by email (repeatable)")
    chat.add_argument("--chat-from", help="Sender of chat emails, whose plus-addresses receive replies (default chat@<website domain>)")
    chat.add_argument("--notify-lang", choices=("fr", "en"), default="fr", help="Language of the team's emails")
    chat.add_argument("--timezone", default="UTC", help="Time zone of the times shown in chat emails, such as Europe/Paris")
    chat.add_argument("--chat-persona", help="JSON file with the chatbot persona: name, avatar PNG and messages")
    chat.add_argument("--resend-key-path", help="Vault path whose RESEND_API_KEY Odoo uses to send email")
    chat.add_argument("--email-worker", action="store_true", help="Deploy the Cloudflare Email Worker (needs CLOUDFLARE_API_TOKEN)")
    args = parser.parse_args()
    chat_requested = any((args.notify, args.reply_sender, args.chat_persona, args.resend_key_path, args.email_worker))
    if chat_requested and not args.notify:
        raise ServiceError("Chat options need at least one --notify address")
    args.chat_from = (args.chat_from or "chat@" + args.website_origin.partition("://")[2].split(":")[0]).lower()
    args.notify = [address.lower() for address in args.notify]
    args.reply_sender = [address.lower() for address in (args.reply_sender or args.notify)]
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if args.email_worker and not token:
        raise ServiceError("--email-worker needs CLOUDFLARE_API_TOKEN in the environment")
    if kubectl("-n", "corp", "get", "deployment", "odoo", "-o", "json").get("status", {}).get("readyReplicas") != 1:
        raise ServiceError("Odoo must be installed and Ready")
    path = f"apps/{args.application}/odoo"
    services = Services(None, kubectl, {"APPLICATION_NAME": args.application,
                        "GITLAB_PROJECT_ID": "odoo", "GITLAB_PROJECT_PATH": "odoo-intake"})
    with services._vault() as api:
        stored, version = Services._kv_get(api, path)
        params = parameters(args.application, args.website_origin, stored, rotate=args.rotate, revoke_previous=args.revoke_previous)
        result = provision(params)
        key = result["key"] or params["key"]
        verify(key)
        if result["key"]:
            stored = dict(stored, url=LOCAL_URL, database="odoo", login=params["login"], apiKey=key)
            Services._kv_write(api, path, stored, version)
            stored, version = Services._kv_get(api, path)
        if chat_requested:
            smtp_password = None
            if args.resend_key_path:
                mail, _ = Services._kv_get(api, services._path(args.resend_key_path))
                smtp_password = mail.get("RESEND_API_KEY") or None
                if not smtp_password:
                    raise ServiceError(f"Vault {args.resend_key_path} has no RESEND_API_KEY")
            chat_result = odoo_shell(odoo_program(chat_parameters(args, smtp_password), CHAT_PROGRAM))
            if stored.get("inboundSecret") != chat_result["inboundSecret"]:
                Services._kv_write(api, path, dict(stored, inboundSecret=chat_result["inboundSecret"]), version)
            if args.email_worker:
                deploy_email_worker(token, args.chat_from, chat_result["baseUrl"].rstrip("/")
                                    + "/platform_crm_intake/mail/inbound", chat_result["inboundSecret"])
    action = "issued a new key" if result["key"] else "kept the stored key"
    print(f"Odoo CRM intake for {args.application}: company {result['company']}, "
          f"{action}, revoked {result['revoked']} other key(s). Vault: {path}")
    if chat_requested:
        print(f"Website chat: emails from {args.chat_from} to {', '.join(args.notify)}; replies accepted from "
              f"{', '.join(args.reply_sender)}" + ("; email worker deployed" if args.email_worker else ""))


if __name__ == "__main__":
    try:
        main()
    except (ServiceError, ValueError, KeyError) as error:
        print("ERROR: " + (str(error) if isinstance(error, ServiceError) else "Malformed Odoo integration configuration"), file=sys.stderr)
        sys.exit(1)
