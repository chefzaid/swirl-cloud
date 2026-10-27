"""Policy checks for scripts/validate-manifests.py: resource wiring and the workload,
probe, alert, lifecycle, vulnerability-exception, Prometheus, Argo CD and ingress policies."""

import datetime
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
VEX_JUSTIFICATIONS = {"component_not_present", "vulnerable_code_not_present", "vulnerable_code_not_in_execute_path",
                      "vulnerable_code_cannot_be_controlled_by_adversary", "inline_mitigations_already_exist"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(*command, **kwargs):
    return subprocess.check_output(command, text=True, cwd=ROOT, **kwargs)


def documents(text):
    return [doc for doc in yaml.safe_load_all(text) if isinstance(doc, dict) and doc.get("kind")]


def identity(doc):
    meta = doc.get("metadata", {})
    return doc.get("apiVersion"), doc["kind"], meta.get("namespace", ""), meta.get("name", meta.get("generateName"))


def index(resources):
    result = {}
    for doc in resources:
        key = identity(doc)
        require(key not in result, f"Duplicate resource: {key}")
        result[key] = doc
    return result


def images(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"image", "imageName"} and isinstance(child, str):
                yield child
            elif key == "expression" and isinstance(child, str):
                yield from re.findall(r'''\bimage:\s*['"]([^'"]+)['"]''', child)
            yield from images(child)
    elif isinstance(value, list):
        for child in value:
            yield from images(child)


def workload_policy(doc):
    spec = doc.get("spec", {})
    if doc["kind"] == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    if doc["kind"] not in {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}:
        return
    pod = spec["template"]["spec"]
    label = "/".join(str(part) for part in identity(doc))
    require(isinstance(pod.get("automountServiceAccountToken"), bool), f"{label}: declare token mounting intent")
    for container in pod.get("initContainers", []) + pod.get("containers", []):
        for budget in ("requests", "limits"):
            values = container.get("resources", {}).get(budget, {})
            require(values.get("cpu") and values.get("memory"), f"{label}/{container['name']}: missing {budget}")
        probe_policy(f"{label}/{container['name']}", container)


def probe_policy(label, container):
    """A loaded node answers slowly: a restart or a removal from its Service must wait for a real failure."""
    liveness = container.get("livenessProbe")
    if liveness:
        require(liveness.get("timeoutSeconds", 1) >= 5, f"{label}: a liveness probe needs a timeout of 5 seconds or more")
        require(liveness.get("periodSeconds", 10) * liveness.get("failureThreshold", 3) >= 60,
                f"{label}: a liveness probe must fail for a minute before a restart")
    readiness = container.get("readinessProbe")
    if readiness:
        require(readiness.get("timeoutSeconds", 1) >= 3, f"{label}: a readiness probe needs a timeout of 3 seconds or more")
    literal = {env["name"] for env in container.get("env", []) if "value" in env}
    for kind in ("startupProbe", "livenessProbe", "readinessProbe"):
        command = " ".join((container.get(kind) or {}).get("exec", {}).get("command", []))
        for name in re.findall(r"\$\(([A-Za-z_][A-Za-z0-9_]*)\)", command):
            require(name in literal, f"{label}: {kind} expands $({name}) only from a literal env value")


def embedded_syntax(doc):
    if doc["kind"] != "ConfigMap":
        return
    for name, content in doc.get("data", {}).items():
        if not isinstance(content, str):
            continue
        if name.endswith(".py"):
            compile(content, name, "exec")
        elif name.endswith(".sh"):
            subprocess.run(["bash", "-n"], input=content, text=True, check=True)
        elif name.endswith((".js", ".mjs")):
            with tempfile.NamedTemporaryFile(suffix=Path(name).suffix, mode="w") as script:
                script.write(content)
                script.flush()
                run("node", "--check", script.name)


def resource_wiring(resources):
    """Resolve chart-owned references and the few separately installed owners."""
    by_name = {(doc["kind"], doc["metadata"].get("namespace", ""), doc["metadata"]["name"]): doc for doc in resources}
    # Vault and Argo CD are separate Helm releases; these roles are Kubernetes
    # built-ins. The fencing inventory belongs to the explicit validation fixture.
    external = {
        ("ServiceAccount", "infra", "vault"): {},
        ("Service", "infra", "vault"): {"spec": {"ports": [{"port": 8200}]}},
        ("Service", "infra", "argocd-server"): {"spec": {"ports": [{"port": 80}]}},
        ("ClusterRole", "", "cluster-admin"): {},
        ("ClusterRole", "", "system:auth-delegator"): {},
        ("Secret", "infra", "fixture-inventory"): {},
        # configure-application-data.py owns this persistent Secret; its guarded
        # preparation must complete before the opt-in Redis auth profile runs.
        ("Secret", "infra", "application-redis-auth"): {"data": {"auth": "fixture", "users.acl": "fixture"}},
        # The separately installed Traefik chart owns this entrypoint middleware.
        ("Middleware", "infra", "forwarded-headers"): {},
        ("ConfigMap", "infra", "ingress-proxy-trust"): {"data": {"trustedIPs": "fixture-edge-cidrs"}},
    }
    for key, doc in external.items():
        by_name.setdefault(key, doc)
    for doc in resources:
        if doc["kind"] == "ExternalSecret":
            target = doc["spec"].get("target", {}).get("name", doc["metadata"]["name"])
            by_name[("Secret", doc["metadata"]["namespace"], target)] = doc

    def reference(kind, namespace, name, owner, key=None, optional=False):
        target = by_name.get((kind, namespace, name))
        require(optional or target is not None, f"{owner}: missing {kind} {namespace}/{name}")
        if target is not None and key and not optional:
            if target.get("kind") == "ExternalSecret":
                template = target["spec"].get("target", {}).get("template")
                keys = set((template or {}).get("data", {}))
                # ESO's default Replace policy emits template outputs only.
                # Dynamic template/provider keys cannot be inferred offline.
                if template and template.get("templateFrom"):
                    return target
                if template is None or template.get("mergePolicy", "Replace") == "Merge":
                    if target["spec"].get("dataFrom"):
                        return target
                    keys.update(entry["secretKey"] for entry in target["spec"].get("data", []))
            else:
                keys = set(target.get("data", {})) | set(target.get("stringData", {})) | set(target.get("binaryData", {}))
            require(key in keys, f"{owner}: missing {kind} key {namespace}/{name}:{key}")
        return target

    pods = []
    for doc in resources:
        kind = doc["kind"]
        namespace = doc["metadata"].get("namespace", "")
        name = doc["metadata"]["name"]
        owner = f"{kind} {namespace}/{name}"
        annotations = doc["metadata"].get("annotations") or {}
        middlewares = annotations.get("traefik.ingress.kubernetes.io/router.middlewares", "")
        for middleware in filter(None, middlewares.split(",")):
            prefix = f"{namespace}-"
            require(middleware.startswith(prefix) and middleware.endswith("@kubernetescrd"),
                    f"{owner}: middleware must belong to the route namespace: {middleware}")
            reference("Middleware", namespace, middleware[len(prefix):-len("@kubernetescrd")], owner)
        transport = annotations.get("traefik.ingress.kubernetes.io/service.serverstransport")
        if transport:
            prefix = f"{namespace}-"
            require(transport.startswith(prefix) and transport.endswith("@kubernetescrd"),
                    f"{owner}: invalid local ServersTransport: {transport}")
            reference("ServersTransport", namespace, transport[len(prefix):-len("@kubernetescrd")], owner)
        spec = doc.get("spec", {})
        if kind == "CronJob":
            spec = spec["jobTemplate"]["spec"]
        template = spec.get("template", {})
        if "spec" in template:
            pod = template["spec"]
            labels = template.get("metadata", {}).get("labels", {})
            pods.append((namespace, labels, pod, owner))
            if kind == "StatefulSet":
                reference("Service", namespace, spec["serviceName"], owner)
                for ordinal in range(spec.get("replicas", 1)):
                    pods.append((namespace, {**labels, "statefulset.kubernetes.io/pod-name": f"{name}-{ordinal}"}, pod, owner))
            account = pod.get("serviceAccountName", "default")
            if account != "default":
                reference("ServiceAccount", namespace, account, owner)
            for secret in pod.get("imagePullSecrets", []):
                reference("Secret", namespace, secret["name"], owner)
            volumes = {volume["name"] for volume in pod.get("volumes", [])}
            volumes.update(claim["metadata"]["name"] for claim in spec.get("volumeClaimTemplates", []))
            for volume in pod.get("volumes", []):
                if "persistentVolumeClaim" in volume:
                    reference("PersistentVolumeClaim", namespace, volume["persistentVolumeClaim"]["claimName"], owner)
                for source in [volume, *volume.get("projected", {}).get("sources", [])]:
                    for resource, field in (("ConfigMap", "configMap"), ("Secret", "secret")):
                        if field in source:
                            value = source[field]
                            target = value.get("name", value.get("secretName"))
                            for key in [None, *(item["key"] for item in value.get("items", []))]:
                                reference(resource, namespace, target, owner, key, value.get("optional", False))
            for container in pod.get("initContainers", []) + pod.get("containers", []):
                for mount in container.get("volumeMounts", []) + container.get("volumeDevices", []):
                    require(mount["name"] in volumes, f"{owner}/{container['name']}: missing volume {mount['name']}")
                for entry in container.get("env", []):
                    for resource, field in (("ConfigMap", "configMapKeyRef"), ("Secret", "secretKeyRef")):
                        value = entry.get("valueFrom", {}).get(field)
                        if value:
                            reference(resource, namespace, value["name"], owner, value["key"], value.get("optional", False))
                for entry in container.get("envFrom", []):
                    for resource, field in (("ConfigMap", "configMapRef"), ("Secret", "secretRef")):
                        if field in entry:
                            value = entry[field]
                            reference(resource, namespace, value["name"], owner, optional=value.get("optional", False))
        if kind in {"RoleBinding", "ClusterRoleBinding"}:
            role = doc["roleRef"]
            reference(role["kind"], namespace if role["kind"] == "Role" else "", role["name"], owner)
            for subject in doc.get("subjects", []):
                if subject["kind"] == "ServiceAccount":
                    reference("ServiceAccount", subject.get("namespace", namespace), subject["name"], owner)
        if kind in {"MutatingAdmissionPolicyBinding", "ValidatingAdmissionPolicyBinding"}:
            reference(kind.removesuffix("Binding"), "", spec["policyName"], owner)

    for doc in resources:
        kind, namespace, name = doc["kind"], doc["metadata"].get("namespace", ""), doc["metadata"]["name"]
        spec = doc.get("spec", {})
        owner = f"{kind} {namespace}/{name}"
        if kind == "Service" and spec.get("selector"):
            selector = spec["selector"]
            selected = [(pod, workload) for ns, labels, pod, workload in pods if ns == namespace
                        and all(labels.get(key) == value for key, value in selector.items())]
            # CloudNativePG supplies the primary pod after reconciling its Cluster.
            cluster = selector.get("cnpg.io/cluster")
            operator_owned = (("Cluster", namespace, cluster) in by_name
                              and selector == {"cnpg.io/cluster": cluster, "cnpg.io/instanceRole": "primary"})
            require(selected or operator_owned, f"{owner}: selector matches no workload: {selector}")
            for port in spec.get("ports", []):
                target = port.get("targetPort", port["port"])
                if isinstance(target, str):
                    for pod, workload in selected:
                        require(any(value.get("name") == target and value.get("protocol", "TCP") == port.get("protocol", "TCP")
                                    for container in pod.get("containers", []) for value in container.get("ports", [])),
                                f"{owner}: named port {target} missing on {workload}")
        if kind == "Ingress":
            require(spec.get("ingressClassName") == "traefik", f"{owner}: unexpected ingress controller")
            backends = [path["backend"] for rule in spec.get("rules", []) for path in rule.get("http", {}).get("paths", [])]
            if "defaultBackend" in spec:
                backends.append(spec["defaultBackend"])
            for backend in backends:
                if "service" in backend:
                    service = backend["service"]
                    target = reference("Service", namespace, service["name"], owner)
                    port = service["port"]
                    require(any(value.get("name") == port["name"] if "name" in port else value["port"] == port["number"]
                                for value in target["spec"].get("ports", [])), f"{owner}: missing Service port {service}")


def alert_policy(resources):
    """Every alert names an owner and links to its section of the runbook guide."""
    runbooks = ROOT / "docs/guides/alert-runbooks.md"
    anchors = {line[4:].strip().lower() for line in runbooks.read_text().splitlines() if line.startswith("### ")}
    for doc in resources:
        if doc["kind"] != "ConfigMap" or "rules.yaml" not in doc.get("data", {}):
            continue
        for group in yaml.safe_load(doc["data"]["rules.yaml"])["groups"]:
            for rule in group["rules"]:
                if "alert" not in rule:
                    continue
                name = rule["alert"]
                require(rule.get("labels", {}).get("owner"), f"alert {name}: missing owner label")
                url = rule.get("annotations", {}).get("runbook_url", "")
                require(url.endswith("docs/guides/alert-runbooks.md#" + name.lower()), f"alert {name}: missing runbook_url")
                require(name.lower() in anchors, f"alert {name}: no section in {runbooks.relative_to(ROOT)}")


def lifecycle_policies(rendered, label):
    """Every Elasticsearch index.lifecycle.name is a policy that a rendered job or init container PUTs."""
    # One curl invocation or script line per call, so a GET or DELETE does not count.
    calls = re.split(r"\bcurl\s|\n", rendered.replace("\\\n", " "))
    stored = {name for call in calls if re.search(r'-X PUT\b|"PUT"', call)
              for name in re.findall(r"/_ilm/policy/([\w.-]+)", call)}
    named = set(re.findall(r'"index\.lifecycle\.name"\s*:\s*"([^"]+)"', rendered))
    require(named, f"{label}: no index.lifecycle.name found; update the lifecycle policy check")
    require(named <= stored, f"{label}: lifecycle policies never stored: {', '.join(sorted(named - stored))}")


def vulnerability_exceptions():
    """Each exception names one repository and one finding, an owner, a reason and a bounded expiry.
    Expiry is not compared with today: an expired entry only stops subtracting its finding."""
    path = ROOT / "k8s/files/vulnerability-exceptions.yaml"
    entries = yaml.safe_load(path.read_text()).get("exceptions") or []
    seen = set()
    for number, entry in enumerate(entries, 1):
        label = f"{path.relative_to(ROOT)} entry {number}"
        fields = {"image", "vulnerability", "owner", "triaged", "expires", "reason", "status"}
        require(set(entry) in (fields, fields | {"justification"}), f"{label}: unexpected fields")
        require(entry["status"] in ("not_affected", "affected"), f"{label}: status must be not_affected or affected")
        require((entry["status"] == "not_affected") == (entry.get("justification") in VEX_JUSTIFICATIONS),
                f"{label}: not_affected needs an OpenVEX justification, affected takes none")
        require(re.fullmatch(r"[a-z0-9]+([._/-][a-z0-9]+)*", str(entry["image"])),
                f"{label}: image is a repository without registry, tag or digest")
        require(re.fullmatch(r"(CVE-\d{4}-\d{4,}|GHSA(-[a-z0-9]{4}){3})", str(entry["vulnerability"])),
                f"{label}: vulnerability must be one CVE or GHSA ID")
        require(str(entry["owner"]).strip() and str(entry["reason"]).strip(), f"{label}: owner and reason are required")
        require(all(isinstance(entry[key], str) for key in ("triaged", "expires")), f"{label}: quote the dates")
        triaged, expires = (datetime.date.fromisoformat(entry[key]) for key in ("triaged", "expires"))
        require(0 <= (expires - triaged).days <= 92, f"{label}: expires within 92 days of its triage")
        key = (entry["image"], entry["vulnerability"])
        require(key not in seen, f"{label}: duplicate exception {key}")
        seen.add(key)


def prometheus_config(runtime, stage):
    """The rendered Prometheus configuration and rules pass promtool and the rule unit tests in tests/prometheus."""
    config = runtime[("v1", "ConfigMap", "infra", "prometheus-config")]["data"]
    for name, content in config.items():
        (stage / name).write_text(content)
    # Only syntax: the referenced rule and credential files exist in the pod, not here.
    run("promtool", "check", "config", "--syntax-only", str(stage / "prometheus.yml"))
    run("promtool", "check", "rules", str(stage / "rules.yaml"))
    exceptions = yaml.safe_load((ROOT / "k8s/files/vulnerability-exceptions.yaml").read_text())["exceptions"]
    recorded = [rule for group in yaml.safe_load(config["rules.yaml"])["groups"] for rule in group["rules"]
                if rule.get("record") == "swirl_cloud:vulnerability_exception"]
    require(len(recorded) == len(exceptions), "rendered rules miss vulnerability exceptions")
    for path in sorted((ROOT / "tests/prometheus").glob("*.yaml")):
        test = yaml.safe_load(path.read_text())
        test["rule_files"] = [str(stage / "rules.yaml")]
        (stage / path.name).write_text(yaml.safe_dump(test, sort_keys=False))
        run("promtool", "test", "rules", str(stage / path.name))


def argocd_probe_policy(contract):
    """The separately installed Argo CD release follows the probe policy; chart 10.9.2 defaults to 1-second probes."""
    require(contract["DEFAULT_ARGOCD_CHART_VERSION"] == "10.9.2", "Argo CD chart changed: recheck its probe defaults here")
    values = yaml.safe_load((ROOT / "config/argocd-values.yaml").read_text())
    default = {"timeoutSeconds": 1, "periodSeconds": 10, "failureThreshold": 3}
    for component, kinds in (("server", ("livenessProbe", "readinessProbe")), ("repoServer", ("livenessProbe", "readinessProbe")),
                             ("controller", ("readinessProbe",))):
        settings = values.get(component) or {}
        probe_policy(f"config/argocd-values.yaml: {component}",
                     {kind: {**default, **(settings.get(kind) or {})} for kind in kinds})


def ingress_policy(contract):
    """Entry-point middlewares of the separately installed Traefik release and its edge header policy."""
    def layer(base, override):
        # Helm merges maps across values files, replaces lists and drops nulls.
        result = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = layer(result[key], value)
            elif value is None:
                result.pop(key, None)
            else:
                result[key] = value
        return result

    # The zone setting configure-cloudflare.sh applies; configure-ingress.sh renders its max-age (0 when disabled).
    require(re.fullmatch(r"[0-9]+", contract.get("DEFAULT_CLOUDFLARE_HSTS_MAX_AGE", "")), "Invalid default HSTS max-age")
    cloudflare = (ROOT / "scripts/configure-cloudflare.sh").read_text()
    payload = re.search(r"strict_transport_security:\{enabled:\$enabled,max_age:\$max_age,"
                        r"include_subdomains:(\$enabled|true|false),preload:(true|false),nosniff:(true|false)\}", cloudflare)
    require(payload and 'ENABLE_HSTS="${CLOUDFLARE_ENABLE_HSTS:-true}"' in cloudflare
            and 'HSTS_MAX_AGE="${CLOUDFLARE_HSTS_MAX_AGE:-$DEFAULT_CLOUDFLARE_HSTS_MAX_AGE}"' in cloudflare,
            "scripts/configure-cloudflare.sh: HSTS inputs or security_header payload not recognized")
    for path in ("scripts/configure-ingress.sh", "scripts/configure-deployment-environments.py"):
        source = (ROOT / path).read_text()
        require(all(name in source for name in ("__HSTS_MAX_AGE__", "CLOUDFLARE_ENABLE_HSTS", "DEFAULT_CLOUDFLARE_HSTS_MAX_AGE")),
                f"{path}: origin HSTS is not rendered from the Cloudflare inputs")
    headers = {"stsSeconds": "__HSTS_MAX_AGE__", "stsIncludeSubdomains": payload[1] != "false",
               "stsPreload": payload[2] == "true", "contentTypeNosniff": payload[3] == "true"}
    base = yaml.safe_load((ROOT / "config/traefik-values.yaml").read_text())
    ha = layer(base, yaml.safe_load((ROOT / "config/traefik-ha-values.yaml").read_text()))
    for profile, values in (("config/traefik-values.yaml", base), ("config/traefik-ha-values.yaml layered", ha)):
        # Traefik chart 41.6.0 probe defaults (timeouts of 2 seconds) apply where the values set none.
        require(contract["DEFAULT_TRAEFIK_CHART_VERSION"] == "41.6.0", "Traefik chart changed: recheck its probe defaults here")
        probe_policy(f"{profile}: traefik", {
            "livenessProbe": {"timeoutSeconds": 2, "periodSeconds": 10, "failureThreshold": 3, **(values.get("livenessProbe") or {})},
            "readinessProbe": {"timeoutSeconds": 2, **(values.get("readinessProbe") or {})}})
        for container in (values.get("deployment") or {}).get("additionalContainers") or []:
            probe_policy(f"{profile}: {container['name']}", container)
        defined = {doc["metadata"]["name"]: doc for doc in values.get("extraObjects") or []
                   if doc["kind"] == "Middleware" and doc["metadata"]["namespace"] == "infra"}
        for entry, port in (values.get("ports") or {}).items():
            for middleware in ((port or {}).get("http") or {}).get("middlewares") or []:
                name = re.fullmatch(r"infra-(.+)@kubernetescrd", middleware)
                require(name and name[1] in defined, f"{profile}: entry point {entry} uses undefined middleware {middleware}")
        chain = (((values.get("ports") or {}).get("websecure") or {}).get("http") or {}).get("middlewares") or []
        require(defined.get("security-headers", {}).get("spec") == {"headers": headers}
                and [m for m in chain if m.startswith(("infra-forwarded-headers@", "infra-security-headers@"))]
                == ["infra-forwarded-headers@kubernetescrd", "infra-security-headers@kubernetescrd"],
                f"{profile}: HTTPS origin headers differ from the Cloudflare edge policy")


def portal_translation():
    """Every portal description has French text in files/homepage-fr.yaml, and no French entry outlives its description."""
    english = set(re.findall(r"^\s+description: (.+)$", (ROOT / "k8s/platform/homepage.yaml").read_text(), re.M))
    french = yaml.safe_load((ROOT / "k8s/files/homepage-fr.yaml").read_text())
    require(english == set(french), "k8s/files/homepage-fr.yaml differs from the portal descriptions: "
            + ", ".join(sorted(english ^ set(french))))


def runner_priority(resources):
    """Every runner configuration names a rendered PriorityClass; Kubernetes rejects job pods otherwise."""
    classes = {doc["metadata"]["name"] for doc in resources if doc["kind"] == "PriorityClass"}
    for doc in resources:
        for name, content in (doc.get("data") or {}).items() if doc["kind"] == "ConfigMap" else ():
            if name.endswith(".toml") and "[runners.kubernetes]" in str(content):
                named = re.findall(r'^\s*priority_class_name = "([^"]+)"', content, re.M)
                require(named and set(named) <= classes,
                        f"ConfigMap {doc['metadata']['name']}: runners need priority_class_name of a rendered PriorityClass")
