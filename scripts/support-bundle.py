#!/usr/bin/env python3
"""Collect a private troubleshooting bundle for an installed platform.

Includes the verification report and rerun plan, versions, nodes, pods, recent
events, descriptions and log tails of unhealthy pods, Argo CD and Helm status,
host maintenance units and the setup hub's run logs. Kubernetes Secrets are
never read, and token- or password-like strings are redacted. Review the archive
before sharing it.
"""
import argparse
from datetime import datetime
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "swirl-cloud"
REDACTIONS = [
    (re.compile(r"\b(cfut|cfat|cfk|glpat|glrt|gldt|ghp|github_pat|tskey-api|tskey-auth|hvs|hvb|s\.)[A-Za-z0-9_.-]{8,}"), "[redacted-token]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)(\"?\s*[:=]\s*\"?)(bearer\s+)?[^\s\",}]+"),
     r"\1\2[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted-private-key]"),
]


def redact(text):
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def capture(*command, timeout=60):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=ROOT)
        return f"$ {' '.join(command)}\n{result.stdout}{result.stderr}"
    except (OSError, subprocess.SubprocessError) as error:
        return f"$ {' '.join(command)}\n{error}\n"


def unhealthy_pods():
    try:
        pods = json.loads(subprocess.run(["kubectl", "--request-timeout=20s", "get", "pods", "-A", "-o", "json"],
                                         capture_output=True, text=True, timeout=60).stdout)["items"]
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        return []
    found = []
    for pod in pods:
        statuses = pod["status"].get("containerStatuses", [])
        phase = pod["status"].get("phase")
        if phase != "Succeeded" and not (phase == "Running" and statuses and all(s.get("ready") for s in statuses)):
            found.append((pod["metadata"]["namespace"], pod["metadata"]["name"]))
    return found[:15]


def collect():
    files = {
        "verify.txt": capture(sys.executable, str(ROOT / "scripts/verify-platform.py"), timeout=300),
        "plan.txt": capture(sys.executable, str(ROOT / "scripts/install-plan.py"), timeout=300),
        "versions.txt": "\n".join([capture("git", "-C", str(ROOT), "describe", "--always", "--dirty"),
                                   capture("k3s", "--version"), capture("kubectl", "version"), capture("helm", "version"),
                                   capture("helm", "list", "-A"), capture("uname", "-a"), capture("cat", "/etc/os-release")]),
        "nodes.txt": capture("kubectl", "get", "nodes", "-o", "wide") + capture("kubectl", "describe", "nodes"),
        "pods.txt": capture("kubectl", "get", "pods", "-A", "-o", "wide"),
        "events.txt": capture("kubectl", "get", "events", "-A", "--sort-by=.lastTimestamp"),
        "argocd.txt": capture("kubectl", "get", "applications.argoproj.io", "-A", "-o", "wide"),
        "externalsecrets.txt": capture("kubectl", "get", "externalsecrets.external-secrets.io", "-A"),
        "longhorn.txt": capture("kubectl", "get", "volumes.longhorn.io", "-n", "longhorn-system"),
        "storage.txt": capture("kubectl", "get", "pvc", "-A") + capture("df", "-h"),
        "host-units.txt": capture("systemctl", "list-units", "swirl-cloud-*", "--all", "--no-pager")
                          + capture("systemctl", "list-timers", "swirl-cloud-*", "--all", "--no-pager"),
    }
    for namespace, name in unhealthy_pods():
        files[f"unhealthy/{namespace}_{name}.txt"] = (capture("kubectl", "-n", namespace, "describe", "pod", name)
                                                      + capture("kubectl", "-n", namespace, "logs", name, "--all-containers",
                                                                "--tail=200", "--prefix"))
    logs = sorted((STATE / "logs").glob("*.log"), reverse=True)[:5] if (STATE / "logs").exists() else []
    for log in logs:
        files[f"hub-logs/{log.name}"] = log.read_text(errors="replace")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=STATE / "support")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    path = args.output_dir / f"swirl-cloud-support-{stamp}.tar.gz"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    print("Collecting the support bundle (about a minute)...", file=sys.stderr)
    with os.fdopen(descriptor, "wb") as handle, tarfile.open(fileobj=handle, mode="w:gz") as archive:
        for name, text in collect().items():
            data = redact(text).encode()
            info = tarfile.TarInfo(f"swirl-cloud-support-{stamp}/{name}")
            info.size, info.mode, info.mtime = len(data), 0o600, int(datetime.now().timestamp())
            archive.addfile(info, io.BytesIO(data))
    print(f"{path}\nSecrets are never collected and token-like strings are redacted; review before sharing.")


if __name__ == "__main__":
    main()
