#!/usr/bin/env python3
"""Swirl Cloud setup hub, started by ./install.sh.

Explains what will be installed, checks this host and the entered credentials,
collects the installation inputs (express or advanced) into a 0600 input file,
runs the installer engine with a log, verifies the result, and hands over to
node enrollment and repository import. Reruns load the saved file as defaults.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import string
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from install_inputs import (INPUTS, SCHEMA_VERSION, SECTIONS, TRUE, VERSION_KEY, InputError,  # noqa: E402
                            applies, check_value, parse, read, validate, write)
from tui_checks import FAIL, credential_checks, host_checks, render, unit_exists  # noqa: E402
from tui_dialog import Cancelled, Dialog  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "swirl-cloud"
DEFAULT_OUTPUT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "swirl-cloud/install.env"
ENGINE = ROOT / "install-control-plane.sh"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

OVERVIEW = """Swirl Cloud turns this server into a private cloud:

  Cluster     K3s, Longhorn storage, Traefik ingress, optional extra servers
  Security    Vault + External Secrets, Keycloak SSO, Cloudflare DNS/TLS/Access,
              host firewall, Fail2ban/CrowdSec on internet-exposed servers
  Data        PostgreSQL, Kafka, Redis and MongoDB shared by applications
  Delivery    GitLab with CI runners and registry, Argo CD, SonarQube
  Operations  Prometheus, Grafana, Elasticsearch/Kibana, Homepage portal,
              nightly backups (optionally encrypted in S3)
  Business    Odoo ERP (scope "apps")

Steps: Check this host -> Configure (express or advanced) -> Test credentials
-> Install. Answers are saved to the input file shown at the top; nothing
changes on the host until you choose Install."""

GUIDES = """Files and commands

  Input file     {output}
  Logs           {logs}
  Setup hub      ./install.sh
  Unattended     ./install.sh --env-file FILE
  Validate file  ./install.sh --env-file FILE --validate-only
  Status         ./install.sh --status

