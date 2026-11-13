#!/usr/bin/env python3
"""Use GitOps Helm templates for native platform workloads and optional profiles."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import sizing  # noqa: E402


def identity(resource):
    meta = resource.get("metadata", {})
    return resource.get("apiVersion"), resource.get("kind"), meta.get("namespace", ""), meta.get("name")


def mounted_config_maps(resource):
    """Names of the ConfigMaps a workload's pod template mounts as volumes."""
    spec = resource.get("spec", {}).get("jobTemplate", {}).get("spec", resource.get("spec", {}))
    volumes = spec.get("template", {}).get("spec", {}).get("volumes", []) or []
    return [volume["configMap"]["name"] for volume in volumes if volume.get("configMap", {}).get("name")]


def merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--values", type=Path)
    args = parser.parse_args()
    values = yaml.safe_load((args.root / "k8s/values.yaml").read_text())
    values.update(publicDomain=os.environ["PLATFORM_DOMAIN"],
                  internalDnsZone=os.environ["INTERNAL_DNS_ZONE"],
                  gitopsRepositoryURL=os.environ["GITOPS_REPOSITORY_URL"],
                  cloudflareAccessTeamName=os.environ["CLOUDFLARE_ACCESS_TEAM_NAME"],
                  appsEnabled=os.environ.get("INSTALL_APPS", "true") == "true",
                  deschedulerEnabled=os.environ.get("INSTALL_DESCHEDULER", "true") == "true")
    if args.values:
        merge(values, yaml.safe_load(args.values.read_text()))
    enabled = os.environ.get("HIGH_AVAILABILITY_ENABLED", "false") == "true"
    values["highAvailabilityEnabled"] = enabled
    # An explicit profile, else the one the platform records, so reruns never shrink it.
    values["sizingProfile"] = os.environ.get("SIZING_PROFILE") or sizing.recorded() or sizing.STANDARD
    sizing.load(values["sizingProfile"])
    with tempfile.TemporaryDirectory(prefix="swirl-cloud-ha-render.") as directory:
        value_file = Path(directory) / "values.json"

        def render(enabled, templates=()):
            value_file.write_text(json.dumps({**values, "highAvailabilityEnabled": enabled}))
            command = ["helm", "template", "swirl-cloud", str(ROOT / "k8s"),
                       "--namespace", "infra", "--skip-tests", "--values", str(value_file)]
            for template in templates:
                command += ["--show-only", template]
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            return [doc for doc in yaml.safe_load_all(result.stdout) if doc and doc.get("kind")]

        baseline = {identity(doc) for doc in render(False)}
        desired = {identity(doc): doc for doc in render(enabled)}
        migrated_templates = [f"templates/{name}-ha.yaml" for name in ("postgres", "kafka")
                              if values.get(name + "Ha", {}).get("enabled")]
        migrated = {identity(doc) for doc in render(enabled, migrated_templates)} if migrated_templates else set()
    groups = ("base", "datastores", "platform", "corp", "addons")
    raw = {identity(doc) for group in groups for path in (args.root / "k8s" / group).glob("*.yaml")
           for doc in yaml.safe_load_all(path.read_text()) if doc}
    existing = set()
    for group in groups:
        for path in (args.root / "k8s" / group).glob("*.yaml"):
            original = list(yaml.safe_load_all(path.read_text()))
            transformed = []
            # Shared startup ConfigMaps go before their consumers, also on non-HA installs.
            startup = {"kafka.yaml": "kafka-remote-startup"}.get(path.name)
            if group == "datastores" and startup:
                key = ("v1", "ConfigMap", "infra", startup)
                if key in desired:
                    transformed.append(desired[key])
                    existing.add(key)
            for doc in original:
                if not doc:
                    continue
                key = identity(doc)
                existing.add(key)
                rendered = desired.get(key, doc)
                # ConfigMaps the chart generates from a marker (Odoo's add-ons) exist only
                # in the render; ship them before the workload that mounts them, which a
                # first installation applies before Argo CD exists.
                for name in mounted_config_maps(rendered):
                    generated = ("v1", "ConfigMap", rendered.get("metadata", {}).get("namespace", ""), name)
                    if generated in desired and generated not in raw and generated not in existing:
                        transformed.append(desired[generated])
                        existing.add(generated)
                transformed.append(rendered)
            path.write_text(yaml.safe_dump_all(transformed, sort_keys=False))
    # The installer's profile phase applies the auth ConfigMap before Redis reads it.
    auth_config = ("v1", "ConfigMap", "infra", "application-data-access")
    extra = [doc for key, doc in desired.items() if key not in existing and (key not in baseline or key in migrated or key == auth_config)]
    # Migration helpers do the bootstrap and cutover; ordinary reconciles still
    # keep the verified Cluster, startup config and PDBs up to date.
    extra_dir = args.root / "k8s/ha"
    extra_dir.mkdir(exist_ok=True)
    (extra_dir / "platform.yaml").write_text(yaml.safe_dump_all(extra, sort_keys=False))
    (args.root / "k8s/values.yaml").write_text(yaml.safe_dump(values, sort_keys=False))


if __name__ == "__main__":
    main()
