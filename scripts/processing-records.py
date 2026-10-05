#!/usr/bin/env python3
"""Draft GDPR Article 30 records of processing from the running platform.

For each application deployed through Argo CD, lists what the platform knows:
environments and namespaces, owner and data-classification labels, public
endpoints (and therefore the edge processor), data services from its Vault
paths, persistent volumes, where it runs, backups and log retention. Platform
activities that hold personal data (identity, source code and CI, the ERP,
logs and audit) are listed too. Purposes, categories of data subjects and
personal data, legal bases, recipients and erasure deadlines are left for the
controller to complete. Read-only; Secret values are never read.
"""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys

# Retention configured by the platform (docs/security.md, backup scripts).
RETENTION = [
    ("Container logs (Elasticsearch kubernetes-logs-*)", "about 1 day"),
    ("Kubernetes API audit log", "30 days"),
    ("Identity events (Keycloak database / identity-audit-*)", "90 days / at least 1 year"),
    ("Local recovery archives on the control plane", "the last 7 nightly archives"),
    ("Off-site recovery archives (restic), when configured", "7 daily, 5 weekly, 12 monthly"),
]


SERVICES = [
    (r"(/database|/postgres|database_|datasource|postgres)", "PostgreSQL (shared server in infra)"),
    (r"(/redis|redis_)", "Redis (shared cache in infra)"),
    (r"(/kafka|kafka_)", "Kafka (shared cluster in infra)"),
    (r"mongo", "MongoDB (shared server in infra)"),
]
PROCESSORS = [
    (r"resend", "Resend (e-mail delivery)"), (r"sendgrid", "SendGrid (e-mail delivery)"),
    (r"mailgun", "Mailgun (e-mail delivery)"), (r"brevo|sendinblue", "Brevo (e-mail delivery)"),
    (r"stripe", "Stripe (payments)"), (r"openrouter", "OpenRouter (AI models)"), (r"openai", "OpenAI (AI models)"),
    (r"anthropic", "Anthropic (AI models)"), (r"twilio", "Twilio (messaging)"), (r"aws_|s3_", "S3-compatible storage"),
]


