#!/usr/bin/env python3
"""Guards for render-identity.py, the installer's staging renderer: every
placeholder is replaced (JSON-quoted where the manifest expects JSON), the
portal switches to French when asked, values.yaml gets the identity, and a file
with an unknown placeholder stops the render."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_identity", ROOT / "scripts/render-identity.py")
render_identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_identity)
SETTINGS = {key: "" for key in render_identity.identity.FIELDS}
SETTINGS.update(PLATFORM_DOMAIN="example.com", INTERNAL_DNS_ZONE="svc.example.internal", ORGANIZATION_NAME='Acme "Labs"',
                ORGANIZATION_SLUG="acme", GITLAB_GROUP_PATH="acme", GITLAB_GROUP_NAME="Acme", GITLAB_PROJECT_NAME="swirl-cloud",
                KEYCLOAK_REALM="acme", TLS_SECRET_NAME="acme-tls", PORTAL_LANGUAGE="fr", INSTALL_APPS="true", INSTALL_DESCHEDULER="false")


class RenderIdentity(unittest.TestCase):
    def stage(self, extra=None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for relative in ("k8s/files/homepage-fr.yaml", "k8s/files/homepage-custom.js", "k8s/values.yaml"):
            (directory / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / relative, directory / relative)
        (directory / "k8s/platform").mkdir(parents=True)
        (directory / "config").mkdir()
        (directory / "k8s/platform/homepage.yaml").write_text(
            "title: __PLATFORM_TITLE_JSON__\nhost: https://gitlab.__PUBLIC_DOMAIN__\n    language: en\n"
            "            description: Ask IT for help or for access to a service\n")
        for name, text in (extra or {}).items():
            (directory / name).write_text(text)
        return directory

    def test_placeholders_french_portal_and_values(self):
        directory = self.stage()
        with mock.patch.dict(os.environ, SETTINGS):
            render_identity.render(directory)
        homepage = (directory / "k8s/platform/homepage.yaml").read_text()
        self.assertIn('title: "Acme \\"Labs\\" Cloud"', homepage)
        self.assertIn("host: https://gitlab.example.com", homepage)
        self.assertIn("    language: fr\n", homepage)
        self.assertIn("description: Demander de l'aide à l'informatique ou l'accès à un service", homepage)
        self.assertIn('const language = "fr";', (directory / "k8s/files/homepage-custom.js").read_text())
        values = yaml.safe_load((directory / "k8s/values.yaml").read_text())
        self.assertEqual(("example.com", "acme", True, False),
                         (values["publicDomain"], values["keycloakRealm"], values["appsEnabled"], values["deschedulerEnabled"]))

    def test_an_unknown_placeholder_stops_the_render(self):
        directory = self.stage({"config/extra.env": "VALUE=__NOT_A_FIELD__\n"})
        with mock.patch.dict(os.environ, SETTINGS), self.assertRaisesRegex(ValueError, "__NOT_A_FIELD__"):
            render_identity.render(directory)


if __name__ == "__main__":
    unittest.main()
