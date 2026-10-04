"""Read-only rerun plan: what is installed, missing, drifted or failed, and what
the next installer run with the given inputs will do.

Inputs are the installation values (from the input file) and the checked-in
config/platform.env pins, which are parsed as data. Nothing here changes state.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess

from install_inputs import flag

INSTALLED, MISSING, DRIFT, FAILED, SKIPPED = "installed", "missing", "drift", "failed", "skipped"


def platform_contract(root):
    values = {}
    for line in (Path(root) / "config/platform.env").read_text().splitlines():
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line.strip())
        if match:
            values[match[1]] = match[2].strip().strip('"').strip("'")
    return values


def run(*args, timeout=30):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return result.returncode, result.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def cluster_reachable():
    return shutil.which("kubectl") is not None and run("kubectl", "--request-timeout=10s", "get", "--raw", "/readyz")[0] == 0


def helm_releases():
    code, output = run("helm", "list", "-A", "-o", "json")
    try:
        return {r["name"]: r for r in json.loads(output)} if code == 0 else {}
    except ValueError:
        return {}


def app_ready(namespace, app):
    code, output = run("kubectl", "--request-timeout=15s", "get", "pods", "-n", namespace, "-l", f"app={app}", "-o", "json")
    if code:
        return None
    pods = json.loads(output)["items"]
    if not pods:
        return False
    return all(p["status"].get("phase") == "Running" and all(s.get("ready") for s in p["status"].get("containerStatuses", []))
               for p in pods)


def selected(values, name):
    """The installer's default for components is 'install'; an explicit false skips them."""
    return flag(values, name, "true")