Guides

  docs/deployment.md                     installation, inputs, Ansible
  docs/guides/node-enrollment.md         adding servers, private transport
  docs/guides/repository-onboarding.md   importing and deploying repositories
  docs/operations.md                     day-2 operations, backups, incidents
  docs/security.md                       administrator sign-in, recovery credentials
  docs/dns.md                            Cloudflare token permissions"""


# ---------------------------------------------------------------- input file

def load_values(output):
    if output.exists():
        try:
            return read(output), "loaded from the input file"
        except InputError:
            values = {k: v for k, v in parse(output.read_text())[0].items() if k != VERSION_KEY}
            return values, "loaded from the input file (has problems; see Review)"
    try:
        result = subprocess.run([sys.executable, str(ROOT / "scripts/platform-identity.py"), "--discover"],
                                capture_output=True, text=True, timeout=60)
        installed = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        installed = {}
    known = {item.name for item in INPUTS}
    values = {key: value for key, value in installed.items() if key in known and value}
    return values, "identity loaded from the running installation" if values else "new configuration"


def problems_of(values):
    return validate({**values, VERSION_KEY: str(SCHEMA_VERSION)})


def generate_password(length=24):
    alphabet = string.ascii_letters + string.digits + "!#%+-=?@_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value)):
            return value


# ---------------------------------------------------------------- questions

def describe(item):
    return item.prompt + (f"\n\n{item.help}" if item.help else "")


def ask(dialog, item, values):
    """Ask one input until it is valid; an empty answer leaves it unset."""
    current = values.get(item.name, "")
    while True:
        if item.kind == "bool":
            value = "true" if dialog.yes_no(describe(item), (current or item.default or "false").lower() in TRUE) else "false"
        elif item.kind == "choice":
            value = dialog.menu(describe(item), [(choice, "") for choice in item.choices], current or item.default or None)
        elif item.secret:
            if item.name.endswith("PASSWORD"):
                options = [("enter", "Type a password"), ("generate", "Generate a strong password")]
                choice = dialog.menu(describe(item), options + ([("keep", "Keep the current value")] if current else []),
                                     "keep" if current else "enter")
                if choice == "keep":
                    return
                if choice == "generate":
                    value = generate_password()
                    dialog.private(f"{item.prompt}:\n\n    {value}\n\nRecord it in your password manager now. "
                                   "It is saved in the input file and not shown again.")
                    values[item.name] = value
                    return
            keep = "\n\nLeave empty to keep the current value." if current else ""
            value = dialog.password(describe(item) + keep)
            if not value and current:
                return
            if value and dialog.password("Confirm: " + item.prompt) != value:
                dialog.message("The values do not match. Try again.", 8)
                continue
        else:
            hint = "" if item.required else "\n\nLeave empty for the default."
            value = dialog.text(describe(item) + hint, current or item.default)
        error = check_value(item, value) if value else None
        if error:
            dialog.message(f"{item.prompt}: {error}.", 8)
            continue
        if value:
            values[item.name] = value
        else:
            values.pop(item.name, None)
        return


def walk(dialog, values, items):
    for item in items:
        if applies(item, values):
            ask(dialog, item, values)


def github_remote():
    for remote in ("github", "origin"):
        result = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", remote], capture_output=True, text=True)
        url = result.stdout.strip().replace("git@github.com:", "https://github.com/")
        url = url.replace("ssh://git@github.com/", "https://github.com/")
        if result.returncode == 0 and url.startswith("https://github.com/") and url.endswith(".git"):
            return url
    return ""


def express(dialog, values):
    dialog.message(OVERVIEW + "\n\nExpress asks only what a default installation needs. "
                   "Change anything later under Advanced.")
    if "GITOPS_REPOSITORY_URL" not in values and github_remote():
        values["GITOPS_REPOSITORY_URL"] = github_remote()
    for section in SECTIONS:
        items = [item for item in INPUTS if item.section == section and item.express]
        if section == "Nodes":
            more = int(values.get("CLUSTER_NODE_COUNT") or 1) > 1
            if not dialog.yes_no("Add more servers to this cluster during installation?\n\nChoose No for one "
                                 "server; you can add servers later from the main menu.", more):
                for item in items:
                    values.pop(item.name, None)
                continue
        walk(dialog, values, items)
    problems = problems_of(values)
    dialog.message("Express configuration complete." + (
        "\n\nStill missing or invalid:\n" + "\n".join(f"- {p}" for p in problems) if problems else
        "\n\nNext: Test credentials, then Install."), 16)


def advanced(dialog, values):
    while True:
        try:
            choice = dialog.menu("Each section walks every input that applies to your answers.",
                                 [(str(n), section) for n, section in enumerate(SECTIONS, 1)])
        except Cancelled:
            return
        if choice.isdigit() and 1 <= int(choice) <= len(SECTIONS):
            try:
                walk(dialog, values, [item for item in INPUTS if item.section == SECTIONS[int(choice) - 1]])
            except Cancelled:
                continue


def review(values):
    lines = []
    for section in SECTIONS:
        entries = [(item, values[item.name]) for item in INPUTS if item.section == section and values.get(item.name)]
        if entries:
            lines.append(f"[{section}]")
            lines += [f"  {item.name} = {'(set)' if item.secret else value}" for item, value in entries]
    problems = problems_of(values)
    lines += ["", "Problems:" if problems else "No problems found."] + [f"  - {p}" for p in problems]
    return "\n".join(lines)


# ---------------------------------------------------------------- actions

def run_logged(title, command, env=None):
    """Run a command on the terminal; its output is also saved to a private log."""
    logs = STATE / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = logs / f"{datetime.now():%Y%m%d-%H%M%S}-{title.lower().replace(' ', '-')}.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    subprocess.run(["clear"])
    print(f"== {title} ==\nLog: {log_path}\n", flush=True)
    with os.fdopen(descriptor, "wb") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env={**os.environ, **(env or {})})
        while chunk := os.read(process.stdout.fileno(), 4096):
            sys.stdout.buffer.write(chunk)
            sys.stdout.flush()
            log.write(ANSI.sub("", chunk.decode(errors="replace")).encode())
        code = process.wait()
    input(f"\n{title} finished with exit code {code}. Press Enter to return to the menu...")
    return code, log_path


def tail(path, lines=25):
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def save(dialog, values, output):
    problems = problems_of(values)
    if problems:
        dialog.message("Fix these first (Review lists them too):\n\n" + "\n".join(f"- {p}" for p in problems))
        return False
    write(output, values)
    check = subprocess.run([str(ENGINE), "--env-file", str(output), "--validate-only"], capture_output=True, text=True)
    if check.returncode:
        dialog.message(f"Saved {output}, but the installer rejected it:\n\n" + ANSI.sub("", check.stdout + check.stderr))
        return False
    return True


def verify_report():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/verify-platform.py")],
                            capture_output=True, text=True, timeout=600)
    return result.returncode, result.stdout + result.stderr


def install(dialog, values, output):
    if not save(dialog, values, output):
        return
    failures = [check for check in host_checks(ROOT, values) if check[0] == FAIL]
    if failures and not dialog.yes_no("This host has blocking problems:\n\n" + render(failures) +
                                      "\n\nInstall anyway?", False):
        return
    if not dialog.yes_no("Install or update the platform now?\n\nThis changes this host: packages, firewall, "
                         "K3s and every selected platform service. A first installation takes 30-60 minutes. "
                         "Reruns keep existing data.", False, "Install", "Cancel"):
        return
    code, log = run_logged("Install", [str(ENGINE), "--env-file", str(output)])
    if code:
        dialog.message(f"The installation stopped (exit code {code}). Last lines:\n\n{tail(log)}\n\nFull log: {log}")
        return
    status, report = verify_report()
    dialog.message(("Installation complete and verified.\n\n" if status == 0 else
                    "Installation finished, but verification found problems:\n\n") + report + f"\n\nLog: {log}")


def transport_environment(values):
    names = ("TAILSCALE_API_TOKEN", "TAILSCALE_TAILNET", "TAILSCALE_MESH_NAME", "OVH_API_ENDPOINT",
             "OVH_APPLICATION_KEY", "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY", "OVH_VRACK_SERVICE_NAME")
    return {name: values[name] for name in names if values.get(name)}


def add_servers(dialog, values):
    choice = dialog.menu("Add servers", [
        ("control-plane", "Enroll new control planes from this server over SSH"),
        ("worker", "Enroll new workers from this server over SSH"),
        ("join", "Join THIS machine to an existing cluster")])
    if choice == "join":
        role = dialog.menu("Role of this machine", [("worker", "Worker"), ("control-plane", "Control plane")])
        command = [str(ROOT / "add-node.sh"), "--role", role, "--mode", "local"]
    else:
        if not unit_exists("k3s.service"):
            dialog.message("Install the platform on this server first; it enrolls the new servers.", 8)
            return
        command = [str(ROOT / "add-node.sh"), "--role", choice, "--mode", "remote"]
        if values.get("K3S_NODE_TRANSPORT"):
            command += ["--transport", values["K3S_NODE_TRANSPORT"]]
    code, log = run_logged("Add servers", command, transport_environment(values))
    if code:
        dialog.message(f"Enrollment stopped (exit code {code}). Last lines:\n\n{tail(log)}\n\nLog: {log}")


def import_repositories(dialog, values):
    if not unit_exists("k3s.service"):
        dialog.message("Install the platform first; repositories are imported into its GitLab.", 8)
        return
    names = ("GITHUB_USERNAME", "GITHUB_ADMIN_TOKEN", "GITHUB_REPOSITORIES", "DEPLOY_REPOSITORIES")
    code, log = run_logged("Import repositories", [str(ROOT / "add-repos.sh")],
                           {name: values[name] for name in names if values.get(name)})
    if code:
        dialog.message(f"Repository import stopped (exit code {code}). Last lines:\n\n{tail(log)}\n\nLog: {log}")


def service_links(values):
    result = subprocess.run(["kubectl", "--request-timeout=20s", "get", "ingress", "-A", "-o", "json"],
                            capture_output=True, text=True)
    if result.returncode:
        return "The cluster API is not reachable from this account yet."
    rows = sorted({(i["metadata"]["namespace"], rule["host"]) for i in json.loads(result.stdout)["items"]
                   for rule in i["spec"].get("rules", []) if rule.get("host")})
    domain = values.get("PLATFORM_DOMAIN", "")
    lines = ([f"Portal: https://cloud.{domain}"] if domain else []) + ["Sign in with the platform administrator login.", ""]
    groups = {"infra": "Platform", "corp": "Corporate", "apps": "Applications"}
    for namespace, label in groups.items():
        hosts = [host for ns, host in rows if ns == namespace]
        if hosts:
            lines += [label] + [f"  https://{host}" for host in hosts]
    others = [f"  https://{host} ({ns})" for ns, host in rows if ns not in groups]
    return "\n".join(lines + (["Other environments"] + others if others else []))


def overview(values, origin, output):
    installed = unit_exists("k3s.service")
    nodes = subprocess.run(["kubectl", "--request-timeout=10s", "get", "nodes", "--no-headers"],
                           capture_output=True, text=True) if installed else None
    state = ["Current state",
             f"  K3s on this host:  {'installed' if installed else 'not installed'}",
             f"  Cluster nodes:     {len(nodes.stdout.splitlines()) if nodes and nodes.returncode == 0 else '-'}",
             f"  Input file:        {output} ({'saved' if output.exists() else 'not saved yet'})",
             f"  Answers:           {origin}",
             f"  Open problems:     {len(problems_of(values))}"]
    return OVERVIEW + "\n\n" + "\n".join(state)


# ---------------------------------------------------------------- hub

MENU = [("overview", "What gets installed and the current state"),
        ("preflight", "Check this host"),
        ("express", "Configure: express (few questions)"),
        ("advanced", "Configure: advanced (every input by section)"),
        ("credentials", "Test credentials (Cloudflare, GitHub, Tailscale)"),
        ("review", "Review answers and problems"),
        ("save", "Save the input file"),
        ("install", "Install or update the platform"),
        ("servers", "Add servers"),
        ("repositories", "Import and deploy GitHub repositories"),
        ("status", "Platform status and verification"),
        ("links", "Service links"),
        ("help", "Files, commands and guides"),
        ("quit", "Quit")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"input file (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if ROOT in output.parents:
        raise SystemExit("Keep the input file outside the repository checkout; it contains credentials.")
    values, origin = load_values(output)
    dialog = Dialog(f"Swirl Cloud setup  |  {output}")
    default = "status" if unit_exists("k3s.service") and output.exists() else "overview"
    while True:
        try:
            choice = dialog.menu("Choose a step. Answers are written only by Save or Install.", MENU, default, "Quit")
        except Cancelled:
            choice = "quit"
        default = choice
        try:
            if choice == "overview":
                dialog.message(overview(values, origin, output))
            elif choice == "preflight":
                dialog.message("Host checks\n\n" + render(host_checks(ROOT, values)))
            elif choice == "express":
                express(dialog, values)
                default = "credentials"
            elif choice == "advanced":
                advanced(dialog, values)
            elif choice == "credentials":
                dialog.message("Credential checks\n\n" + render(credential_checks(values)))
                default = "install"
            elif choice == "review":
                dialog.message(review(values))
            elif choice == "save":
                if save(dialog, values, output):
                    dialog.message(f"Saved and validated {output}.\n\nUnattended reruns:\n  ./install.sh --env-file {output}", 10)
            elif choice == "install":
                install(dialog, values, output)
                default = "status"
            elif choice == "servers":
                add_servers(dialog, values)
            elif choice == "repositories":
                import_repositories(dialog, values)
            elif choice == "status":
                dialog.message(verify_report()[1] if unit_exists("k3s.service") else
                               "The platform is not installed on this host yet.")
            elif choice == "links":
                dialog.message(service_links(values))
            elif choice == "help":
                dialog.message(GUIDES.format(output=output, logs=STATE / "logs"))
            elif choice == "quit":
                if values and not output.exists() and not problems_of(values) and \
                        dialog.yes_no("Save your answers before quitting?", True):
                    save(dialog, values, output)
                return
        except Cancelled:
            continue


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
