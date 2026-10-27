#!/usr/bin/env python3
"""Offline deployment validation: source inputs and installer/GitOps rendering."""

import argparse
import base64
import copy
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from manifest_checks import (  # noqa: E402
    alert_policy, argocd_probe_policy, documents, embedded_syntax, identity, images, index, ingress_policy,
    lifecycle_policies, portal_translation, prometheus_config, require, resource_wiring, run, runner_priority,
    vulnerability_exceptions, workload_policy)

GROUPS = ("base", "datastores", "platform", "corp", "addons")
TOKEN = re.compile(r"__[A-Z][A-Z0-9_]+__")
DIGEST = re.compile(r"@sha256:[a-f0-9]{64}$")
FILE_MARKER = re.compile(r"^( *)# swirl-cloud:file (\S+)$", re.M)
BINARY_FILE = re.compile(r"\.(png|ico|jpe?g|gif|webp)$")


def inline_files(text):
    """Fill `# swirl-cloud:file` markers as templates/resources.yaml does."""
    def body(match):
        source = ROOT / "k8s/files" / match[2]
        require(source.is_file(), f"missing k8s/files/{match[2]}")
        if BINARY_FILE.search(match[2]):
            return match[1] + base64.b64encode(source.read_bytes()).decode()
        return "\n".join(match[1] + line if line else line for line in source.read_text().removesuffix("\n").split("\n"))
    return FILE_MARKER.sub(body, text)


def comparable(doc):
    if not doc:
        return doc
    if doc["kind"] == "Application":
        doc = copy.deepcopy(doc)
        helm = doc["spec"]["source"]["helm"]
        defaults = yaml.safe_load((ROOT / "k8s/values.yaml").read_text())

        def merge(base, override):
            for key, value in override.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    merge(base[key], value)
                else:
                    base[key] = value

        merge(defaults, helm.get("valuesObject", {}))
        helm["valuesObject"] = defaults
        return doc
    if doc["kind"] != "ConfigMap":
        return doc
    # JSON/YAML encoders can escape the same value differently (e.g. &).
    data = dict(doc.get("data", {}))
    for name, content in data.items():
        if name.endswith((".yaml", ".yml", ".json")):
            data[name] = list(yaml.safe_load_all(content))
    return {**doc, "data": data}


def source_inputs():
    files = {ROOT / name for name in run("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0") if name}
    for path in sorted(files):
        # Packaged charts and vendored images are not text sources.
        if not path.is_file() or path.suffix in {".tgz", ".png"}:
            continue
        text = path.read_text()
        if path.suffix == ".py":
            compile(text, str(path), "exec")
        elif path.suffix in {".yaml", ".yml", ".values"} and "templates" not in path.parts:
            list(yaml.safe_load_all(text))
        elif path.suffix == ".json":
            json.loads(text)
        elif path.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^\s)]+)\)", text):
                if not target.startswith(("http:", "https:", "mailto:", "#")):
                    require((path.parent / target.split("#")[0]).exists(), f"{path.relative_to(ROOT)}: broken link {target}")
    contract = {}
    for line in (ROOT / "config/platform.env").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*=[^\s]*", line), "Invalid platform.env property")
        name, value = line.split("=", 1)
        require(name not in contract, f"Duplicate platform property: {name}")
        contract[name] = value
        if name.endswith("_MANIFESTS"):
            paths = value.split(",") if value else []
            require(len(paths) == len(set(paths)), f"Duplicate manifest in {name}")
            for manifest in paths:
                require((ROOT / "k8s" / manifest).is_file(), f"{name}: missing {manifest}")
    resources = []
    for group in GROUPS:
        for path in (ROOT / "k8s" / group).glob("*.yaml"):
            for doc in documents(inline_files(path.read_text())):
                workload_policy(doc)
                for image in images(doc):
                    require(DIGEST.search(image), f"{path.name}: image must have an immutable digest: {image}")
                resources.append(doc)
    index(resources)
    alert_policy(resources)
    runner_priority(resources)
    hosts = {rule["host"].removesuffix(".__PUBLIC_DOMAIN__") for doc in resources if doc["kind"] == "Ingress"
             for rule in doc["spec"].get("rules", []) if rule.get("host", "").endswith(".__PUBLIC_DOMAIN__")}
    require(hosts == set(contract["DEFAULT_CLOUDFLARE_HOST_LABELS"].split(",")), "Ingress and public DNS inventories differ")
    ingress_policy(contract)
    vulnerability_exceptions()
    portal_translation()
    argocd_probe_policy(contract)
    for path in (ROOT / ".github/workflows").glob("*.y*ml"):
        for action in re.findall(r"^\s+(?:- )?uses:\s*(\S+)", path.read_text(), re.M):
            require(action.startswith("./") or re.search(r"@[a-f0-9]{40}$|@sha256:[a-f0-9]{64}$", action), f"Unpinned action: {action}")
    for path in (ROOT / "k8s/templates").iterdir():
        for method, target in re.findall(r'\.Files\.(Get|Glob) "([^"]+)"', path.read_text()):
            require(list((ROOT / "k8s").glob(target)), f"{path.name}: missing chart {method} input {target}")
    print("[PASS] Source syntax, documentation links, manifests and deployment policy", flush=True)


