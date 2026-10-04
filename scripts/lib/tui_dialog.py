"""Thin whiptail wrapper for the installer TUI.

Answers travel through a private file descriptor. Secrets are never placed in
whiptail arguments: password boxes have no initial text, and text that contains
a secret is shown from a 0600 temporary file with --textbox.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

TITLE = "Swirl Cloud"


class Cancelled(Exception):
    """The operator pressed Cancel or Esc."""


class Dialog:
    def __init__(self, backtitle=""):
        self.binary = shutil.which("whiptail")
        if not self.binary:
            raise SystemExit("whiptail is required: run ./install.sh, or sudo apt-get install whiptail")
        self.backtitle = backtitle
        columns, lines = shutil.get_terminal_size((80, 24))
        self.width, self.height = max(60, min(columns - 4, 100)), max(16, min(lines - 2, 30))

    def run(self, *box):
        read_end, write_end = os.pipe()
        command = [self.binary, "--title", TITLE, "--output-fd", str(write_end)]
        if self.backtitle:
            command += ["--backtitle", self.backtitle]
        try:
            result = subprocess.run(command + list(box), pass_fds=(write_end,))
        finally:
            os.close(write_end)
        with os.fdopen(read_end) as output:
            return result.returncode, output.read()

    def message(self, text, height=None):
        self.run("--scrolltext", "--msgbox", text, str(height or self.height), str(self.width))

    def private(self, text):
        """Show text that contains a secret without putting it in a process argument."""
        with tempfile.TemporaryDirectory(prefix="swirl-cloud-tui-") as directory:
            path = Path(directory) / "message"
            path.write_text(text)
            path.chmod(0o600)
            self.run("--scrolltext", "--textbox", str(path), str(self.height), str(self.width))

    def yes_no(self, text, default=True, yes="Yes", no="No"):
        code, _ = self.run(*([] if default else ["--defaultno"]), "--yes-button", yes, "--no-button", no,
                           "--yesno", text, str(min(self.height, 8 + text.count("\n"))), str(self.width))
        if code == 255:
            raise Cancelled
        return code == 0

    def text(self, prompt, initial=""):
        code, answer = self.run("--inputbox", prompt, str(min(self.height, 10 + prompt.count("\n"))),
                                str(self.width), initial)
        if code:
            raise Cancelled
        return answer.strip()

    def password(self, prompt):
        code, answer = self.run("--passwordbox", prompt, str(min(self.height, 10 + prompt.count("\n"))), str(self.width))
        if code:
            raise Cancelled
        return answer

    def menu(self, prompt, items, default=None, cancel="Back"):
        flat = [part for tag, label in items for part in (tag, label)]
        extra = (["--default-item", default] if default else []) + ["--cancel-button", cancel]
        rows = min(len(items), self.height - 9 - prompt.count("\n"))
        code, answer = self.run(*extra, "--menu", prompt, str(self.height), str(self.width), str(max(rows, 3)), *flat)
        if code:
            raise Cancelled
        return answer
