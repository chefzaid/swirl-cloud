#!/usr/bin/env python3
"""Asset inventory of an installed Swirl Cloud platform (NIS2 Art. 21(2)(i) evidence).

Lists servers (hardware, OS, location, owner), workloads with their images, owner,
data classification and criticality, data stores (persistent volumes) and the
outside services in docs/guides/external-services.md. Staff devices are not
inventoried until an endpoint agent exists (TODO §37). Read-only: it reads node,
workload and volume metadata and never reads Secrets.

Owner, data classification and criticality come from the labels
swirl-cloud.io/owner, swirl-cloud.io/data-classification and
swirl-cloud.io/criticality. Without a label, platform namespaces belong to
"platform" and criticality follows the environment (marked "derived").
"""
import argparse
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_SERVICES = ROOT / "docs/guides/external-services.md"
OWNER, DATA_CLASS, CRITICALITY = "swirl-cloud.io/owner", "swirl-cloud.io/data-classification", "swirl-cloud.io/criticality"
PLATFORM_NAMESPACES = {"infra", "kube-system", "longhorn-system", "host-agents", "gitlab-runners", "cnpg-system"}
WORKLOAD_KINDS = "deployments,statefulsets,daemonsets,cronjobs"
COLUMNS = {
    "servers": ["name", "roles", "cpu", "memory_gib", "architecture", "os", "kernel", "kubelet", "internal_ip",
                "region", "zone", "owner"],
    "workloads": ["namespace", "kind", "name", "images", "owner", "data_classification", "criticality"],
    "data_stores": ["namespace", "name", "capacity", "storage_class", "access_modes", "status", "owner",
                    "data_classification"],
    "external_services": ["category", "service", "provider", "used_for", "data_received"],
}


def kubectl_items(kind):
    result = subprocess.run(["kubectl", "--request-timeout=60s", "get", kind, "-A", "-o", "json"],
                            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"kubectl get {kind} failed")
    return json.loads(result.stdout).get("items", [])


