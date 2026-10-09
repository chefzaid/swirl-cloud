#!/usr/bin/env python3
"""Deploy the outage watch (ADR 0030).

A Cloudflare Worker checks the public endpoints every five minutes from outside
the cluster and emails the platform alert recipients through the platform mail
relay when an endpoint goes down or answers again. It watches every Ingress
host of the platform, Odoo and production applications that is not behind
Cloudflare Access (Access answers at the edge, without the origin). The Worker
has no route, so nothing new becomes public.

Needs CLOUDFLARE_API_TOKEN (Workers Scripts: Edit, Access: Apps read) and
platform mail turned on (scripts/configure-platform-mail.py). The relay password
travels only in the Worker's secret binding. Rerun after changing the relay or
adding public hosts. See docs/guides/service-levels.md#outage-watch.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from lib.onboarding_services import ServiceError, Services

WORKER_NAME = "platform-outage-watch"
WORKER_SOURCE = Path(__file__).resolve().parent / "lib" / "outage_watch_worker.js"
COMPATIBILITY_DATE = "2026-10-01"
SCHEDULE = "*/5 * * * *"
WATCHED_NAMESPACES = {"apps", "corp", "infra"}
HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
API = "https://api.cloudflare.com/client/v4"


def kubectl(*args):
    try:
        result = subprocess.run(["kubectl", "--request-timeout=30s", *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        raise ServiceError("A Kubernetes API call failed") from None
    if result.returncode:
        raise ServiceError("A Kubernetes API call failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def cloudflare(token, method, path, body=None, content_type="application/json", missing=False, attempts=3):
    data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
    request = Request(API + path, data=data, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": content_type})
    for attempt in range(1, attempts + 1):
        # Cloudflare's API occasionally answers 5xx or an empty body; retry those.
        try:
            with urlopen(request, timeout=60) as response:
                result = json.loads(response.read())
            break
        except HTTPError as error:
            if missing and error.code == 404:
                return None
            try:
                result = json.loads(error.read())
            except ValueError:
                result = {}
            if error.code < 500 or attempt == attempts:
                break
        except (URLError, OSError, ValueError):
            if attempt == attempts:
                raise ServiceError("Cloudflare could not be reached") from None
        time.sleep(2 * attempt)
    if not result.get("success"):
        codes = ", ".join(str(item.get("code")) for item in result.get("errors", []))
        raise ServiceError(f"Cloudflare refused {method} {path.split('?')[0]} ({codes or 'no details'}); "
                           "the token needs Workers Scripts: Edit and Access: Apps read")
    return result["result"]


def watched_endpoints(ingresses, access_hosts, domain):
    """Origin-reaching hosts of the platform, Odoo and production applications, the apex first."""
    hosts = set()
    for ingress in ingresses:
        if ingress["metadata"].get("namespace") not in WATCHED_NAMESPACES:
            continue
        for rule in ingress.get("spec", {}).get("rules", []):
            host = (rule.get("host") or "").lower()
            if HOST.fullmatch(host) and host not in access_hosts:
                hosts.add(host)
    ordered = sorted(hosts, key=lambda host: (host != domain, host.count("."), host))
    return [f"https://{host}/" for host in ordered]


def relay_settings():
    services = Services(None, kubectl, {"APPLICATION_NAME": "platform", "GITLAB_PROJECT_ID": "outage-watch",
                                        "GITLAB_PROJECT_PATH": "outage-watch"})
    with services._vault() as api:
        smtp, _version = Services._kv_get(api, "infra/smtp")
    if smtp.get("enabled") != "true" or not smtp.get("password") or not smtp.get("alert_to"):
        raise ServiceError("Platform mail is off; turn it on with scripts/configure-platform-mail.py first")
    return smtp


def bindings(smtp, endpoints, domain, drill_until=None, report_id=None):
    """Worker bindings; the relay password is the only secret and only in a secret binding."""
    plain = {"ENDPOINTS": json.dumps(endpoints), "SITE": domain, "SMTP_HOST": smtp["host"], "SMTP_PORT": smtp["port"],
             "SMTP_TLS": smtp["tls"], "SMTP_USERNAME": smtp["username"], "MAIL_FROM": smtp["from_address"],
             "MAIL_FROM_NAME": smtp.get("from_name", ""), "MAIL_TO": smtp["alert_to"]}
    if drill_until:
        plain["DRILL_UNTIL"] = str(drill_until)
    if report_id:
        plain["REPORT_ID"] = report_id
    return ([{"type": "durable_object_namespace", "name": "WATCH", "class_name": "Watch"}]
            + [{"type": "plain_text", "name": name, "text": value} for name, value in plain.items()]
            + [{"type": "secret_text", "name": "SMTP_PASSWORD", "text": smtp["password"]}])


def ensure_workers_subdomain(token, account, slug):
    """Cloudflare accepts Durable Object Workers only on accounts with a workers.dev subdomain."""
    if cloudflare(token, "GET", f"/accounts/{account}/workers/subdomain", missing=True) is not None:
        return
    for name in (slug, f"{slug}-cloud"):
        try:
            cloudflare(token, "PUT", f"/accounts/{account}/workers/subdomain", {"subdomain": name})
            return
        except ServiceError:
            continue
    raise ServiceError("The Cloudflare account has no workers.dev subdomain; open Workers & Pages in the dashboard once")


def deploy(token, account, worker_bindings):
    settings = cloudflare(token, "GET", f"/accounts/{account}/workers/scripts/{WORKER_NAME}/settings", missing=True)
    metadata = {"main_module": "worker.js", "compatibility_date": COMPATIBILITY_DATE, "bindings": worker_bindings}
    # The Durable Object class is created once; later uploads keep its storage.
    if not any(binding.get("type") == "durable_object_namespace" and binding.get("name") == "WATCH"
               for binding in (settings or {}).get("bindings", [])):
        metadata["migrations"] = {"new_tag": "v1", "new_sqlite_classes": ["Watch"]}
    boundary = uuid.uuid4().hex
    parts = [("metadata", "metadata.json", "application/json", json.dumps(metadata).encode()),
             ("worker.js", "worker.js", "application/javascript+module", WORKER_SOURCE.read_bytes())]
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {kind}\r\n\r\n".encode() + content + b"\r\n" for name, filename, kind, content in parts
    ) + f"--{boundary}--\r\n".encode()
    cloudflare(token, "PUT", f"/accounts/{account}/workers/scripts/{WORKER_NAME}", body,
               f"multipart/form-data; boundary={boundary}")
    # Keep the Worker off workers.dev: it has no public address at all.
    cloudflare(token, "POST", f"/accounts/{account}/workers/scripts/{WORKER_NAME}/subdomain",
               {"enabled": False, "previews_enabled": False})
    cloudflare(token, "PUT", f"/accounts/{account}/workers/scripts/{WORKER_NAME}/schedules", [{"cron": SCHEDULE}])
    return "migrations" in metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drill", type=int, metavar="MINUTES",
                        help="Add a simulated endpoint that fails for MINUTES, to prove the down and recovery emails")
    parser.add_argument("--report", action="store_true", help="Email the status of every endpoint after the next check")
    parser.add_argument("--remove", action="store_true", help="Delete the outage watch Worker and its state")
    args = parser.parse_args()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not token or any(character.isspace() for character in token):
        raise ServiceError("Set CLOUDFLARE_API_TOKEN in the environment")
    if args.drill is not None and not 10 <= args.drill <= 120:
        raise ServiceError("--drill takes 10 to 120 minutes: the watch needs two failed checks to report an outage")
    identity = kubectl("get", "configmap", "swirl-cloud-identity", "-n", "infra", "-o", "json").get("data", {})
    domain = identity.get("PLATFORM_DOMAIN", "")
    if not HOST.fullmatch(domain):
        raise ServiceError("The installed identity has no public domain")
    zones = cloudflare(token, "GET", f"/zones?name={quote(domain)}")
    if len(zones) != 1:
        raise ServiceError(f"Cloudflare has no zone for {domain}")
    account = zones[0]["account"]["id"]
    if args.remove:
        cloudflare(token, "DELETE", f"/accounts/{account}/workers/scripts/{WORKER_NAME}?force=true", missing=True)
        print(f"Outage watch {WORKER_NAME} removed")
        return
    # Only whole-host Access applications answer every path at the edge.
    access_hosts = {str(app.get("domain", "")).lower() for app in
                    cloudflare(token, "GET", f"/accounts/{account}/access/apps?per_page=500")
                    if "/" not in str(app.get("domain", ""))}
    endpoints = watched_endpoints(kubectl("get", "ingress", "-A", "-o", "json").get("items", []), access_hosts, domain)
    if not endpoints:
        raise ServiceError("No public Ingress host outside Cloudflare Access to watch")
    smtp = relay_settings()
    ensure_workers_subdomain(token, account, identity.get("ORGANIZATION_SLUG") or domain.split(".")[0])
    drill_until = int((time.time() + args.drill * 60) * 1000) if args.drill else None
    created = deploy(token, account, bindings(smtp, endpoints, domain, drill_until, uuid.uuid4().hex if args.report else None))
    print(f"Outage watch {'created' if created else 'updated'}: {len(endpoints)} endpoints every five minutes, "
          f"emails to {smtp['alert_to'].replace(',', ', ')} through {smtp['host']}")
    for endpoint in endpoints:
        print(f"  {endpoint}")
    if args.drill:
        print(f"Drill: a simulated endpoint fails for {args.drill} minutes; expect a DOWN email within ten minutes "
              "and an UP email after the drill. Rerun without --drill afterwards to remove it.")
    if args.report:
        print("The status of every endpoint is emailed after the next check (within five minutes)")


if __name__ == "__main__":
    try:
        main()
    except ServiceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
