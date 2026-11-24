#!/usr/bin/env python3
"""Checkpoints before a risky change (upgrade, migration, module removal).

  checkpoint.py create [--label TEXT] [--hours 72] [--namespace NS ...] [--dump-dir DIR]
  checkpoint.py list
  checkpoint.py show ID
  checkpoint.py delete ID

create takes a Longhorn snapshot of every Longhorn volume claim (all namespaces,
or the ones named), waits until each is ready, and records the Git revision
Argo CD deploys and every Helm release's revision in the ConfigMap
checkpoint-<ID> in infra. A volume snapshot is crash-consistent, which
PostgreSQL, MongoDB, Kafka and Elasticsearch recover from as after a power cut;
snapshots of different volumes are taken one after another, not at one instant.
Checkpoints expire (72 hours by default, at most 14 days): the checkpoint-expiry
CronJob deletes their snapshots and record. Snapshots live on the volumes' own
replicas and count against the node's disk; they are not backups. --dump-dir
also writes a logical dump of the platform PostgreSQL (pg_dumpall, gzip, mode
0600) there first, so one database can be restored without reverting a volume;
the dump is not expired with the checkpoint. Restoring is described in
docs/operations.md#checkpoints.
"""
import argparse
from datetime import datetime, timedelta, timezone
import getpass
import gzip
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

CLASS = "longhorn-snapshot"
LABEL = "swirl-cloud.io/checkpoint"
EXPIRES = "swirl-cloud.io/checkpoint-expires-at"
MAX_HOURS = 14 * 24


def run(*command, stdin=None, check=True):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=300)
    if check and result.returncode:
        raise SystemExit((result.stderr.strip() or result.stdout.strip() or "command failed").splitlines()[-1])
    return result


def kubectl_json(*args):
    return json.loads(run("kubectl", "--request-timeout=60s", *args, "-o", "json").stdout)


def snapshot_name(checkpoint, claim):
    """A DNS label of at most 63 characters, unique per checkpoint and claim."""
    name = f"{checkpoint}-{claim}"
    if len(name) <= 63:
        return name
    return f"{name[:54]}-{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def longhorn_claims(claims, namespaces):
    selected = []
    for claim in claims:
        meta, spec = claim["metadata"], claim.get("spec", {})
        if namespaces and meta["namespace"] not in namespaces:
            continue
        if not (spec.get("storageClassName") or "").startswith("longhorn") or claim.get("status", {}).get("phase") != "Bound":
            continue
        selected.append({"namespace": meta["namespace"], "claim": meta["name"], "storageClass": spec["storageClassName"],
                         "size": claim.get("status", {}).get("capacity", {}).get("storage", "")})
    return sorted(selected, key=lambda item: (item["namespace"], item["claim"]))


def releases():
    if run("sh", "-c", "command -v helm", check=False).returncode:
        return []
    result = run("helm", "list", "--all-namespaces", "--output", "json", check=False)
    if result.returncode:
        return []
    return [{"name": r["name"], "namespace": r["namespace"], "revision": r["revision"], "chart": r["chart"],
             "app_version": r.get("app_version", "")} for r in json.loads(result.stdout or "[]")]


def argocd():
    result = run("kubectl", "--request-timeout=60s", "-n", "infra", "get", "application", "swirl-cloud", "-o", "json", check=False)
    if result.returncode:
        return {}
    app = json.loads(result.stdout)
    source = app.get("spec", {}).get("source", {})
    return {"revision": app.get("status", {}).get("sync", {}).get("revision", ""), "targetRevision": source.get("targetRevision", ""),
            "repoURL": source.get("repoURL", ""), "parameters": source.get("helm", {}).get("parameters", [])}


def snapshot(checkpoint, item, expires):
    return {"apiVersion": "snapshot.storage.k8s.io/v1", "kind": "VolumeSnapshot",
            "metadata": {"name": snapshot_name(checkpoint, item["claim"]), "namespace": item["namespace"],
                         "labels": {LABEL: checkpoint}, "annotations": {EXPIRES: expires}},
            "spec": {"volumeSnapshotClassName": CLASS, "source": {"persistentVolumeClaimName": item["claim"]}}}


def record(checkpoint, expires, data):
    return {"apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": f"checkpoint-{checkpoint}", "namespace": "infra", "labels": {LABEL: checkpoint},
                         "annotations": {EXPIRES: expires}},
            "data": {"checkpoint.json": json.dumps(data, indent=2, sort_keys=True)}}


def wait_ready(items, timeout):
    deadline = time.monotonic() + timeout
    pending = {(item["namespace"], item["snapshot"]) for item in items}
    while pending:
        for namespace, name in sorted(pending):
            status = kubectl_json("-n", namespace, "get", "volumesnapshot", name).get("status", {})
            if status.get("error", {}).get("message"):
                raise SystemExit(f"Snapshot {namespace}/{name} failed: {status['error']['message']}")
            if status.get("readyToUse"):
                pending.discard((namespace, name))
        if pending and time.monotonic() > deadline:
            raise SystemExit("Snapshots not ready in time: " + ", ".join(f"{n}/{s}" for n, s in sorted(pending)))
        if pending:
            time.sleep(3)