def plan(root, values, state_dir):
    contract = platform_contract(root)
    rows = []
    reachable = cluster_reachable()
    releases = helm_releases() if reachable and shutil.which("helm") else {}

    tools = [tool for tool in ("ansible", "jq", "kubectl", "helm", "shellcheck", "sqlite3") if not shutil.which(tool)]
    rows.append(("System prerequisites", INSTALLED if not tools else MISSING,
                 "all tools present" if not tools else "missing: " + ", ".join(tools),
                 "update" if selected(values, "INSTALL_PREREQS") else "keep"))

    k3s = run("systemctl", "is-active", "k3s")[1].strip() == "active"
    pinned = contract.get("DEFAULT_K3S_INSTALL_VERSION", "")
    version = ""
    if reachable:
        code, output = run("kubectl", "--request-timeout=15s", "get", "nodes", "-o", "json")
        nodes = json.loads(output)["items"] if code == 0 else []
        versions = sorted({n["status"]["nodeInfo"]["kubeletVersion"] for n in nodes})
        version = ", ".join(versions)
        control = sum(1 for n in nodes if "node-role.kubernetes.io/control-plane" in n["metadata"].get("labels", {}))
        planned_control = int(values.get("CONTROL_PLANE_COUNT") or max(control, 1))
        planned_total = int(values.get("CLUSTER_NODE_COUNT") or max(len(nodes), planned_control))
        add = max(planned_total - len(nodes), 0)
        rows.append(("Cluster nodes", INSTALLED if add == 0 else MISSING,
                     f"{len(nodes)} registered ({control} control plane); planned {planned_total} ({planned_control})",
                     f"enroll {add} node(s)" if add else "keep"))
    if k3s:
        state = INSTALLED if not pinned or version in ("", pinned) else DRIFT
        detail = f"{version or 'running'}" + ("" if state == INSTALLED else f"; pinned {pinned} (reruns do not upgrade K3s)")
        rows.insert(1, ("K3s control plane", state, detail,
                        "reinstall" if values.get("INSTALL_K3S", "").lower() in ("true", "yes", "1", "y") else "keep"))
    else:
        rows.insert(1, ("K3s control plane", MISSING, "not installed on this host", "install"))

    for name, component, pin, release, namespace in (
            ("Longhorn storage", "INSTALL_LONGHORN", "DEFAULT_LONGHORN_CHART_VERSION", "longhorn", "longhorn-system"),
            ("Traefik ingress", "INSTALL_INGRESS", "DEFAULT_TRAEFIK_CHART_VERSION", "traefik", "infra"),
            ("Vault", "INSTALL_VAULT_STACK", "DEFAULT_VAULT_CHART_VERSION", "vault", "infra"),
            ("External Secrets", "INSTALL_VAULT_STACK", "DEFAULT_EXTERNAL_SECRETS_CHART_VERSION", "external-secrets", "infra"),
            ("Argo CD", "INSTALL_ARGOCD", "DEFAULT_ARGOCD_CHART_VERSION", "argocd", "infra")):
        current = releases.get(release)
        want = selected(values, component)
        if not current:
            rows.append((name, MISSING if reachable else SKIPPED, "not installed" if reachable else "cluster not reachable",
                         "install" if want else "skip"))
            continue
        chart_version = current["chart"].rsplit("-", 1)[-1]
        if current.get("status") != "deployed":
            state, detail = FAILED, f"Helm release {current.get('status')}"
        elif chart_version != contract.get(pin, chart_version):
            state, detail = DRIFT, f"chart {chart_version}; pinned {contract.get(pin)}"
        else:
            state, detail = INSTALLED, f"chart {chart_version}"
        rows.append((name, state, detail, ("upgrade" if state == DRIFT else "reconcile") if want else "skip"))

    for name, component, apps, namespace in (
            ("Data stores", "DEPLOY_DATA_STORES", contract.get("DATASTORE_WAIT_APPS", ""), "infra"),
            ("Platform services", "DEPLOY_PLATFORM_SERVICES", contract.get("PLATFORM_WAIT_APPS", ""), "infra"),
            ("Odoo", None, "odoo", "corp")):
        if component is None and (values.get("INSTALL_SCOPE") or "apps") != "apps":
            rows.append((name, SKIPPED, "scope infra", "skip"))
            continue
        if not reachable:
            rows.append((name, SKIPPED, "cluster not reachable", "install"))
            continue
        status = {app: app_ready(namespace, app) for app in filter(None, apps.split(","))}
        missing = [app for app, ready in status.items() if ready is False]
        state = INSTALLED if not missing else MISSING if len(missing) == len(status) else FAILED
        detail = f"{len(status) - len(missing)}/{len(status)} ready" + (": not ready " + ", ".join(missing) if missing and state == FAILED else "")
        rows.append((name, state, detail, "reconcile" if component is None or selected(values, component) else "skip"))

    if k3s:
        code, output = run(str(Path(root) / "scripts/configure-k3s-audit.sh"), "--check")
        audit = output.strip() or "unknown"
        rows.append(("API audit logging", {"active": INSTALLED, "missing": MISSING}.get(audit, DRIFT),
                     {"active": "writing /var/log/kubernetes/audit/audit.log",
                      "pending-restart": "configured; starts at the next K3s restart",
                      "configured": "configured; not writing yet",
                      "missing": "not configured"}.get(audit, "state unknown (needs passwordless sudo to read)"),
                     "configure (restart from Maintenance)" if audit != "active" else "keep"))

    exposure = values.get("SERVER_EXPOSURE") or "internet"
    cloudflare = flag(values, "CONFIGURE_CLOUDFLARE", "true" if exposure == "internet" else "false")
    rows.append(("Cloudflare DNS, TLS, Access", INSTALLED if cloudflare else SKIPPED,
                 "reconciled on every run" if cloudflare else "local exposure or disabled", "reconcile" if cloudflare else "skip"))
    timer = run("systemctl", "is-enabled", "swirl-cloud-k3s-backup.timer")[1].strip() == "enabled"
    offsite = flag(values, "CONFIGURE_OFFSITE_BACKUPS")
    rows.append(("Backups", INSTALLED if timer else MISSING, ("nightly timer enabled" if timer else "no nightly timer") +
                 ("; off-node S3 selected" if offsite else "; local only"), "reconcile"))

    if reachable:
        code, output = run("python3", str(Path(root) / "scripts/platform-identity.py"), "--discover", timeout=60)
        try:
            installed = json.loads(output) if code == 0 else {}
        except ValueError:
            installed = {}
        changed = sorted(k for k, v in installed.items() if values.get(k) and values[k] != v)
        rows.append(("Installation identity", DRIFT if changed else INSTALLED if installed else MISSING,
                     ("differs: " + ", ".join(changed) + " - renames are migrations, not reruns") if changed else
                     ("matches the input file" if values else "recorded; no input values to compare") if installed
                     else "not recorded yet",
                     "review first" if changed else "keep"))

    last = Path(state_dir) / "last-run.json"
    if last.exists():
        record = json.loads(last.read_text())
        rows.append(("Last run", INSTALLED if record.get("code") == 0 else FAILED,
                     f"{record.get('title')} {record.get('finished')} exit {record.get('code')}; log {record.get('log')}",
                     "resume by rerunning (stages are idempotent)" if record.get("code") else "-"))
    return rows


def render(rows):
    width = max(len(row[0]) for row in rows)
    lines = [f"{'Stage'.ljust(width)}  {'State'.ljust(9)}  Next run", "-" * (width + 30)]
    for stage, state, detail, action in rows:
        lines.append(f"{stage.ljust(width)}  {state.ljust(9)}  {action}")
        lines.append(f"{''.ljust(width)}  {''.ljust(9)}  {detail}")
    counts = {state: sum(row[1] == state for row in rows) for state in (MISSING, DRIFT, FAILED)}
    lines += ["", f"{counts[MISSING]} missing, {counts[DRIFT]} drifted, {counts[FAILED]} failed. "
              "Reruns are idempotent: Install reconciles every selected stage and resumes after an interruption. "
              "Turn components off under Advanced > Components to skip them."]
    return "\n".join(lines)
