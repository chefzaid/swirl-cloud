"""Run records for the playbooks, in the installer's format (scripts/lib/install-record.sh).

Each playbook run writes /var/lib/swirl-cloud/install-runs/<run>.jsonl: a start
event (playbook, revision, versions, host), one step per task and a finish
event with the result; ./install.sh --status shows the newest record. Records
hold task names and timings, never variables, arguments or results. Recording
is best effort and never stops a run; check-mode runs are not recorded.
"""
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from datetime import datetime, timezone

from ansible import context
from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: swirl_cloud_record
type: notification
short_description: Writes Swirl Cloud run records
description: Writes one JSON line per playbook start, task and finish under /var/lib/swirl-cloud/install-runs.
"""

KEEP = 50


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "notification"
    CALLBACK_NAME = "swirl_cloud_record"
    CALLBACK_NEEDS_ENABLED = False

    def __init__(self):
        super().__init__()
        self.directory = Path(os.environ.get("INSTALL_RECORD_DIR", "/var/lib/swirl-cloud/install-runs"))
        self.file = None
        self.started = int(time.time())

    def write(self, **event):
        if self.file is None:
            return
        event["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        event["epoch"] = int(time.time())
        try:
            with self.file.open("a") as handle:
                handle.write(json.dumps(event) + "\n")
        except OSError:
            self.file = None

    def run(self, *command):
        """Runs a file command directly, or through passwordless sudo when the directory is root's."""
        for prefix in ([], ["sudo", "-n"]):
            try:
                if subprocess.run(prefix + list(command), capture_output=True, timeout=30,
                                  stdin=subprocess.DEVNULL).returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                pass
        return False

    def create(self, run):
        target = self.directory / f"{run}.jsonl"
        if not (self.run("install", "-d", "-m", "0755", str(self.directory))
                and self.run("install", "-m", "0644", "-o", str(os.getuid()), "-g", str(os.getgid()), "/dev/null", str(target))):
            self._display.warning(f"Could not create the run record in {self.directory}; continuing without it.")
            return None
        for old in sorted(self.directory.glob("*.jsonl"), reverse=True)[KEEP:]:
            self.run("rm", "-f", "--", str(old))
        return target

    def v2_playbook_on_start(self, playbook):
        if context.CLIARGS.get("check"):
            return
        root = Path(playbook._basedir).resolve().parent
        settings = {}
        try:
            settings = dict(line.split("=", 1) for line in (root / "config/platform.env").read_text().splitlines()
                            if "=" in line and not line.startswith("#"))
        except OSError:
            pass
        try:
            revision = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                                      text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = ""
        try:
            chart = next((line.split()[1] for line in (root / "k8s/Chart.yaml").read_text().splitlines()
                          if line.startswith("version:")), "")
        except OSError:
            chart = ""
        try:
            os_name = next((line.split("=", 1)[1].strip().strip('"') for line in Path("/etc/os-release").read_text().splitlines()
                            if line.startswith("PRETTY_NAME=")), "")
        except OSError:
            os_name = ""
        name = Path(playbook._file_name).stem
        self.file = self.create(f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}")
        self.write(event="start", mode=f"ansible:{name}", started=str(self.started), revision=revision or "unknown",
                   chart=chart, k3s=settings.get("DEFAULT_K3S_INSTALL_VERSION", ""), os=os_name, host=platform.node())

    def v2_playbook_on_task_start(self, task, is_conditional):
        self.write(event="step", name=task.get_name())

    def v2_playbook_on_handler_task_start(self, task):
        self.write(event="step", name=task.get_name())

    def v2_playbook_on_stats(self, stats):
        hosts = stats.processed.keys()
        failed = any(stats.summarize(host)["failures"] for host in hosts)
        unreachable = any(stats.summarize(host)["unreachable"] for host in hosts)
        result, code = ("failed", 2) if failed else ("failed", 4) if unreachable else ("success", 0)
        self.write(event="finish", result=result, exit_code=str(code), seconds=str(int(time.time()) - self.started))
