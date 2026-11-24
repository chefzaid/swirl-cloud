"""Multi-server budget for a sizing profile (TODO §26): can the platform still be
scheduled, and its volumes still hold every copy, after the largest server fails?

Requests are what the scheduler counts, so the budget is in requests, not limits:
- the platform's Deployments and StatefulSets as rendered in DIR with the profile:
  its containers' requests merged as templates/_sizing.tpl does, times the rendered
  replicas or the live pod count when HA runs more;
- every other running or pending pod with an owner (Helm releases, applications)
  at its live requests. DaemonSet pods are left out of the need and taken off each
  node's allocatable instead, since they run per node. Bare pods (CI jobs) and Jobs
  are transient and not counted: keep room for the runner's concurrent jobs.
- storage: each Longhorn volume's size, or the profile's larger claim size, times
  its copies, plus rendered claims not created yet, against Longhorn's schedulable
  disk space.

A single server has no host to fail over to, so it is not checked.
"""
from pathlib import Path
import re

import yaml

CPU = re.compile(r"^([0-9.]+)(m)?$")
WORKLOADS = ("Deployment", "StatefulSet")


def cpu(value):
    """Cores, from a Kubernetes CPU quantity."""
    match = CPU.match(str(value))
    if not match:
        raise ValueError(f"Unreadable CPU quantity {value!r}")
    return float(match[1]) / (1000 if match[2] else 1)


def requests_of(resources, quantity):
    requests = (resources or {}).get("requests") or {}
    return {"cpu": cpu(requests["cpu"]) if "cpu" in requests else 0.0,
            "memory": quantity(requests["memory"]) if "memory" in requests else 0.0}


def merged(resources, override):
    result = {key: dict(value) for key, value in (resources or {}).items()}
    for key, value in (override or {}).items():
        result.setdefault(key, {}).update(value)
    return result


def rendered_workloads(root, manifests, quantity, apps_enabled=True):
    """{(namespace, kind, name): (replicas, total requests per replica)} of DIR's platform;
    corp/ renders only with the company applications (appsEnabled)."""
    found = {}
    for path in (Path(root) / "k8s").rglob("*.yaml"):
        relative = path.relative_to(Path(root) / "k8s").as_posix()
        if relative.startswith("corp/") and not apps_enabled:
            continue
        entries = manifests.get(relative, {})
        try:
            documents = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue
        for document in documents:
            if not isinstance(document, dict) or document.get("kind") not in WORKLOADS:
                continue
            meta = document.get("metadata") or {}
            spec = document.get("spec") or {}
            override = (entries.get(f"{document['kind']}/{meta.get('name')}") or {}).get("containers") or {}
            total = {"cpu": 0.0, "memory": 0.0}
            for container in ((spec.get("template") or {}).get("spec") or {}).get("containers") or []:
                resources = merged(container.get("resources"), (override.get(container.get("name")) or {}).get("resources"))
                for key, value in requests_of(resources, quantity).items():
                    total[key] += value
            replicas = spec.get("replicas", 1)
            if isinstance(replicas, int):
                found[(meta.get("namespace", "infra"), document["kind"], meta.get("name"))] = (replicas, total)
    return found


def owner(pod, replica_sets):
    """(namespace, kind, name) of the pod's workload, or None for bare pods and Jobs."""
    references = pod["metadata"].get("ownerReferences") or []
    if not references:
        return None
    reference = next((r for r in references if r.get("controller")), references[0])
    namespace = pod["metadata"].get("namespace", "default")
    if reference["kind"] == "ReplicaSet":
        deployment = replica_sets.get((namespace, reference["name"]))
        return (namespace, "Deployment", deployment) if deployment else (namespace, "ReplicaSet", reference["name"])
    if reference["kind"] == "Job":
        return None
    return namespace, reference["kind"], reference["name"]


def pod_requests(pod, quantity):
    total = {"cpu": 0.0, "memory": 0.0}
    for container in pod.get("spec", {}).get("containers") or []:
        for key, value in requests_of(container.get("resources"), quantity).items():
            total[key] += value
    return total


def schedulable(node):
    taints = node.get("spec", {}).get("taints") or []
    return not node.get("spec", {}).get("unschedulable") and not any(t.get("effect") in ("NoSchedule", "NoExecute") for t in taints)


