#!/usr/bin/env python3
"""Configure the platform mail relay (ADR 0029).

Checks the relay's TLS certificate, login and sender, stores the settings in
Vault infra/smtp, refreshes the platform-mail ExternalSecrets, restarts
Alertmanager and reruns the Keycloak realm reconciliation through Argo CD.
GitLab and Odoo read the settings when they start: --restart-services restarts
them when the settings changed. The password comes from stdin or a protected
file, never from the command line, and is never printed.
See docs/guides/platform-mail.md.
"""
import argparse
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from lib.onboarding_services import ServiceError, Services
from lib.platform_mail import MailError, check, default_port, send

VAULT_PATH = "infra/smtp"
HOST = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
EMAIL = re.compile(r"[a-z0-9._%+-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
USERNAME = re.compile(r"[^\s\x00-\x1f\x7f]{1,256}\Z")
EXTERNAL_SECRETS = (("infra", "platform-mail"), ("infra", "alertmanager-config"), ("corp", "platform-mail"))
RESTARTED_ON_REQUEST = (("infra", "gitlab", 1800), ("corp", "odoo", 900))
# Validates the rendered file in the running Alertmanager before it restarts;
# the configuration arrives on stdin and only pass or fail comes back.
AMTOOL_CHECK = ('umask 077; f=/alertmanager/.config-check.yml; cat > "$f"; '
                'amtool check-config "$f" >/dev/null 2>&1; status=$?; rm -f "$f"; exit $status')


def run(args, payload=None, *, failure="A Kubernetes operation failed", timeout=120):
    try:
        result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout,
                                text=not isinstance(payload, bytes))
    except (OSError, subprocess.SubprocessError):
        raise ServiceError(failure) from None
    if result.returncode:
        raise ServiceError(failure)
    return result.stdout


def kubectl(*args, optional=False):
    """Run kubectl and parse JSON output; optional reads return {} for a missing object."""
    extra = ["--ignore-not-found"] if optional else []
    output = run(["kubectl", "--request-timeout=30s", *args, *extra], failure="A Kubernetes API call failed")
    return json.loads(output) if output.strip() else {}


def read_password(args):
    if args.password_file:
        path = Path(args.password_file)
        try:
            if path.stat().st_mode & 0o077:
                raise ServiceError("The password file must not be readable by group or others (chmod 600)")
            value = path.read_text()
        except OSError:
            raise ServiceError("The password file could not be read") from None
    else:
        value = sys.stdin.readline()
    value = value.rstrip("\r\n")
    if not value or len(value) > 512 or any(character in value for character in "\r\n\x00"):
        raise ServiceError("The relay password must be one non-empty line of at most 512 characters")
    return value


def settings_from(args, identity, administrator_email):
    """Validate the relay settings; missing sender values default from the installation identity."""
    host = (args.host or "").strip().lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST.fullmatch(host):
            raise ServiceError("--host must be the relay's DNS name or IP address") from None
    port = args.port or default_port(args.tls)
    if not 1 <= port <= 65535:
        raise ServiceError("--port must be between 1 and 65535")
    if not USERNAME.fullmatch(args.username or ""):
        raise ServiceError("--username must be one word without spaces or control characters")
    domain = identity.get("PLATFORM_DOMAIN", "")
    sender = (args.sender or (f"cloud@{domain}" if domain else "")).strip().lower()
    if not EMAIL.fullmatch(sender):
        raise ServiceError("--from must be a plain email address")
    name = args.from_name
    if name is None:
        name = f"{identity['ORGANIZATION_NAME']} Cloud" if identity.get("ORGANIZATION_NAME") else ""
    name = name.strip()
    # The name lands in mail headers, JSON and YAML: printable and unquoted.
    if len(name) > 100 or not name.isprintable() or any(character in name for character in '"<>\\'):
        raise ServiceError("--from-name must be printable, at most 100 characters, without quotes, < > or backslashes")
    recipients = [value.strip().lower() for value in (args.alert_to or administrator_email or "").split(",") if value.strip()]
    if not recipients or len(recipients) > 10 or not all(EMAIL.fullmatch(value) for value in recipients):
        raise ServiceError("--alert-to must list one to ten email addresses, separated by commas")
    return {"enabled": "true", "host": host, "port": str(port), "tls": args.tls, "username": args.username,
            "from_address": sender, "from_name": name, "alert_to": ",".join(recipients)}


def relay(settings):
    return {"host": settings["host"], "port": settings["port"], "tls": settings["tls"],
            "username": settings["username"], "sender": settings["from_address"]}


def sender_header(settings):
    return formataddr((settings["from_name"], settings["from_address"])) if settings["from_name"] else settings["from_address"]


def describe(settings):
    mode = "implicit TLS" if settings["tls"] == "ssl" else "STARTTLS"
    return (f"{settings['host']}:{settings['port']} ({mode}) as {sender_header(settings)}; "
            f"alerts to {settings['alert_to'].replace(',', ', ')}")


