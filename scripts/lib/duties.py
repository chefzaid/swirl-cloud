"""Control calendar (config/control-calendar.yaml): periodic duties, when each was
last done, and which are overdue. Completions live in the ConfigMap
infra/swirl-cloud-duties as {id: {"done": ISO time, "by": who, "note": text}}, so
the status report and the evidence pack read them without root."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIGMAP = ("infra", "swirl-cloud-duties")


def catalog(path=ROOT / "config/control-calendar.yaml"):
    duties = yaml.safe_load(Path(path).read_text())["duties"]
    ids = [duty["id"] for duty in duties]
    if len(ids) != len(set(ids)):
        raise ValueError("control calendar: duplicate duty ids")
    return {duty["id"]: duty for duty in duties}


def kubectl(*args, stdin=None):
    result = subprocess.run(["kubectl", "--request-timeout=20s", *args], input=stdin, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "kubectl failed")
    return result.stdout


def recorded():
    """Completions by duty id; an empty map when nothing was recorded yet."""
    namespace, name = CONFIGMAP
    text = kubectl("-n", namespace, "get", "configmap", name, "--ignore-not-found", "-o", "jsonpath={.data.completions}")
    return json.loads(text) if text.strip() else {}


def record(duty, note="", by=None, when=None):
    """Mark a duty done now; the ConfigMap is created on first use."""
    if duty not in catalog():
        raise ValueError(f"unknown duty {duty!r}; see config/control-calendar.yaml")
    completions = recorded()
    completions[duty] = {"done": (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                         "by": by or os.environ.get("SUDO_USER") or os.environ.get("USER") or "unknown", "note": note[:300]}
    namespace, name = CONFIGMAP
    manifest = {"apiVersion": "v1", "kind": "ConfigMap",
                "metadata": {"name": name, "namespace": namespace, "labels": {"app.kubernetes.io/part-of": "swirl-cloud"}},
                "data": {"completions": json.dumps(completions, sort_keys=True)}}
    kubectl("apply", "-f", "-", stdin=json.dumps(manifest))
    return completions[duty]


def status(duties, completions, now=None):
    """[(duty, last done or None, days overdue or None)]: overdue when never done or past its period."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for duty in duties.values():
        done = completions.get(duty["id"], {}).get("done")
        last = datetime.strptime(done, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if done else None
        due = last + timedelta(days=duty["every_days"]) if last else None
        rows.append((duty, last, None if due and due > now else (now - due).days if due else -1))
    return rows
