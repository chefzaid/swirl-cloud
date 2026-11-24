#!/usr/bin/env python3
"""Guards for the setup hub's French translation: English passes through
unchanged, whole texts and single lines translate, lines with values keep their
values and indentation, an unknown line stays in English, and every entry is
still used by the hub (a renamed English text would otherwise silently fall back
to English)."""
import ast
import importlib
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import tui_i18n  # noqa: E402

SOURCES = [ROOT / "scripts/install-tui.py", *sorted((ROOT / "scripts/lib").glob("*.py"))]


def hub_strings():
    """(strings, f-string patterns) the hub's Python sources hold, adjacent literals joined as
    Python joins them; an f-string's values match anything."""
    found, patterns = [], []
    for path in SOURCES:
        if path.name == "tui_i18n.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append(node.value)
            elif isinstance(node, ast.JoinedStr):
                literal = [part.value for part in node.values if isinstance(part, ast.Constant)]
                if sum(len(text) for text in literal) < 20:
                    continue  # mostly values: as a pattern it would match any entry
                patterns.append(re.compile("".join(re.escape(part.value) if isinstance(part, ast.Constant) else ".+?"
                                                   for part in node.values), re.S))
    return found, patterns


class Translation(unittest.TestCase):
    def setUp(self):
        self.addCleanup(tui_i18n.set_language, tui_i18n.LANGUAGE)

    def test_english_is_unchanged(self):
        tui_i18n.set_language("en")
        for text in ("Yes", "Install or update the platform", "anything\nat all", ""):
            self.assertEqual(text, tui_i18n.t(text))

    def test_language_is_shared_with_the_engine(self):
        tui_i18n.set_language("FR-fr")
        self.assertEqual(("fr", "fr"), (tui_i18n.LANGUAGE, tui_i18n.os.environ["SWIRL_CLOUD_LANG"]))
        self.assertEqual("Oui", tui_i18n.t("Yes"))

    def test_lines_translate_and_keep_values_and_indentation(self):
        tui_i18n.set_language("fr")
        text = "Install or update the platform now?\n\n  K3s on this host:  installed\nFull log: /var/log/x.log\nNot a known line"
        lines = tui_i18n.t(text).split("\n")
        self.assertEqual("  K3s sur ce serveur :  installé", lines[2])
        self.assertEqual("Journal complet : /var/log/x.log", lines[3])
        self.assertEqual("Not a known line", lines[4])
        self.assertEqual("", lines[1])
        self.assertEqual("Confirmer : Oui", tui_i18n.translate_line("Confirm: Yes"))
        self.assertEqual("Confirmer : something new", tui_i18n.translate_line("Confirm: something new"))
        self.assertEqual("L'installation s'est arrêtée (code 3). Dernières lignes :",
                         tui_i18n.translate_line("The installation stopped (exit code 3). Last lines:"))
        self.assertEqual("Déployer shop vers prod", tui_i18n.translate_line("Deploy shop to prod"))

    def test_patterns_are_whole_line_and_their_templates_use_existing_groups(self):
        for pattern, template in tui_i18n.PATTERNS:
            groups = re.compile(pattern).groups
            used = {int(n) for n in re.findall(r"\\(\d)", template)}
            with self.subTest(pattern=pattern):
                self.assertTrue(used <= set(range(1, groups + 1)), "the template names a group the pattern lacks")
                self.assertEqual(groups, len(used), "a captured value is dropped from the translation")

    def test_every_entry_is_used_by_the_hub(self):
        strings, patterns = hub_strings()
        lines = {line.strip() for text in strings for line in text.split("\n")} | set(strings)
        shell = "".join(path.read_text() for path in (ROOT / "scripts").rglob("*.sh"))
        unused = [key for key in tui_i18n.FR if key not in lines and key not in shell
                  and not any(key in text for text in strings) and not any(p.search(key) for p in patterns)]
        self.assertEqual([], unused)

    def test_translations_are_not_empty_or_english_copies_of_long_texts(self):
        for english, french in tui_i18n.FR.items():
            with self.subTest(english=english):
                self.assertTrue(french.strip())
                if len(english) > 40:
                    self.assertNotEqual(english, french)

    def test_the_module_reads_the_language_from_the_environment(self):
        saved = tui_i18n.os.environ.get("SWIRL_CLOUD_LANG")
        try:
            tui_i18n.os.environ["SWIRL_CLOUD_LANG"] = "fr_FR.UTF-8"
            self.assertEqual("fr", importlib.reload(tui_i18n).LANGUAGE)
            tui_i18n.os.environ.pop("SWIRL_CLOUD_LANG")
            self.assertEqual("en", importlib.reload(tui_i18n).LANGUAGE)
        finally:
            if saved is None:
                tui_i18n.os.environ.pop("SWIRL_CLOUD_LANG", None)
            else:
                tui_i18n.os.environ["SWIRL_CLOUD_LANG"] = saved
            importlib.reload(tui_i18n)


if __name__ == "__main__":
    unittest.main()