def test_message(settings, recipient):
    message = EmailMessage()
    message["From"] = sender_header(settings)
    message["To"] = recipient
    message["Subject"] = f"{settings['from_name'] or 'Platform'} mail test"
    message["Message-ID"] = make_msgid(domain=settings["from_address"].partition("@")[2])
    message.set_content(
        "This message confirms that the platform can send email through its mail relay.\n\n"
        "Alert notifications, password reset emails, GitLab notifications and Odoo use the same relay.\n")
    return message


def platform_services():
    return Services(None, kubectl, {"APPLICATION_NAME": "platform", "GITLAB_PROJECT_ID": "platform-mail",
                                    "GITLAB_PROJECT_PATH": "platform-mail"})


def administrator_email():
    with platform_services()._vault() as api:
        keycloak, _version = Services._kv_get(api, "infra/keycloak")
    return keycloak.get("sso_primary_email", "")


def store(settings, password):
    """Store the settings, or turn mail off with settings None; returns whether Vault changed."""
    with platform_services()._vault() as api:
        stored, version = Services._kv_get(api, VAULT_PATH)
        if settings is None:
            if not stored:
                raise ServiceError("Vault has no platform mail settings to turn off")
            data = dict(stored, enabled="false", password="")
        else:
            data = dict(settings, password=password)
        if stored == data:
            return False
        Services._kv_write(api, VAULT_PATH, data, version)
        return True


def refresh_external_secrets():
    """Ask External Secrets to read Vault now and wait until each Secret is current."""
    present = [(namespace, name) for namespace, name in EXTERNAL_SECRETS
               if kubectl("get", "externalsecret", name, "-n", namespace, "-o", "json", optional=True)]
    requested = datetime.now(timezone.utc).replace(microsecond=0)
    for namespace, name in present:
        kubectl("annotate", "externalsecret", name, "-n", namespace, "--overwrite",
                f"force-sync={int(requested.timestamp())}", "-o", "json")
    deadline = time.monotonic() + 120
    for namespace, name in present:
        while True:
            status = kubectl("get", "externalsecret", name, "-n", namespace, "-o", "json").get("status", {})
            ready = any(item.get("type") == "Ready" and item.get("status") == "True" for item in status.get("conditions", []))
            refreshed = status.get("refreshTime", "")
            if ready and refreshed and datetime.fromisoformat(refreshed.replace("Z", "+00:00")) >= requested:
                break
            if time.monotonic() > deadline:
                raise ServiceError(f"ExternalSecret {namespace}/{name} did not refresh from Vault; check External Secrets")
            time.sleep(3)
    return present


def restart_alertmanager():
    secret = kubectl("get", "secret", "alertmanager-config", "-n", "infra", "-o", "json", optional=True)
    if not secret or not kubectl("get", "deployment", "alertmanager", "-n", "infra", "-o", "json", optional=True):
        return False
    try:
        config = base64.b64decode(secret["data"]["alertmanager.yml"], validate=True)
    except (KeyError, TypeError, ValueError):
        raise ServiceError("The rendered Alertmanager configuration is missing") from None
    run(["kubectl", "-n", "infra", "exec", "-i", "deploy/alertmanager", "-c", "alertmanager", "--", "sh", "-c", AMTOOL_CHECK],
        config, failure="The rendered Alertmanager configuration is invalid; Alertmanager keeps its current one")
    run(["kubectl", "-n", "infra", "rollout", "restart", "deployment/alertmanager"], failure="Alertmanager could not be restarted")
    run(["kubectl", "-n", "infra", "rollout", "status", "deployment/alertmanager", "--timeout=180s"],
        failure="The restarted Alertmanager did not become ready; the previous pod keeps running", timeout=240)
    return True


def rerun_realm_reconciliation():
    """Sync the root Application so its PostSync Keycloak reconciliation applies the settings."""
    # A first installation configures mail before Argo CD exists; its first sync runs
    # the reconciliation anyway.
    if not kubectl("get", "crd", "applications.argoproj.io", "-o", "json", optional=True):
        return False
    name = "swirl-cloud"
    deadline = time.monotonic() + 600
    while True:
        application = kubectl("get", "application", name, "-n", "infra", "-o", "json", optional=True)
        if not application:
            return False
        phase = application.get("status", {}).get("operationState", {}).get("phase")
        if not application.get("operation") and phase not in ("Running", "Terminating"):
            break
        if time.monotonic() > deadline:
            raise ServiceError("Argo CD is still syncing; rerun with --refresh when the current sync ends")
        time.sleep(5)
    started = datetime.now(timezone.utc).replace(microsecond=0)
    operation = {"operation": {"initiatedBy": {"username": "configure-platform-mail"},
                               "sync": {"syncStrategy": {"hook": {}}}}}
    kubectl("patch", "application", name, "-n", "infra", "--type", "merge", "-p", json.dumps(operation), "-o", "json")
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        time.sleep(10)
        state = kubectl("get", "application", name, "-n", "infra", "-o", "json").get("status", {}).get("operationState", {})
        started_at = state.get("startedAt", "")
        if not started_at or datetime.fromisoformat(started_at.replace("Z", "+00:00")) < started:
            continue
        if state.get("phase") == "Succeeded":
            return True
        if state.get("phase") in ("Failed", "Error"):
            raise ServiceError("The Argo CD sync failed; check the keycloak-sso-reconcile hook of the swirl-cloud Application")
    raise ServiceError("The Argo CD sync did not finish within 20 minutes")