def memory_gib(quantity):
    units = {"Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "K": 10 ** 3, "M": 10 ** 6, "G": 10 ** 9}
    match = re.fullmatch(r"([0-9.]+)(Ki|Mi|Gi|Ti|K|M|G)?", quantity or "")
    if not match:
        return ""
    return f"{float(match[1]) * units.get(match[2] or '', 1) / 2 ** 30:.1f}"


def environment_criticality(namespace):
    if namespace in PLATFORM_NAMESPACES or namespace in {"corp", "apps"} or namespace.endswith("-prod"):
        return "high"
    if namespace.endswith("-uat"):
        return "medium"
    if namespace.endswith("-int"):
        return "low"
    return ""


def classify(namespace, labels):
    owner = labels.get(OWNER) or labels.get("app.kubernetes.io/part-of") or ("platform" if namespace in PLATFORM_NAMESPACES else "")
    derived = environment_criticality(namespace)
    criticality = labels.get(CRITICALITY) or (f"{derived} (derived)" if derived else "")
    return owner or "not set", labels.get(DATA_CLASS) or "not set", criticality or "not set"


def servers(nodes):
    rows = []
    for node in nodes:
        labels, status = node["metadata"].get("labels", {}), node.get("status", {})
        info, capacity = status.get("nodeInfo", {}), status.get("capacity", {})
        roles = sorted(key.split("/", 1)[1] for key in labels if key.startswith("node-role.kubernetes.io/"))
        addresses = {a.get("type"): a.get("address") for a in status.get("addresses", [])}
        rows.append({"name": node["metadata"]["name"], "roles": ",".join(roles) or "worker", "cpu": capacity.get("cpu", ""),
                     "memory_gib": memory_gib(capacity.get("memory")), "architecture": info.get("architecture", ""),
                     "os": info.get("osImage", ""), "kernel": info.get("kernelVersion", ""),
                     "kubelet": info.get("kubeletVersion", ""), "internal_ip": addresses.get("InternalIP", ""),
                     "region": labels.get("topology.kubernetes.io/region", ""),
                     "zone": labels.get("topology.kubernetes.io/zone", ""),
                     "owner": labels.get(OWNER, "platform")})
    return sorted(rows, key=lambda row: row["name"])


def pod_spec(workload):
    spec = workload.get("spec", {})
    if workload["kind"] == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template", {}).get("spec", {})


def workloads(items):
    rows = []
    for workload in items:
        meta = workload["metadata"]
        labels = {**meta.get("labels", {}), **(workload.get("spec", {}).get("template", {}).get("metadata", {}).get("labels") or {})}
        spec = pod_spec(workload)
        images = sorted({c.get("image", "") for key in ("initContainers", "containers") for c in spec.get(key) or []})
        owner, data_class, criticality = classify(meta.get("namespace", ""), labels)
        rows.append({"namespace": meta.get("namespace", ""), "kind": workload["kind"], "name": meta["name"],
                     "images": " ".join(images), "owner": owner, "data_classification": data_class,
                     "criticality": criticality})
    return sorted(rows, key=lambda row: (row["namespace"], row["kind"], row["name"]))


def data_stores(claims):
    rows = []
    for claim in claims:
        meta, spec, status = claim["metadata"], claim.get("spec", {}), claim.get("status", {})
        owner, data_class, _ = classify(meta.get("namespace", ""), meta.get("labels", {}))
        rows.append({"namespace": meta.get("namespace", ""), "name": meta["name"],
                     "capacity": status.get("capacity", {}).get("storage")
                     or spec.get("resources", {}).get("requests", {}).get("storage", ""),
                     "storage_class": spec.get("storageClassName", ""), "access_modes": ",".join(spec.get("accessModes", [])),
                     "status": status.get("phase", ""), "owner": owner, "data_classification": data_class})
    return sorted(rows, key=lambda row: (row["namespace"], row["name"]))


def external_services(text):
    """Rows of the guide's service tables; the guide is the supplier inventory reviewed with each change.
    The switched-off telemetry table (first column "Component") lists no outside service."""
    rows, category, header = [], "", None
    for line in text.splitlines():
        if line.startswith("## "):
            category, header = line[3:].strip(), None
            continue
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = [cell.lower() for cell in cells]
        elif header[0] in ("service", "source") and not set("".join(cells)) <= set("-: "):
            value = dict(zip(header, cells))
            rows.append({"category": category, "service": value.get("service") or value.get("source", ""),
                         "provider": value.get("provider (headquarters)") or value.get("provider", ""),
                         "used_for": value.get("used for") or value.get("delivers", ""),
                         "data_received": value.get("data it receives") or value.get("data", "")})
    return rows


def spreadsheet_safe(value):
    """Keep a spreadsheet from running a cell as a formula."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_csv(section, rows):
    table = io.StringIO()
    writer = csv.DictWriter(table, fieldnames=COLUMNS[section])
    writer.writeheader()
    writer.writerows({key: spreadsheet_safe(row.get(key, "")) for key in COLUMNS[section]} for row in rows)
    return table.getvalue()


def collect():
    inventory = {"generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                 "staff_devices": "not inventoried: no endpoint agent yet (TODO §37)", "errors": []}
    for section, kind, build in (("servers", "nodes", servers), ("workloads", WORKLOAD_KINDS, workloads),
                                 ("data_stores", "persistentvolumeclaims", data_stores)):
        try:
            inventory[section] = build(kubectl_items(kind))
        except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            inventory[section] = []
            inventory["errors"].append(f"{section}: {error}")
    inventory["external_services"] = external_services(EXTERNAL_SERVICES.read_text())
    return inventory


def render_text(inventory):
    lines = [f"Swirl Cloud asset inventory - {inventory['generated_at']}", ""]
    for section, title in (("servers", "Servers"), ("workloads", "Workloads"), ("data_stores", "Data stores"),
                           ("external_services", "External services")):
        rows = inventory[section]
        lines.append(f"{title}: {len(rows)}")
        unowned = sum(row.get("owner") == "not set" for row in rows)
        if unowned:
            lines.append(f"  {unowned} without an owner label ({OWNER})")
    lines.append(f"Staff devices: {inventory['staff_devices']}")
    lines += [f"Not collected: {error}" for error in inventory["errors"]]
    return "\n".join(lines)


def write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)


def directory_argument(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("the output directory must not be empty")
    return Path(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output-dir", type=directory_argument,
                        help="also write one CSV per section and the JSON (mode 0600) here")
    args = parser.parse_args()
    inventory = collect()
    print(json.dumps(inventory, indent=2) if args.format == "json" else render_text(inventory))
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = inventory["generated_at"].replace(":", "").replace("-", "").replace("+0000", "Z")
        directory = args.output_dir / f"asset-inventory-{stamp}"
        directory.mkdir(mode=0o700)
        for section in COLUMNS:
            write_private(directory / f"{section.replace('_', '-')}.csv", to_csv(section, inventory[section]))
        write_private(directory / "inventory.json", json.dumps(inventory, indent=2))
        print(f"Inventory sheets: {directory}", file=sys.stderr)
    return 1 if inventory["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