def compute_budget(workloads, nodes, pods, replica_sets, quantity):
    """{"need": {...}, "nodes": {node: usable}, "after_loss": {...}, "lost": node} in cores and bytes."""
    hosts = {n["metadata"]["name"]: {"cpu": cpu(n["status"]["allocatable"]["cpu"]),
                                     "memory": quantity(n["status"]["allocatable"]["memory"])}
             for n in nodes if schedulable(n)}
    need = {"cpu": 0.0, "memory": 0.0}
    live = {}
    for pod in pods:
        if pod.get("status", {}).get("phase") not in ("Running", "Pending"):
            continue
        workload = owner(pod, replica_sets)
        if workload is None:
            continue
        if workload in workloads:
            live[workload] = live.get(workload, 0) + 1
            continue
        requests = pod_requests(pod, quantity)
        if workload[1] == "DaemonSet":
            host = hosts.get(pod.get("spec", {}).get("nodeName"))
            if host:
                for key in host:
                    host[key] -= requests[key]
            continue
        for key in need:
            need[key] += requests[key]
    # The rendered replicas, or more where a profile or HA scaled the workload live.
    for workload, (replicas, requests) in workloads.items():
        for key in need:
            need[key] += max(replicas, live.get(workload, 0)) * requests[key]
    lost = max(hosts, key=lambda name: (hosts[name]["memory"], hosts[name]["cpu"])) if hosts else None
    after = {key: sum(h[key] for name, h in hosts.items() if name != lost) for key in ("cpu", "memory")}
    return {"need": need, "nodes": hosts, "after_loss": after, "lost": lost}


def storage_budget(volumes, longhorn_nodes, claims, quantity):
    """Longhorn copies needed against schedulable disk space after losing the largest storage host."""
    def size(value):
        return float(value) if str(value).isdigit() else quantity(value)
    copies_default = max([v["spec"].get("numberOfReplicas", 1) for v in volumes] or [1])
    need = 0.0
    seen = set()
    for volume in volumes:
        if volume["metadata"].get("deletionTimestamp"):
            continue
        status = (volume.get("status") or {}).get("kubernetesStatus") or {}
        claim = f"{status.get('namespace')}/{status.get('pvcName')}" if status.get("pvcName") else None
        seen.add(claim)
        bytes_ = max(size(volume["spec"]["size"]), quantity(claims[claim]) if claim in claims else 0)
        need += bytes_ * volume["spec"].get("numberOfReplicas", 1)
    need += sum(quantity(value) * copies_default for name, value in claims.items() if name not in seen)
    space = {}
    for node in longhorn_nodes:
        if not node["spec"].get("allowScheduling", True):
            continue
        disks = node["spec"].get("disks") or {}
        statuses = (node.get("status") or {}).get("diskStatus") or {}
        space[node["metadata"]["name"]] = sum(
            float(statuses.get(name, {}).get("storageMaximum", 0)) - float(disk.get("storageReserved", 0))
            for name, disk in disks.items() if disk.get("allowScheduling", True))
    lost = max(space, key=space.get) if space else None
    return {"need": need, "after_loss": sum(v for name, v in space.items() if name != lost), "lost": lost,
            "hosts": len(space), "copies": copies_default}


def check(workloads, nodes, pods, replica_sets, volumes, longhorn_nodes, claims, quantity):
    """(report lines, problems). A single schedulable server is not checked."""
    budget = compute_budget(workloads, nodes, pods, replica_sets, quantity)
    if len(budget["nodes"]) < 2:
        return ["One schedulable server: a host failure stops the platform, so no failover budget applies."], []
    gib = 2 ** 30
    lines = [f"Without {budget['lost']}: requests need {budget['need']['cpu']:.1f} CPU and {budget['need']['memory'] / gib:.1f} GiB;"
             f" the other servers offer {budget['after_loss']['cpu']:.1f} CPU and {budget['after_loss']['memory'] / gib:.1f} GiB."]
    problems = [f"{label} requests ({budget['need'][key] / unit:.1f}{suffix}) exceed what the servers other than "
                f"{budget['lost']} offer ({budget['after_loss'][key] / unit:.1f}{suffix})"
                for key, label, unit, suffix in (("cpu", "CPU", 1, " CPU"), ("memory", "Memory", gib, " GiB"))
                if budget["need"][key] > budget["after_loss"][key]]
    if longhorn_nodes:
        storage = storage_budget(volumes, longhorn_nodes, claims, quantity)
        lines.append(f"Without {storage['lost']}: Longhorn copies need {storage['need'] / gib:.0f} GiB;"
                     f" the other storage servers offer {storage['after_loss'] / gib:.0f} GiB.")
        if storage["need"] > storage["after_loss"]:
            problems.append(f"Longhorn copies ({storage['need'] / gib:.0f} GiB) exceed the schedulable disk space of the storage "
                            f"servers other than {storage['lost']} ({storage['after_loss'] / gib:.0f} GiB)")
        if storage["hosts"] - 1 < storage["copies"]:
            lines.append(f"Note: volumes keep {storage['copies']} copies on {storage['hosts']} storage servers; after a loss they"
                         " run degraded until the server returns.")
    return lines, problems
