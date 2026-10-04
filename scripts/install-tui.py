#!/usr/bin/env python3
"""Guided installer front end.

Walks the installation-input schema in whiptail dialogs and writes a 0600 input
file for `install-control-plane.sh --env-file`. Express mode asks only for what
a default installation needs; Advanced walks every input grouped by section.
Rerunning loads the existing file, so earlier answers become the defaults.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
from install_inputs import (INPUTS, SECTIONS, TRUE, applies, check_value, parse,  # noqa: E402
                            read, InputError, validate, write, VERSION_KEY, SCHEMA_VERSION)

TITLE = "Swirl Cloud installer"
DEFAULT_OUTPUT = Path.home() / ".config/swirl-cloud/install.env"
EXPRESS_SUMMARY = """Express installs the recommended platform on this server:

  - K3s, Longhorn storage and Traefik ingress
  - Vault and External Secrets
  - PostgreSQL, Kafka, Redis and MongoDB
  - Keycloak SSO, GitLab with CI and registry, Argo CD, SonarQube,
    observability, Homepage and Odoo
  - host firewall and intrusion prevention for internet-exposed servers

It asks for the company name and domain, the administrator login, the
Cloudflare token for internet-exposed servers, and optionally more servers,
S3 backups and GitHub repositories to import. Everything else uses defaults,
which you can change later under Advanced."""


class Cancelled(Exception):
    pass


class Dialog:
    def __init__(self):
        self.binary = shutil.which("whiptail")
        if not self.binary:
            raise SystemExit("whiptail is required: sudo apt-get install whiptail")
        columns, lines = shutil.get_terminal_size((80, 24))
        self.width, self.height = max(60, min(columns - 4, 90)), max(16, min(lines - 2, 24))

    def run(self, *box):
        read_end, write_end = os.pipe()
        try:
            result = subprocess.run([self.binary, "--title", TITLE, "--output-fd", str(write_end), *box],
                                    pass_fds=(write_end,))
        finally:
            os.close(write_end)
        with os.fdopen(read_end) as output:
            answer = output.read()
        return result.returncode, answer

    def message(self, text):
        self.run("--scrolltext", "--msgbox", text, str(self.height), str(self.width))

    def yes_no(self, text, default):
        args = ["--yesno", text, "12", str(self.width)]
        code, _ = self.run(*([] if default else ["--defaultno"]), *args)
        if code == 255:
            raise Cancelled
        return code == 0

    def text(self, prompt, initial=""):
        code, answer = self.run("--inputbox", prompt, "12", str(self.width), initial)
        if code:
            raise Cancelled
        return answer.strip()

    def password(self, prompt):
        # Secrets are never passed as arguments, so they cannot appear in the process list.
        code, answer = self.run("--passwordbox", prompt, "10", str(self.width))
        if code:
            raise Cancelled
        return answer

    def menu(self, prompt, items, default=None):
        flat = [part for tag, label in items for part in (tag, label)]
        extra = ["--default-item", default] if default else []
        code, answer = self.run(*extra, "--menu", prompt, str(self.height), str(self.width),
                                str(min(len(items), self.height - 8)), *flat)
        if code:
            raise Cancelled
        return answer


def github_remote():
    for remote in ("github", "origin"):
        result = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", remote], capture_output=True, text=True)
        url = result.stdout.strip()
        url = url.replace("git@github.com:", "https://github.com/").replace("ssh://git@github.com/", "https://github.com/")
        if result.returncode == 0 and url.startswith("https://github.com/") and url.endswith(".git"):
            return url
    return ""


def describe(item):
    return item.prompt + (f"\n\n{item.help}" if item.help else "")


def ask(dialog, item, values):
    """Ask one input until its value is valid; an empty answer leaves it unset."""
    current = values.get(item.name, "")
    while True:
        if item.kind == "bool":
            value = "true" if dialog.yes_no(describe(item), (current or item.default or "false").lower() in TRUE) else "false"
        elif item.kind == "choice":
            value = dialog.menu(describe(item), [(choice, "") for choice in item.choices], current or item.default or None)
        elif item.secret:
            keep = "\n\nLeave empty to keep the current value." if current else ""
            value = dialog.password(describe(item) + keep)
            if not value and current:
                return
            if value and dialog.password("Confirm: " + item.prompt) != value:
                dialog.message("The values do not match. Try again.")
                continue
        else:
            value = dialog.text(describe(item) + ("\n\nLeave empty for the default." if not item.required else ""),
                                current or item.default)
        error = check_value(item, value) if value else None
        if error:
            dialog.message(f"{item.prompt}: {error}.")
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


def express(dialog, values):
    dialog.message(EXPRESS_SUMMARY)
    values.setdefault("GITOPS_REPOSITORY_URL", github_remote())
    for section in SECTIONS:
        items = [item for item in INPUTS if item.section == section and item.express]
        if section == "Nodes":
            if not dialog.yes_no("Add more servers to this cluster now?\n\nChoose No for a single-server "
                                 "installation; add-node.sh can enroll servers later.",
                                 int(values.get("CLUSTER_NODE_COUNT") or 1) > 1):
                for item in items:
                    values.pop(item.name, None)
                continue
        walk(dialog, values, items)


def advanced(dialog, values):
    while True:
        choice = dialog.menu("Choose a section. Each walks every input that applies to your answers.",
                             [(str(number), section) for number, section in enumerate(SECTIONS, 1)] + [("back", "Return")])
        if choice == "back":
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(SECTIONS):
            continue
        try:
            walk(dialog, values, [item for item in INPUTS if item.section == SECTIONS[int(choice) - 1]])
        except Cancelled:
            continue


def summary(values):
    lines = []
    for section in SECTIONS:
        entries = [(item, values[item.name]) for item in INPUTS if item.section == section and values.get(item.name)]
        if entries:
            lines.append(f"[{section}]")
            lines += [f"  {item.name} = {'(set)' if item.secret else value}" for item, value in entries]
    problems = validate({**values, VERSION_KEY: str(SCHEMA_VERSION)})
    lines += ["", "Problems:" if problems else "No problems found in the schema checks."] + [f"  - {p}" for p in problems]
    return "\n".join(lines)


def save(dialog, values, output):
    problems = validate({**values, VERSION_KEY: str(SCHEMA_VERSION)})
    if problems:
        dialog.message("Fix these before saving:\n\n" + "\n".join(f"- {p}" for p in problems))
        return False
    write(output, values)
    check = subprocess.run([str(ROOT / "install-control-plane.sh"), "--env-file", str(output), "--validate-only"],
                           capture_output=True, text=True)
    report = (check.stdout + check.stderr).replace("\x1b[0;31m", "").replace("\x1b[0;32m", "").replace("\x1b[0m", "")
    if check.returncode:
        dialog.message(f"Saved {output}, but the installer rejected it:\n\n{report}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"input file to write (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if ROOT in output.parents:
        raise SystemExit("Keep the input file outside the repository checkout; it contains credentials.")
    values = {}
    if output.exists():
        try:
            values = read(output)
        except InputError:
            # Reuse what parses; the review screen lists the remaining problems.
            values = {key: value for key, value in parse(output.read_text())[0].items() if key != VERSION_KEY}
    dialog = Dialog()
    while True:
        try:
            choice = dialog.menu(f"Input file: {output}", [
                ("express", "Express: recommended platform, few questions"),
                ("advanced", "Advanced: every installer input by section"),
                ("review", "Review answers and problems"),
                ("save", "Save, validate and optionally start the installation"),
                ("quit", "Quit without saving")], "express" if not values else "review")
        except Cancelled:
            return
        try:
            if choice == "express":
                express(dialog, values)
            elif choice == "advanced":
                advanced(dialog, values)
            elif choice == "review":
                dialog.message(summary(values))
            elif choice == "save" and save(dialog, values, output):
                if dialog.yes_no(f"Saved and validated {output}.\n\nStart the installation now?", False):
                    os.execv(str(ROOT / "install-control-plane.sh"),
                             [str(ROOT / "install-control-plane.sh"), "--env-file", str(output)])
                print(f"Saved {output}. Install later with:\n  ./install-control-plane.sh --env-file {output}")
                return
            elif choice == "quit":
                return
        except Cancelled:
            continue


if __name__ == "__main__":
    main()
