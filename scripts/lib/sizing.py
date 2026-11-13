#!/usr/bin/env python3
"""Sizing profiles (k8s/files/sizing/<profile>.yaml, TODO §26) outside Helm.

    sizing.py resolve [--explicit NAME]   print the profile: explicit, recorded, else suggested
    sizing.py recorded                    print the profile the root Application records, if any
    sizing.py check-volumes --root DIR    fail if rendered PVCs in DIR would shrink live ones
    sizing.py record --root DIR           put DIR's profile on the live root Application

The chart applies profiles in templates/_sizing.tpl; apply_replacements() repeats its
text replacements for helpers that start from the raw manifests (application delivery).
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
STANDARD = "standard"
NAME = re.compile(r"[a-z][a-z0-9-]{0,30}\Z")
# A suggestion only; the operator's choice and the recorded profile win.
LARGE_MIN_THREADS = 16
LARGE_MIN_MEMORY_GIB = 96


class SizingError(ValueError):
    pass


def available(root=ROOT):
    return [STANDARD] + sorted(path.stem for path in (root / "k8s/files/sizing").glob("*.yaml"))


def load(profile, root=ROOT):
    """The profile's definition; standard is empty."""
    if profile == STANDARD:
        return {"values": {}, "manifests": {}}
    if not NAME.fullmatch(profile or "") or profile not in available(root):
        raise SizingError(f"Unknown sizing profile {profile!r}; choose one of {', '.join(available(root))}")
    data = yaml.safe_load((root / "k8s/files/sizing" / f"{profile}.yaml").read_text()) or {}
    return {"values": data.get("values") or {}, "manifests": data.get("manifests") or {}}


def apply_replacements(text, path, key, profile, root=ROOT):
    """Apply a document's exact-text replacements, as templates/_sizing.tpl does."""
    for edit in load(profile, root)["manifests"].get(path, {}).get(key, {}).get("replace", []):
        if text.count(edit["from"]) != 1:
            raise SizingError(f"sizing {path} {key}: {edit['from']!r} must occur exactly once")
        text = text.replace(edit["from"], edit["to"])
    return text


def kubectl_json(*args):
    try:
        result = subprocess.run(["kubectl", "--request-timeout=10s", *args, "-o", "json"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def values_object(application):
    return application.get("spec", {}).get("source", {}).get("helm", {}).get("valuesObject") or {}


def recorded():
    """The profile on the live root Application; None when it cannot be read."""
    application = kubectl_json("-n", "infra", "get", "application", "swirl-cloud")
    if application is None:
        return None
    return values_object(application).get("sizingProfile") or STANDARD


def recording_patch(live, rendered):
    """The merge patch that moves the live root Application's valuesObject to the
    rendered profile, or None when it already records it. The previous profile's
    subchart values are removed; the rendered profile's are set."""
    current = values_object(live).get("sizingProfile") or STANDARD
    wanted = values_object(rendered)
    profile = wanted.get("sizingProfile") or STANDARD
    if profile == current:
        return None
    try:
        patch = {key: None for key in load(current)["values"]}
    except SizingError:
        patch = {}
    patch["sizingProfile"] = None if profile == STANDARD else profile
    patch.update({key: wanted[key] for key in load(profile)["values"]})
    return {"spec": {"source": {"helm": {"valuesObject": patch}}}}


def record(root):
    """Record the rendered profile on the live root Application before the installer
    applies sized manifests: Argo CD renders the platform from that Application, so
    its self-sync would otherwise put the previous sizes back. A first installation
    has no Application yet and applies the rendered one later."""
    live = kubectl_json("-n", "infra", "get", "application", "swirl-cloud")
    if live is None:
        return None
    path = Path(root) / "k8s/addons/swirl-cloud-application.yaml"
    rendered = next(document for document in yaml.safe_load_all(path.read_text())
                    if isinstance(document, dict) and document.get("kind") == "Application")
    patch = recording_patch(live, rendered)
    if patch is None:
        return None
    result = subprocess.run(["kubectl", "--request-timeout=10s", "-n", "infra", "patch", "application",
                             "swirl-cloud", "--type", "merge", "-p", json.dumps(patch)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SizingError(f"Could not record the sizing profile on the root Application: {result.stderr.strip()}")
    return values_object(rendered).get("sizingProfile") or STANDARD


def suggested(threads=None, memory_gib=None):
    threads = os.cpu_count() if threads is None else threads
    if memory_gib is None:
        try:
            kib = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                       if line.startswith("MemTotal:"))
            memory_gib = kib / 1024 / 1024
        except (OSError, StopIteration, ValueError):
            memory_gib = 0
    return "large" if threads >= LARGE_MIN_THREADS and memory_gib >= LARGE_MIN_MEMORY_GIB else STANDARD


def resolve(explicit=""):
    """(profile, reason): the operator's choice, else the recorded one, else a suggestion."""
    if explicit:
        load(explicit)
        return explicit, "chosen"
    current = recorded()
    if current:
        return current, "recorded on the installed platform"
    return suggested(), "suggested from this host's CPU threads and memory"


QUANTITY = re.compile(r"^([0-9.]+)(Ki|Mi|Gi|Ti|Pi|k|M|G|T|P)?$")
FACTOR = {None: 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15,
          "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50}


def quantity(value):
    match = QUANTITY.match(str(value))
    if not match:
        raise SizingError(f"Unreadable storage quantity {value!r}")
    return float(match[1]) * FACTOR[match[2]]


def shrinking(rendered, live):
    """[(namespace/name, live, rendered)] for PVCs the render would make smaller."""
    return [(name, live[name], size) for name, size in sorted(rendered.items())
            if name in live and quantity(size) < quantity(live[name])]


def rendered_claims(root):
    claims = {}
    for path in (Path(root) / "k8s").rglob("*.yaml"):
        try:
            documents = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue
        for document in documents:
            if isinstance(document, dict) and document.get("kind") == "PersistentVolumeClaim":
                meta = document.get("metadata", {})
                size = document.get("spec", {}).get("resources", {}).get("requests", {}).get("storage")
                if size and meta.get("name"):
                    claims[f"{meta.get('namespace', 'default')}/{meta['name']}"] = size
    return claims


def live_claims():
    claims = kubectl_json("get", "persistentvolumeclaims", "-A")
    if claims is None:
        return {}
    return {f"{item['metadata']['namespace']}/{item['metadata']['name']}":
            item.get("status", {}).get("capacity", {}).get("storage") or item["spec"]["resources"]["requests"]["storage"]
            for item in claims.get("items", [])}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("resolve").add_argument("--explicit", default="")
    commands.add_parser("recorded")
    commands.add_parser("check-volumes").add_argument("--root", required=True)
    commands.add_parser("record").add_argument("--root", required=True)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            profile, reason = resolve(args.explicit)
            print(f"{profile}\t{reason}")
        elif args.command == "recorded":
            print(recorded() or "")
        elif args.command == "record":
            print(record(args.root) or "")
        else:
            problems = shrinking(rendered_claims(args.root), live_claims())
            for name, live, size in problems:
                print(f"{name} is {live}; the selected profile renders {size}", file=sys.stderr)
            if problems:
                raise SizingError("Volumes only grow: keep the larger sizing profile (SIZING_PROFILE) "
                                  "or set these sizes explicitly before changing it.")
    except SizingError as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
