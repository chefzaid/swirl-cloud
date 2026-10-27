#!/usr/bin/env python3
"""French console text for the installer engine and the verification report.

English stays the source and the default; SWIRL_CLOUD_LANG=fr (set by
./install.sh --lang fr) switches the installer's [STEP]/[INFO]/[WARN]/[ERROR]
messages and the ./install.sh --status report to French. Each table under i18n/
maps an English message to French, one tab-separated pair per line; shell-style
variables ($name, ${...}, $(...)) stand for values and are carried over. A message
without an entry stays in English, so a missing entry never hides information.
JSON output and run records stay in English.

  console_i18n.py MESSAGE    print an installer message in the selected language
"""
import os
from pathlib import Path
import re
import sys

TABLES = Path(__file__).resolve().parent / "i18n"
TOKEN = re.compile(r"\$\(\([^)]*\)\)|\$\([^)]*\)|\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*|\$[0-9@*#?]")
BASH_ESCAPE = re.compile(r'\\([\\"$`])')
_loaded = {}


def language():
    return (os.environ.get("SWIRL_CLOUD_LANG") or "en").lower()[:2]


def unescape(text):
    """The text a double-quoted shell string prints; harmless for Python-side tables."""
    return BASH_ESCAPE.sub(r"\1", text)


def load(name):
    """Exact entries and value patterns of i18n/<name>.fr.tsv, the most specific pattern first."""
    if name not in _loaded:
        exact, patterns = {}, []
        for line in (TABLES / f"{name}.fr.tsv").read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            english, french = line.split("\t", 1)
            literals, tokens = TOKEN.split(english), TOKEN.findall(english)
            if not tokens:
                exact[unescape(english)] = unescape(french)
            elif any(part.strip() for part in literals):  # A bare "$VAR" would match every message.
                regex = "(.*?)".join(re.escape(unescape(part)) for part in literals)
                patterns.append((len("".join(literals)), re.compile(regex, re.S), tokens, french))
        patterns.sort(key=lambda pattern: -pattern[0])
        _loaded[name] = exact, [pattern[1:] for pattern in patterns]
    return _loaded[name]


def translate(text, name):
    if language() != "fr" or not text:
        return text
    exact, patterns = load(name)
    if text in exact:
        return exact[text]
    for regex, tokens, french in patterns:
        match = regex.fullmatch(text)
        if match:
            values = {}
            for token, value in zip(tokens, match.groups()):
                values.setdefault(token, value)
            out, end = [], 0
            for token in TOKEN.finditer(french):
                out += [unescape(french[end:token.start()]), values.get(token[0], token[0])]
                end = token.end()
            return "".join(out) + unescape(french[end:])
    return text


if __name__ == "__main__":
    print(translate(" ".join(sys.argv[1:]), "installer"))