def kubectl_json(*args):
    result = subprocess.run(["kubectl", "--request-timeout=60s", *args, "-o", "json"], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise SystemExit(result.stderr.strip())
    return json.loads(result.stdout)


def application(app):
    name = app["metadata"]["name"]
    resources = app.get("status", {}).get("resources", [])
    namespaces = sorted({r.get("namespace", "") for r in resources if r.get("namespace")})
    owners, classes, hosts, volumes, services, processors = set(), set(), set(), [], set(), set()
    for resource in resources:
        kind, namespace, rname = resource["kind"], resource.get("namespace", ""), resource["name"]
        if kind in ("Deployment", "StatefulSet", "CronJob", "DaemonSet"):
            labels = kubectl_json("get", kind.lower(), rname, "-n", namespace)["metadata"].get("labels", {})
            owners.add(labels.get("swirl-cloud.io/owner", ""))
            classes.add(labels.get("swirl-cloud.io/data-classification", ""))
        elif kind == "Ingress":
            spec = kubectl_json("get", "ingress", rname, "-n", namespace)["spec"]
            hosts.update(rule["host"] for rule in spec.get("rules", []) if rule.get("host"))
        elif kind == "PersistentVolumeClaim":
            claim = kubectl_json("get", "pvc", rname, "-n", namespace)
            volumes.append(f"{namespace}/{rname} ({claim['spec']['resources']['requests']['storage']}, "
                           f"{claim['spec'].get('storageClassName', 'default')})")
        elif kind == "ExternalSecret":
            # Vault paths and property names (never values) show the data
            # services and external processors an application uses.
            spec = kubectl_json("get", "externalsecret", rname, "-n", namespace)["spec"]
            names = [f"{d['remoteRef']['key']}:{d['remoteRef'].get('property', '')}" for d in spec.get("data", [])]
            names += [d.get("extract", {}).get("key", "") for d in spec.get("dataFrom", [])]
            for text in filter(None, names):
                for pattern, service in SERVICES:
                    if re.search(pattern, text, re.I):
                        services.add(service)
                for pattern, processor in PROCESSORS:
                    if re.search(pattern, text, re.I):
                        processors.add(processor)
    return {"name": name, "namespaces": namespaces, "owners": sorted(o for o in owners if o) or ["not labelled"],
            "classification": sorted(c for c in classes if c) or ["not labelled"], "hosts": sorted(hosts),
            "volumes": volumes, "services": sorted(services), "processors": sorted(processors)}


def render(organization, nodes, apps):
    now = datetime.now(timezone.utc)
    lines = [f"# Records of processing activities (GDPR Article 30): draft", "",
             f"Generated {now:%Y-%m-%d} from the running platform for {organization}. Items in [brackets] must be"
             " completed by the controller; the technical facts below come from the cluster.", "",
             f"Processing runs on: {', '.join(nodes)} [hosting provider and country].", "",
             "## Retention configured by the platform", "", "| Data | Kept |", "|---|---|"]
    lines += [f"| {data} | {kept} |" for data, kept in RETENTION]
    lines += ["", "External processors and data flows: see docs/guides/external-services.md (Cloudflare receives all"
              " public web traffic when the edge is enabled; the off-site backup target receives encrypted archives).", ""]
    platform = [
        ("Identity and single sign-on (Keycloak)", "Staff and application users: usernames, names, e-mail addresses, group"
         " memberships, password hashes, login events with IP addresses."),
        ("Source code, CI and registry (GitLab)", "Developer accounts, commit authorship, merge requests, CI logs."),
        ("Monitoring, logs and audit (Prometheus, Elasticsearch)", "IP addresses and identities in logs and audit events."),
        ("Business management (Odoo, when installed)", "[Customers, suppliers, employees: contacts, invoices, HR records]."),
    ]
    lines += ["## Platform activities", ""]
    for title, data in platform:
        lines += [f"### {title}", "", f"- Personal data held: {data}", "- Purposes: [ ]", "- Legal basis: [ ]",
                  "- Data subjects: [ ]", "- Recipients: [ ]", ""]
    lines += ["## Applications", ""]
    for app in apps:
        lines += [f"### {app['name']}", "",
                  f"- Environments and namespaces: {', '.join(app['namespaces']) or '-'}",
                  f"- Owner: {', '.join(app['owners'])}; data classification: {', '.join(app['classification'])}",
                  f"- Public endpoints: {', '.join(app['hosts']) or 'none'}",
                  f"- Data services: {', '.join(app['services']) or 'none found in its credentials'}",
                  f"- External processors found in its credentials: {', '.join(app['processors']) or 'none'} "
                  "[add processors used without platform credentials]",
                  f"- Persistent volumes: {', '.join(app['volumes']) or 'none'}",
                  "- Purposes: [ ]", "- Categories of data subjects and personal data: [ ]", "- Legal basis: [ ]",
                  "- Recipients and transfers outside the EEA: [ ]", "- Erasure deadlines: [ ]", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    identity = kubectl_json("get", "configmap", "swirl-cloud-identity", "-n", "infra").get("data", {})
    organization = identity.get("ORGANIZATION_NAME") or identity.get("organizationName") or "[organization]"
    nodes = [n["metadata"]["name"] for n in kubectl_json("get", "nodes")["items"]]
    apps = [application(app) for app in kubectl_json("get", "applications.argoproj.io", "-n", "infra")["items"]
            if app["metadata"]["name"] != "swirl-cloud"]
    if args.format == "json":
        print(json.dumps({"organization": organization, "nodes": nodes, "retention": RETENTION, "applications": apps}, indent=2))
    else:
        print(render(organization, nodes, apps))
    return 0


if __name__ == "__main__":
    sys.exit(main())
