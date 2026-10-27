#!/usr/bin/env python3
"""Email the installation report through the platform mail relay (ADR 0029).

Sends the platform alert recipients the verification report the installer just
printed, with a link to every service: the portal first, then each Ingress host
grouped as platform, corporate and applications. Does nothing when platform mail
is off. The relay password is read from Vault infra/smtp and never printed.

  send-install-report.py --report FILE
"""
import argparse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
import json
from pathlib import Path
import re
import subprocess
import sys

from lib.onboarding_services import ServiceError, Services
from lib.platform_mail import MailError, send

HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
GROUPS = (("Platform", lambda namespace: namespace == "infra"), ("Corporate", lambda namespace: namespace == "corp"),
          ("Applications", lambda namespace: namespace == "apps" or namespace.startswith("apps-")
           or namespace.endswith(("-prod", "-uat", "-int"))))


def kubectl(*args):
    try:
        result = subprocess.run(["kubectl", "--request-timeout=30s", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise ServiceError("A Kubernetes API call failed") from None
    if result.returncode:
        raise ServiceError("A Kubernetes API call failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def relay_settings():
    services = Services(None, kubectl, {"APPLICATION_NAME": "platform", "GITLAB_PROJECT_ID": "install-report",
                                        "GITLAB_PROJECT_PATH": "install-report"})
    with services._vault() as api:
        smtp, _version = Services._kv_get(api, "infra/smtp")
    return smtp if smtp.get("enabled") == "true" and smtp.get("password") and smtp.get("alert_to") else None


def service_links(ingresses, domain):
    """(group, name, URL) for every Ingress host, the portal first; other namespaces are left out."""
    links = {}
    for ingress in ingresses:
        meta = ingress["metadata"]
        group = next((name for name, member in GROUPS if member(meta.get("namespace", ""))), None)
        name = (meta.get("annotations") or {}).get("gethomepage.dev/name") or meta["name"]
        for rule in ingress.get("spec", {}).get("rules", []):
            host = (rule.get("host") or "").lower()
            if group and HOST.fullmatch(host):
                links.setdefault(host, (group, name))
    portal = f"cloud.{domain}"
    ordered = sorted(links, key=lambda host: (host != portal, [g for g, _ in GROUPS].index(links[host][0]), host))
    return [(links[host][0], links[host][1], f"https://{host}/") for host in ordered]


def report_message(smtp, identity, report, links):
    summary = next((line for line in reversed(report.splitlines()) if line.startswith(("Summary:", "Bilan :"))), "")
    title = f"{identity.get('ORGANIZATION_NAME', 'Swirl')} Cloud"
    message = EmailMessage()
    message["From"] = formataddr((smtp["from_name"], smtp["from_address"])) if smtp.get("from_name") else smtp["from_address"]
    message["To"] = ", ".join(smtp["alert_to"].split(","))
    message["Subject"] = f"{title} installation report{': ' + summary.split(':', 1)[1].strip() if summary else ''}"
    message["Message-ID"] = make_msgid(domain=smtp["from_address"].partition("@")[2])
    lines = [f"The installation of {title} finished. {summary}".rstrip(), ""]
    for group in [name for name, _ in GROUPS]:
        entries = [(name, url) for linked, name, url in links if linked == group]
        if entries:
            lines += [f"{group}:"] + [f"  {name}: {url}" for name, url in entries] + [""]
    lines += ["Verification report:", "", report.rstrip(), "",
              "Run ./install.sh --status on the control plane for a fresh report."]
    message.set_content("\n".join(lines) + "\n")
    return message


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, required=True, help="verification report text to include")
    args = parser.parse_args()
    try:
        report = re.sub(r"\x1b\[[0-9;]*m", "", args.report.read_text(errors="replace"))
        smtp = relay_settings()
        if smtp is None:
            print("Platform mail is off; the installation report was not emailed.")
            return 0
        identity = kubectl("get", "configmap", "swirl-cloud-identity", "-n", "infra", "-o", "json").get("data", {})
        links = service_links(kubectl("get", "ingresses", "-A", "-o", "json").get("items", []),
                              identity.get("PLATFORM_DOMAIN", ""))
        relay = {"host": smtp["host"], "port": smtp["port"], "tls": smtp["tls"], "username": smtp["username"],
                 "sender": smtp["from_address"]}
        send(relay, smtp["password"], report_message(smtp, identity, report, links))
    except (ServiceError, MailError, OSError, KeyError) as error:
        print(f"[ERROR] The installation report was not emailed: {error}", file=sys.stderr)
        return 1
    print(f"Emailed the installation report to {smtp['alert_to'].replace(',', ', ')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