def render_profiles(output):
    # Explicit synthetic settings keep offline validation independent of the
    # operator's environment, kubeconfig and installed cluster identity.
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", KUBECONFIG="/dev/null")
    for legacy in ("bootstrap", "patched"):
        result = subprocess.run(["helm", "template", "swirl-cloud", str(ROOT / "k8s"),
                                 "--values", str(ROOT / f"k8s/profiles/security-images-{legacy}.values")],
                                text=True, capture_output=True, env=env)
        require(result.returncode != 0 and "Legacy ingress/image settings require explicit migration" in result.stderr,
                f"Retired {legacy} profile did not stop reconciliation")
    profiles = [(ha, apps, None, False) for ha, apps in itertools.product((False, True), repeat=2)]
    profiles += [(True, True, phase, False) for phase in ("dynamic", "expanded", "active")]
    profiles += [(False, True, None, True), (True, True, "active", True)]
    for ha, apps, phase, shared_data in profiles:
        label = f"upstream-{'ha' if ha else 'single'}-{'corp' if apps else 'infra'}"
        if phase:
            label += f"-{phase}-fencing"
        if shared_data:
            label += "-shared-data"
        stage = output / label
        flag = lambda value: str(value).lower()
        profile_env = dict(env, HIGH_AVAILABILITY_ENABLED=flag(ha))
        if shared_data and not ha:
            profile_env["PORTAL_LANGUAGE"] = "fr"  # One profile proves the French portal renders the same both ways.
        migration = {}
        if phase:
            migration = yaml.safe_load((ROOT / "config/postgres-ha-values.yaml").read_text())
            migration["postgresHa"]["active"] = phase == "active"
            migration.update(yaml.safe_load((ROOT / "config/kafka-ha-values.yaml").read_text()))
            migration["kafkaHa"].update(phase=phase, clusterId="8ipNY9RxQtWkattTais5yQ")
            migration["nodeFencing"] = {"enabled": True, "inventorySecret": "fixture-inventory", "nodeNames": ["fixture-node"]}
        if shared_data:
            migration.update(yaml.safe_load((ROOT / "config/application-data-values.yaml").read_text()))
            migration["applicationData"]["kafkaBrokers"] = [
                {"id": ordinal, "host": "100.100.0.10", "port": 30940 + ordinal}
                for ordinal in range(3 if phase == "active" else 1)]
        if migration:
            migration_file = output / f"{label}-values.json"
            migration_file.write_text(json.dumps(migration))
            profile_env["PLATFORM_HA_VALUES_FILE"] = str(migration_file)
        run("bash", str(ROOT / "scripts/render-cluster-config.sh"), "--output", str(stage),
            "--domain", "example.com", "--internal-domain", "internal.example.com",
            "--organization-name", "Example & Company", "--organization-slug", "example",
            "--gitlab-group", "example", "--gitlab-project", "swirl-cloud",
            "--gitops-repository", "https://github.com/example/swirl-cloud.git",
            "--cloudflare-access-team", "example", "--apps-enabled", flag(apps),
            "--descheduler-enabled", flag(apps), env=profile_env)
        application = yaml.safe_load((stage / "k8s/addons/swirl-cloud-application.yaml").read_text())
        helm = application["spec"]["source"]["helm"]
        values = dict(helm["valuesObject"])
        for parameter in helm["parameters"]:
            target = values
            parts = parameter["name"].split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            value = parameter["value"]
            if not parameter.get("forceString") and value in ("true", "false"):
                value = value == "true"
            target[parts[-1]] = value
        value_file = stage / "gitops-values.json"
        value_file.write_text(json.dumps(values))
        options = ["--namespace", "infra"]
        for profile in helm.get("valueFiles", []):
            options += ["--values", str(ROOT / "k8s" / profile)]
        options += ["--values", str(value_file)]
        run("helm", "lint", str(ROOT / "k8s"), "--strict", *options, env=env)
        rendered = run("helm", "template", "swirl-cloud", str(ROOT / "k8s"), "--skip-tests", *options, env=env)
        require(set(TOKEN.findall(rendered)) <= {"__RUNNER_TOKEN__"}, f"{label}: unresolved deployment placeholders")
        require(not FILE_MARKER.search(rendered), f"{label}: unresolved file markers")
        runtime = index(documents(rendered))
        resource_wiring(list(runtime.values()))
        lifecycle_policies(rendered, label)
        prometheus_config(runtime, stage)
        owned = {identity(doc) for block in rendered.split("\n---")
                 if "# Source: swirl-cloud/templates/" in block for doc in documents(block)}
        require(any(doc["kind"] == "Namespace" and doc["metadata"]["name"] == "apps" for doc in runtime.values()), f"{label}: missing apps foundation")
        require(any(doc["kind"] == "Deployment" and doc["metadata"]["name"] == "odoo" for doc in runtime.values()) == apps, f"{label}: incorrect Odoo scope")
        for group in (*GROUPS, "ha"):
            for path in (stage / "k8s" / group).glob("*.yaml"):
                if (not apps and (group == "corp" or path.name in {"corp-namespace.yaml", "descheduler.yaml"})) or path.name == "descheduler-run-job.yaml":
                    continue
                for doc in documents(path.read_text()):
                    require(comparable(runtime.get(identity(doc))) == comparable(doc), f"{label}: installer/GitOps mismatch in {path.relative_to(stage)}: {identity(doc)}")
        for doc in runtime.values():
            # Vendored dependencies own their image schema. Enforce our digest
            # policy on platform-owned resources; lint renders dependencies too.
            if identity(doc) in owned:
                for image in images(doc):
                    require(DIGEST.search(image), f"{label}: unpinned rendered image {image}")
            if not ha and apps:
                embedded_syntax(doc)
        (output / f"{label}.yaml").write_text(rendered)
        print(f"[PASS] {label}: Helm lint, {len(runtime)} wired resources and installer/GitOps parity", flush=True)