def restart_services(restart):
    """Restart GitLab and Odoo when asked; returns the deployments that read mail settings at start."""
    names = []
    for namespace, name, timeout in RESTARTED_ON_REQUEST:
        if not kubectl("get", "deployment", name, "-n", namespace, "-o", "json", optional=True):
            continue
        names.append((namespace, name))
        if restart:
            run(["kubectl", "-n", namespace, "rollout", "restart", f"deployment/{name}"],
                failure=f"{namespace}/{name} could not be restarted")
            run(["kubectl", "-n", namespace, "rollout", "status", f"deployment/{name}", f"--timeout={timeout}s"],
                failure=f"{namespace}/{name} did not become ready after its restart", timeout=timeout + 60)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", help="Relay host name, such as smtp.example.com")
    parser.add_argument("--port", type=int, help="Relay port (default 465 with --tls ssl, 587 with starttls)")
    parser.add_argument("--tls", choices=("ssl", "starttls"), default="ssl", help="Implicit TLS (ssl) or STARTTLS")
    parser.add_argument("--username", help="Relay login")
    secret = parser.add_mutually_exclusive_group()
    secret.add_argument("--password-stdin", action="store_true", help="Read the relay password from the first line of stdin")
    secret.add_argument("--password-file", help="Read the relay password from a file only its owner can read")
    parser.add_argument("--from", dest="sender", help="Sender address (default cloud@<platform domain>)")
    parser.add_argument("--from-name", help="Sender display name (default '<organization> Cloud')")
    parser.add_argument("--alert-to", help="Alert recipients, comma-separated (default: the platform administrator's email)")
    parser.add_argument("--test-to", help="Also send a test message to this address")
    parser.add_argument("--restart-services", action="store_true",
                        help="Restart GitLab and Odoo when the settings changed (GitLab is unavailable for minutes)")
    parser.add_argument("--refresh", action="store_true",
                        help="Refresh the services even when Vault is unchanged, for example after an interrupted run")
    parser.add_argument("--disable", action="store_true", help="Turn platform mail off and remove the stored password")
    args = parser.parse_args()

    settings = password = None
    test_to = (args.test_to or "").strip().lower()
    if args.disable:
        if any((args.host, args.username, args.password_stdin, args.password_file, args.test_to)):
            raise ServiceError("--disable takes no relay settings")
    else:
        if not (args.password_stdin or args.password_file):
            raise ServiceError("Give the relay password with --password-stdin or --password-file")
        if args.test_to and not EMAIL.fullmatch(test_to):
            raise ServiceError("--test-to must be a plain email address")
        password = read_password(args)
        identity = kubectl("get", "configmap", "swirl-cloud-identity", "-n", "infra", "-o", "json", optional=True).get("data", {})
        settings = settings_from(args, identity, "" if args.alert_to else administrator_email())
        try:
            check(relay(settings), password)
        except MailError as error:
            raise ServiceError(f"Nothing was stored: {error}") from None
        print(f"Relay accepted the login and sender: {describe(settings)}")

    changed = store(settings, password)
    print(f"Vault {VAULT_PATH}: " + ("updated" if changed else "unchanged"))
    if test_to:
        try:
            send(relay(settings), password, test_message(settings, test_to))
        except MailError as error:
            raise ServiceError(f"The settings are stored, but the test message failed: {error}") from None
        print(f"Test message sent to {test_to}")
    password = None
    if not (changed or args.refresh):
        return
    refreshed = refresh_external_secrets()
    if refreshed:
        print("Refreshed from Vault: " + ", ".join(f"{namespace}/{name}" for namespace, name in refreshed))
    if restart_alertmanager():
        print("Alertmanager restarted " + ("with the email route" if settings else "without email"))
    if rerun_realm_reconciliation():
        print("Keycloak realm mail settings reconciled through an Argo CD sync")
    pending = restart_services(args.restart_services)
    if pending and args.restart_services:
        print("Restarted: " + ", ".join(f"{namespace}/{name}" for namespace, name in pending))
    elif pending:
        print("GitLab and Odoo read mail settings when they start. Restart them in a quiet period:")
        for namespace, name in pending:
            print(f"  kubectl -n {namespace} rollout restart deployment/{name}")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except ServiceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
