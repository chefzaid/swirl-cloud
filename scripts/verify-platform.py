#!/usr/bin/env python3
"""Bounded, read-only verification of an installed Swirl Cloud platform.

Checks nodes, workloads, ExternalSecrets, Argo CD Applications, Vault seal state,
Longhorn volumes, TLS secrets, public Ingress endpoints and the nightly backup.
Login redirects and 401/403 answers count as reachable: administration hosts are
protected by design. Exits 1 when a check fails (or warns, with --strict).
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
BACKUP_METRICS = Path("/var/lib/node_exporter/textfile_collector/swirl_cloud_unit_swirl_cloud_k3s_backup_service.prom")
BACKUP_UNIT = "swirl-cloud-k3s-backup"
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
YOUNG_POD_SECONDS = 300


def first_object(details):
    """Return (namespace, name) of the first 'namespace/name: ...' detail."""
    match = re.match(r"([a-z0-9-]+)/([A-Za-z0-9.-]+)", details[0]) if details else None
    return match.groups() if match else ("<namespace>", "<name>")


def first_name(details):
    return details[0].split(":")[0].replace("faulted: ", "").replace("degraded: ", "") if details else "<name>"


# The next command to run for a warning or failure; nothing here changes state.
HINTS = {
    "Nodes": lambda d: f"kubectl describe node {first_name(d)}; on that host: sudo journalctl -u k3s -u k3s-agent -n 100",
    "Workloads": lambda d: "kubectl -n {0} describe pod {1}; kubectl -n {0} logs {1} --all-containers --tail=50".format(*first_object(d)),
    "ExternalSecrets": lambda d: "kubectl -n {0} describe externalsecret {1}; see docs/operations.md (ExternalSecret is not Ready)".format(*first_object(d)),
    "Argo CD": lambda d: f"kubectl -n infra get application {first_name(d)} -o yaml | less; see docs/deployment.md (Argo CD operations)",
    "Vault": lambda d: "sudo systemctl start swirl-cloud-vault-unseal.service; kubectl -n infra logs vault-0 -c vault --tail=50",
    "Longhorn": lambda d: f"kubectl -n longhorn-system get volumes.longhorn.io {first_name(d)} -o yaml; open the Longhorn UI",
    "TLS secrets": lambda d: "rerun the installer (Cloudflare origin certificate) or replace the secret; see docs/dns.md",
    "Public endpoints": lambda d: f"curl -sSI https://{first_name(d)}/ ; kubectl get ingress -A | grep {first_name(d)}",
    "Disk headroom": lambda d: "sudo du -xh --max-depth=3 /var/lib /var/backups | sort -h | tail; move old archives "
                               "off the host (pinned recovery images must stay); see docs/operations.md#storage-and-retention",
    "Host firewall": lambda d: "sudo ufw status verbose; rerun scripts/configure-node-security.sh --apply (it verifies UFW)",
    "API audit log": lambda d: "./install.sh > Maintenance > Apply Kubernetes API audit logging (restarts K3s)",
    "Nightly backup": lambda d: "sudo journalctl -u swirl-cloud-k3s-backup.service -n 50; ./install.sh > Maintenance > Run the recovery backup now",
    "Platform mail": lambda d: "set up the relay with scripts/configure-platform-mail.py; see docs/guides/platform-mail.md",
}


class Report:
    def __init__(self):
        self.results = []

    def add(self, status, check, summary, details=()):
        details = list(details)
        hint = HINTS[check](details) if status != PASS and check in HINTS else ""
        self.results.append({"status": status, "check": check, "summary": summary, "details": details, "hint": hint})

    def counts(self):
        return {status: sum(r["status"] == status for r in self.results) for status in (PASS, WARN, FAIL)}

    def text(self):
        lines = [f"Swirl Cloud verification - {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
        for result in self.results:
            lines.append(f"[{result['status']}] {result['check']}: {result['summary']}")
            lines += [f"       {detail}" for detail in result["details"][:12]]
            if len(result["details"]) > 12:
                lines.append(f"       ... and {len(result['details']) - 12} more")
            if result["hint"]:
                lines.append(f"       Next: {result['hint']}")
        counts = self.counts()
        lines += ["", f"Summary: {counts[PASS]} passed, {counts[WARN]} warning(s), {counts[FAIL]} failed"]
        return "\n".join(lines)


def kubectl(*args, timeout=30):
    result = subprocess.run(["kubectl", "--request-timeout=20s", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "kubectl failed")
    return json.loads(result.stdout) if result.stdout.strip().startswith("{") else result.stdout


def items(kind, namespace=None):
    scope = ["-n", namespace] if namespace else ["-A"]
    return kubectl("get", kind, *scope, "-o", "json")["items"]


def age_seconds(timestamp):
    return time.time() - datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def check_nodes(report):
    nodes = items("nodes")
    bad = [n["metadata"]["name"] for n in nodes
           if not any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"].get("conditions", []))]
    report.add(FAIL if bad or not nodes else PASS, "Nodes", f"{len(nodes) - len(bad)}/{len(nodes)} Ready", bad)


def check_pods(report):
    failing, starting = [], []
    for pod in items("pods"):
        phase = pod["status"].get("phase")
        if phase == "Succeeded":
            continue
        statuses = pod["status"].get("containerStatuses", [])
        ready = phase == "Running" and statuses and all(s.get("ready") for s in statuses)
        if ready:
            continue
        name = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"
        reasons = {s.get("state", {}).get("waiting", {}).get("reason") for s in statuses} - {None}
        detail = f"{name}: {phase}{' (' + ', '.join(sorted(reasons)) + ')' if reasons else ''}"
        if pod["metadata"].get("ownerReferences", [{}])[0].get("kind") == "Job" and phase == "Failed":
            continue  # Job retries and history are judged by the Job/CronJob, not each pod.
        (starting if age_seconds(pod["metadata"]["creationTimestamp"]) < YOUNG_POD_SECONDS else failing).append(detail)
    status = FAIL if failing else WARN if starting else PASS
    summary = "all running pods ready" if status == PASS else f"{len(failing)} not ready, {len(starting)} starting"
    report.add(status, "Workloads", summary, failing + starting)


def condition(resource, kind):
    return next((c for c in resource.get("status", {}).get("conditions", []) if c.get("type") == kind), {})


def check_external_secrets(report):
    secrets = items("externalsecrets.external-secrets.io")
    bad = [f"{s['metadata']['namespace']}/{s['metadata']['name']}: {condition(s, 'Ready').get('message', 'not ready')}"
           for s in secrets if condition(s, "Ready").get("status") != "True"]
    report.add(FAIL if bad else PASS, "ExternalSecrets", f"{len(secrets) - len(bad)}/{len(secrets)} Ready", bad)


def check_applications(report):
    applications = items("applications.argoproj.io")
    failing, progressing = [], []
    for app in applications:
        status = app.get("status", {})
        sync, health = status.get("sync", {}).get("status"), status.get("health", {}).get("status")
        revision = (status.get("sync", {}).get("revision") or "")[:8]
        line = f"{app['metadata']['name']}: {sync}/{health} at {revision or 'unknown revision'}"
        if health == "Progressing" and sync == "Synced":
            progressing.append(line)
        elif sync != "Synced" or health != "Healthy":
            failing.append(line)
    status = FAIL if failing else WARN if progressing else PASS
    report.add(status, "Argo CD", f"{len(applications) - len(failing) - len(progressing)}/{len(applications)} Synced and Healthy",
               failing + progressing)


def check_vault(report):
    pods = [p["metadata"]["name"] for p in items("pods", "infra")
            if p["metadata"].get("labels", {}).get("app.kubernetes.io/name") == "vault" and p["status"].get("phase") == "Running"]
    sealed = []
    for pod in pods:
        result = subprocess.run(["kubectl", "--request-timeout=20s", "-n", "infra", "exec", pod, "-c", "vault", "--",
                                 "vault", "status", "-format=json"], capture_output=True, text=True, timeout=30)
        try:
            if json.loads(result.stdout).get("sealed", True):
                sealed.append(pod)
        except ValueError:
            sealed.append(f"{pod}: status unavailable")
    if not pods:
        installed = kubectl("get", "statefulset", "-n", "infra", "-l", "app.kubernetes.io/name=vault", "-o", "json")["items"]
        report.add(FAIL if installed else WARN, "Vault", "no running Vault pod" if installed else "not installed")
        return
    report.add(FAIL if sealed else PASS, "Vault", f"{len(pods) - len(sealed)}/{len(pods)} unsealed", sealed)


def check_longhorn(report):
    try:
        volumes = items("volumes.longhorn.io", "longhorn-system")
    except RuntimeError:
        report.add(WARN, "Longhorn", "volumes not readable (Longhorn not installed?)")
        return
    faulted = [v["metadata"]["name"] for v in volumes if v.get("status", {}).get("robustness") == "faulted"]
    degraded = [v["metadata"]["name"] for v in volumes if v.get("status", {}).get("robustness") == "degraded"]
    status = FAIL if faulted else WARN if degraded else PASS
    report.add(status, "Longhorn", f"{len(volumes)} volumes, {len(degraded)} degraded, {len(faulted)} faulted",
               [f"faulted: {n}" for n in faulted] + [f"degraded: {n}" for n in degraded])


def check_tls_secrets(report):
    expiring, expired, checked = [], [], 0
    for namespace in ("infra", "apps", "corp"):
        try:
            secrets = items("secrets", namespace)
        except RuntimeError:
            continue
        for secret in secrets:
            if secret.get("type") != "kubernetes.io/tls" or "tls.crt" not in secret.get("data", {}):
                continue
            pem = base64.b64decode(secret["data"]["tls.crt"])
            result = subprocess.run(["openssl", "x509", "-noout", "-enddate"], input=pem, capture_output=True, timeout=10)
            match = re.search(rb"notAfter=(.+)", result.stdout)
            if not match:
                continue
            checked += 1
            ends = datetime.strptime(match[1].decode().strip(), "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            days = (ends - datetime.now(timezone.utc)).days
            name = f"{namespace}/{secret['metadata']['name']}: {days} days left"
            if days < 0:
                expired.append(name)
            elif days < 30:
                expiring.append(name)
    status = FAIL if expired else WARN if expiring else PASS
    report.add(status, "TLS secrets", f"{checked} certificates, {len(expiring)} expiring within 30 days, {len(expired)} expired",
               expired + expiring)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def probe(host):
    opener = build_opener(NoRedirect())
    try:
        with opener.open(Request(f"https://{host}/", method="GET", headers={"User-Agent": "swirl-cloud-verify"}), timeout=10) as r:
            code = r.status
    except HTTPError as error:
        code = error.code
    except URLError as error:
        reason = error.reason
        if isinstance(reason, socket.gaierror):
            return WARN, f"{host}: DNS does not resolve yet"
        if isinstance(reason, ssl.SSLError):
            return FAIL, f"{host}: TLS error ({reason.reason or reason})"
        return WARN, f"{host}: {reason}"
    except (TimeoutError, OSError) as error:
        return WARN, f"{host}: {error}"
    if code >= 500:
        return FAIL, f"{host}: HTTP {code}"
    return PASS, f"{host}: HTTP {code}"


def check_ingress(report):
    hosts = sorted({rule["host"] for ingress in items("ingresses.networking.k8s.io")
                    for rule in ingress.get("spec", {}).get("rules", []) if rule.get("host") and "*" not in rule["host"]})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, hosts))
    failing = [line for status, line in results if status == FAIL]
    warning = [line for status, line in results if status == WARN]
    status = FAIL if failing else WARN if warning else PASS
    report.add(status, "Public endpoints", f"{len(hosts) - len(failing) - len(warning)}/{len(hosts)} answer over HTTPS",
               failing + warning)


def check_mail(report):
    """Read only the enabled flag and sender of the platform-mail Secret, never the password."""
    flags = kubectl("get", "secret", "platform-mail", "-n", "infra", "--ignore-not-found", "-o",
                    "jsonpath={.data.PLATFORM_MAIL_ENABLED} {.data.PLATFORM_MAIL_FROM}").split()
    if not flags:
        report.add(WARN, "Platform mail", "not set up: the platform-mail Secret is missing (Vault infra/smtp)")
        return
    enabled, sender = (base64.b64decode(value).decode() for value in (flags + [""])[:2])
    if enabled == "true":
        report.add(PASS, "Platform mail", f"on: alerts, password resets, GitLab and Odoo send as {sender}")
    else:
        report.add(WARN, "Platform mail", "off: alerts reach only GitLab and Keycloak cannot send password reset emails")


def check_disk(report):
    usage = shutil.disk_usage("/var/lib/rancher" if Path("/var/lib/rancher").exists() else "/")
    free, used = usage.free / 1024 ** 3, usage.used / usage.total
    status = FAIL if used >= 0.85 else WARN if free < 30 else PASS
    report.add(status, "Disk headroom", f"{free:.0f} GiB free, {used:.0%} used on the K3s filesystem "
               "(maintenance needs about 30 GiB; kubelet image GC starts at 85 %)")


def check_firewall(report):
    status = subprocess.run(["sudo", "-n", "ufw", "status"], capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL)
    if status.returncode and "password" in status.stderr:
        report.add(WARN, "Host firewall", "unknown (needs passwordless sudo to read UFW)")
        return
    policies = [subprocess.run(["sudo", "-n", tool, "-S", "INPUT"], capture_output=True, text=True, timeout=30,
                               stdin=subprocess.DEVNULL).stdout for tool in ("iptables", "ip6tables")]
    active = "Status: active" in status.stdout and all("-P INPUT DROP" in policy for policy in policies)
    report.add(PASS if active else FAIL, "Host firewall", "UFW active, IPv4 and IPv6 INPUT default-deny" if active else
               "UFW is inactive or INPUT is not default-deny")


def check_audit(report):
    if subprocess.run(["systemctl", "cat", "k3s.service"], capture_output=True).returncode:
        return
    result = subprocess.run([str(ROOT / "scripts/configure-k3s-audit.sh"), "--check"], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=30)
    state = result.stdout.strip()
    report.add(PASS if state == "active" else WARN, "API audit log", {
        "active": "Kubernetes API audit events are being written",
        "pending-restart": "configured; starts at the next K3s restart (./install.sh > Maintenance)",
        "configured": "configured but not writing; check: sudo journalctl -u k3s",
        "missing": "not configured; rerun the installer or scripts/configure-k3s-audit.sh"}.get(state, "unknown (needs passwordless sudo)"))


def check_kubelet_memory(report):
    if subprocess.run(["systemctl", "cat", "k3s.service"], capture_output=True).returncode:
        return
    result = subprocess.run([str(ROOT / "scripts/configure-k3s-kubelet-memory.sh"), "--check"], capture_output=True,
                            text=True, stdin=subprocess.DEVNULL, timeout=30)
    state = result.stdout.strip()
    report.add(PASS if state == "active" else WARN, "Kubelet memory reservation", {
        "active": "memory reserved for the host and K3s; pods are evicted before the node swaps",
        "pending-restart": "configured; applies at the next K3s restart (scripts/configure-k3s-kubelet-memory.sh --restart)",
        "configured": "configured but K3s is not running",
        "missing": "not configured; rerun the installer or scripts/configure-k3s-kubelet-memory.sh"}.get(state, "unknown (needs passwordless sudo)"))


def check_backup(report, max_age_hours):
    if BACKUP_METRICS.exists():
        text = BACKUP_METRICS.read_text()
        success = re.search(r"^swirl_cloud_systemd_unit_last_success_timestamp_seconds\S* (\S+)", text, re.M)
        if success:
            age = (time.time() - float(success[1])) / 3600
            status = PASS if age <= max_age_hours else FAIL
            report.add(status, "Nightly backup", f"last success {age:.1f} hours ago (limit {max_age_hours} h)")
            return
    shown = subprocess.run(["systemctl", "show", f"{BACKUP_UNIT}.service", "-p", "Result"], capture_output=True, text=True)
    timer = subprocess.run(["systemctl", "show", f"{BACKUP_UNIT}.timer", "-p", "LastTriggerUSec", "-p", "LoadState"],
                           capture_output=True, text=True)
    values = dict(line.split("=", 1) for line in (shown.stdout + timer.stdout).splitlines() if "=" in line)
    if values.get("LoadState") != "loaded":
        report.add(WARN, "Nightly backup", "backup timer is not installed on this host")
    elif values.get("Result") != "success":
        report.add(FAIL, "Nightly backup", f"last run result: {values.get('Result') or 'unknown'}")
    else:
        report.add(WARN, "Nightly backup", "no success metric yet; last trigger " +
                   (values.get("LastTriggerUSec") or "never") + ". Check again after the next scheduled run.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable results")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--skip-endpoints", action="store_true", help="skip public HTTPS probes")
    parser.add_argument("--api-dry-run", action="store_true", help="also run validate-repository.sh --live")
    parser.add_argument("--backup-max-age-hours", type=int, default=26)
    args = parser.parse_args()
    report = Report()
    checks = [check_nodes, check_pods, check_external_secrets, check_applications, check_vault, check_longhorn,
              check_tls_secrets, check_mail] + ([] if args.skip_endpoints else [check_ingress])
    for check in checks:
        try:
            check(report)
        except (RuntimeError, subprocess.SubprocessError, KeyError, ValueError) as error:
            report.add(FAIL, check.__name__.removeprefix("check_").replace("_", " ").title(), f"check failed: {error}")
    check_disk(report)
    check_firewall(report)
    check_audit(report)
    check_kubelet_memory(report)
    check_backup(report, args.backup_max_age_hours)
    if args.api_dry_run:
        result = subprocess.run([str(ROOT / "scripts/validate-repository.sh"), "--live"], cwd=ROOT,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        report.add(PASS if result.returncode == 0 else FAIL, "API dry-run",
                   "rendered profiles accepted by the API server" if result.returncode == 0 else "validate-repository.sh --live failed",
                   [] if result.returncode == 0 else (result.stdout + result.stderr).strip().splitlines()[-5:])
    print(json.dumps(report.results, indent=2) if args.json else report.text())
    counts = report.counts()
    return 1 if counts[FAIL] or (args.strict and counts[WARN]) else 0


if __name__ == "__main__":
    sys.exit(main())