def load(checkpoint):
    result = run("kubectl", "--request-timeout=60s", "-n", "infra", "get", "configmap", f"checkpoint-{checkpoint}", "-o", "json", check=False)
    if result.returncode:
        raise SystemExit(f"No checkpoint {checkpoint} (./install.sh --checkpoint list).")
    return json.loads(json.loads(result.stdout)["data"]["checkpoint.json"])


def database_dump(directory, checkpoint):
    """pg_dumpall of the platform PostgreSQL into directory; returns its path and digest."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = run("bash", "-c", 'source "$1"; postgres_runtime_target infra', "checkpoint",
                 str(Path(__file__).resolve().parent / "lib/postgres-access.sh")).stdout.strip()
    path = directory / f"{checkpoint}-postgresql.sql.gz"
    digest = hashlib.sha256()
    process = subprocess.Popen(["kubectl", "-n", "infra", "exec", target, "-c", "postgres", "--", "sh", "-c",
                                'exec pg_dumpall -U "$POSTGRES_USER" --clean --if-exists'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as out:
        for chunk in iter(lambda: process.stdout.read(1 << 20), b""):
            out.write(chunk)
    if process.wait() != 0:
        path.unlink()
        raise SystemExit("pg_dumpall failed: " + process.stderr.read().decode(errors="replace").strip()[-300:])
    with path.open("rb") as written:
        for block in iter(lambda: written.read(1 << 20), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest()}


def create(args, now):
    if not 1 <= args.hours <= MAX_HOURS:
        raise SystemExit(f"--hours must be between 1 and {MAX_HOURS}")
    kubectl_json("get", "volumesnapshotclass", CLASS)
    checkpoint = now.strftime("cp-%Y%m%d-%H%M%S")
    expires = (now + timedelta(hours=args.hours)).isoformat()
    items = longhorn_claims(kubectl_json("get", "pvc", "--all-namespaces")["items"], set(args.namespace or []))
    if not items:
        raise SystemExit("No bound Longhorn volume claims to snapshot.")
    data = {"id": checkpoint, "label": args.label, "created": now.isoformat(), "expires": expires,
            "operator": f"{getpass.getuser()}@{socket.gethostname()}", "argocd": argocd(), "helm": releases(), "snapshots": []}
    for item in items:
        item["snapshot"] = snapshot_name(checkpoint, item["claim"])
        data["snapshots"].append(item)
    if args.dump_dir:
        print("Dumping the platform PostgreSQL...")
        data["database_dump"] = database_dump(Path(args.dump_dir), checkpoint)
    # The record first, so the expiry job also finds snapshots of a checkpoint that failed halfway.
    run("kubectl", "apply", "-f", "-", stdin=json.dumps(record(checkpoint, expires, data)))
    print(f"Checkpoint {checkpoint}: snapshotting {len(items)} volume(s)...")
    for item in items:
        run("kubectl", "apply", "-f", "-", stdin=json.dumps(snapshot(checkpoint, item, expires)))
    wait_ready(data["snapshots"], args.timeout)
    print(f"Checkpoint {checkpoint} is ready; it expires at {expires[:16].replace('T', ' ')} UTC.")
    if data.get("database_dump"):
        print(f"Database dump: {data['database_dump']['path']} (delete it when the change is accepted)")
    if data["argocd"].get("revision"):
        print(f"Deployed revision: {data['argocd']['revision']}")


def delete(checkpoint):
    for namespace in {item["namespace"] for item in load(checkpoint)["snapshots"]}:
        run("kubectl", "-n", namespace, "delete", "volumesnapshot", "-l", f"{LABEL}={checkpoint}", "--ignore-not-found")
    run("kubectl", "-n", "infra", "delete", "configmap", f"checkpoint-{checkpoint}", "--ignore-not-found")
    print(f"Deleted checkpoint {checkpoint}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create")
    make.add_argument("--label", default="", help="what the checkpoint is for, such as the change's ticket")
    make.add_argument("--hours", type=int, default=72, help="how long to keep it (default 72, at most 336)")
    make.add_argument("--namespace", action="append", help="only claims in this namespace (repeatable)")
    make.add_argument("--timeout", type=int, default=900, help="seconds to wait for the snapshots")
    make.add_argument("--dump-dir", help="also write a pg_dumpall of the platform PostgreSQL here, first")
    commands.add_parser("list")
    show = commands.add_parser("show")
    show.add_argument("id")
    remove = commands.add_parser("delete")
    remove.add_argument("id")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.command == "create":
        create(args, now)
    elif args.command == "show":
        print(json.dumps(load(args.id), indent=2, sort_keys=True))
    elif args.command == "delete":
        delete(args.id)
    else:
        maps = kubectl_json("-n", "infra", "get", "configmap", "-l", LABEL)["items"]
        if not maps:
            print("No checkpoints.")
        for item in sorted(maps, key=lambda m: m["metadata"]["name"]):
            data = json.loads(item["data"]["checkpoint.json"])
            print(f"{data['id']}  expires {data['expires'][:16].replace('T', ' ')}  {len(data['snapshots'])} volume(s)  "
                  f"{data['argocd'].get('revision', '')[:12]}  {data['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