def render_onboarding(output):
    # Exercise the real renderer: raw YAML substitutions can silently turn valid
    # Git branch names into booleans/numbers or prevent a default-branch switch.
    sys.path.insert(0, str(ROOT / "scripts/lib"))
    from repository_onboarding import OnboardingError, render

    fixture = output / "onboarding"
    (fixture / "infra/k8s").mkdir(parents=True)
    (fixture / "infra/argocd").mkdir()
    app_path = fixture / "infra/argocd/application.yaml"
    settings_path = fixture / "infra/onboarding-values.json"
    contract = {"application": "infra/argocd/application.yaml", "files": ["infra/argocd/application.yaml"]}
    application = {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": "fixture"},
        "spec": {
            "project": "default",
            "source": {"repoURL": "https://example.com/source.git", "path": "infra/k8s", "targetRevision": "HEAD"},
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "apps"},
        },
    }
    context = {"DEFAULT_BRANCH": "main", "GITLAB_REPOSITORY_URL": "http://gitlab.example.internal/team/nested/app.git"}
    for branch in ("no", "true", "null", "123", "release/stable", "release's"):
        app_path.write_text(yaml.safe_dump(application))
        current = dict(context, DEFAULT_BRANCH=branch)
        render(fixture, contract, current, {})
        rendered = yaml.safe_load(app_path.read_text())
        require(rendered["spec"]["source"]["targetRevision"] == branch,
                f"Onboarding did not preserve the branch string {branch!r}")
        saved = json.loads(settings_path.read_text())
        first = app_path.read_bytes()
        render(fixture, contract, current, {})
        require(app_path.read_bytes() == first, f"Onboarding changed a current-branch rerun for {branch!r}")
        switched = dict(current, DEFAULT_BRANCH=f"next/{branch}")
        render(fixture, contract, switched, saved)
        require(yaml.safe_load(app_path.read_text())["spec"]["source"]["targetRevision"] == switched["DEFAULT_BRANCH"],
                f"Onboarding did not follow a default-branch switch from {branch!r}")
        before = (app_path.read_bytes(), settings_path.read_bytes())
        render(fixture, contract, switched, json.loads(settings_path.read_text()))
        require((app_path.read_bytes(), settings_path.read_bytes()) == before,
                f"Onboarding changed a saved-settings rerun for {branch!r}")
    for revision, previous_branch in (("unrelated", "old-default"), ("", ""), (None, None), (True, True), (123, 123)):
        application["spec"]["source"]["targetRevision"] = revision
        app_path.write_text(yaml.safe_dump(application))
        try:
            render(fixture, contract, context, {"context": {"DEFAULT_BRANCH": previous_branch}})
        except OnboardingError as error:
            require("follow the default branch" in str(error), f"Unexpected onboarding failure: {error}")
        else:
            raise ValueError(f"Onboarding accepted an unrelated or invalid revision {revision!r}")
    print("[PASS] Onboarding branch rendering, saved-settings reruns and unrelated-revision rejection", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Private temporary directory for rendered validation manifests")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_inputs()
    render_onboarding(args.output)
    render_profiles(args.output)


if __name__ == "__main__":
    main()
