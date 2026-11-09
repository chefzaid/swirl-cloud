#!/usr/bin/env python3
"""Guards for the website's Odoo integration: scoped credentials never reach
process arguments, and only validated chat inputs reach Odoo and Cloudflare."""
import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("odoo_website", ROOT / "scripts/configure-odoo-website.py")
odoo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(odoo)


ORIGIN = "https://example.test"


class OdooWebsite(unittest.TestCase):
    def test_integration_names_and_stored_credentials_are_scoped_to_the_website(self):
        for application in ("bad/path", "Swirl Website", "x" * 32):
            with self.assertRaises(odoo.ServiceError):
                odoo.parameters(application, ORIGIN, None)
        # The origin is written into the chat frame's script and frame-ancestors.
        for origin in ("http://example.test", "https://example.test/", "https://example.test\";alert(1)//", "*"):
            with self.assertRaises(odoo.ServiceError):
                odoo.parameters("swirl-website", origin, None)
        for stored in ({"login": "integration-other", "apiKey": "a" * 40},
                       {"login": "integration-swirl-website", "apiKey": "bad"}):
            with self.assertRaises(odoo.ServiceError):
                odoo.parameters("swirl-website", ORIGIN, stored)

    def test_api_keys_never_reach_process_arguments_or_error_messages(self):
        stored, issued = "c" * 40, "d" * 40
        calls = []

        def run(args, input=None, **_kwargs):
            calls.append(args)
            if args[-1] == odoo.VERIFY:
                return subprocess.CompletedProcess(args, 0, "200 403\n", "")
            result = {"userId": 7, "company": "Org", "key": issued, "revoked": 1}
            return subprocess.CompletedProcess(args, 0, "log line\n" + odoo.MARKER + json.dumps(result) + "\n", "")

        with patch.object(odoo.subprocess, "run", side_effect=run):
            result = odoo.provision(odoo.parameters("swirl-website", ORIGIN, {
                "login": "integration-swirl-website", "apiKey": stored}, rotate=True))
            odoo.verify(result["key"])
        self.assertEqual(issued, result["key"])
        self.assertNotIn(stored, str(calls))
        self.assertNotIn(issued, str(calls))

        def fail(args, input=None, **_kwargs):
            return subprocess.CompletedProcess(args, 1, odoo.MARKER + json.dumps({"key": issued}),
                                               "Traceback " + stored + "\n" + odoo.FAILURE + "reason")

        with patch.object(odoo.subprocess, "run", side_effect=fail), self.assertRaises(odoo.ServiceError) as raised:
            odoo.provision(odoo.parameters("swirl-website", ORIGIN, None))
        self.assertTrue(str(raised.exception).endswith(": reason"))
        self.assertNotIn(issued, str(raised.exception))
        self.assertNotIn(stored, str(raised.exception))
        with patch.object(odoo.subprocess, "run", side_effect=lambda args, **_: subprocess.CompletedProcess(args, 0, "200 200\n", "")):
            with self.assertRaisesRegex(odoo.ServiceError, "must not read CRM records"):
                odoo.verify(issued)


    def chat_args(self, **overrides):
        values = {"chat_from": "chat@example.test", "notify": ["team@example.test"], "reply_sender": ["team@example.test"],
                  "notify_lang": "fr", "timezone": "Europe/Paris", "chat_persona": None}
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_chat_options_are_validated_before_reaching_odoo(self):
        for overrides in ({"notify": ["not an address"]}, {"reply_sender": ["Team <team@example.test>"]},
                          {"chat_from": "chat+x@example.test"}, {"timezone": "Mars/Olympus"}):
            with self.assertRaises(odoo.ServiceError):
                odoo.chat_parameters(self.chat_args(**overrides))
        params = odoo.chat_parameters(self.chat_args(), "re_secret")
        self.assertEqual(("chat@example.test", "re_secret", "Europe/Paris"),
                         (params["chatFrom"], params["smtpPassword"], params["timezone"]))

    def test_persona_files_need_messages_and_a_png_avatar(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "face.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
            (folder / "face.jpg").write_bytes(b"\xff\xd8\xff" + b"0" * 32)
            message = {"type": "text", "en": "Hi", "fr": "Bonjour"}
            for name, persona in (("ok", {"name": "Chloé", "avatar": "face.png", "messages": [message]}),
                                  ("jpeg", {"name": "Chloé", "avatar": "face.jpg", "messages": [message]}),
                                  ("step", {"name": "Chloé", "avatar": "face.png", "messages": [dict(message, type="forward")]})):
                (folder / f"{name}.json").write_text(json.dumps(persona))
            params = odoo.chat_parameters(self.chat_args(chat_persona=str(folder / "ok.json")))
            self.assertEqual("Chloé", params["persona"]["name"])
            self.assertTrue(base64.b64decode(params["persona"]["avatar"]).startswith(b"\x89PNG"))
            for name in ("jpeg", "step"):
                with self.assertRaises(odoo.ServiceError):
                    odoo.chat_parameters(self.chat_args(chat_persona=str(folder / f"{name}.json")))
            self.assertNotIn("colors", params["persona"])

    def test_persona_colours_are_plain_hex_before_reaching_odoo_styles(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "face.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

            def persona(colors):
                (folder / "persona.json").write_text(json.dumps({"name": "Chloé", "avatar": "face.png", "colors": colors,
                    "messages": [{"type": "text", "en": "Hi", "fr": "Bonjour"}]}))
                return odoo.chat_parameters(self.chat_args(chat_persona=str(folder / "persona.json")))

            self.assertEqual({"background": "#6a33a6", "text": "#ffffff"},
                             persona({"background": "#6A33A6", "text": "#ffffff"})["persona"]["colors"])
            # Odoo writes the colours into style attributes of the chat button and header.
            for colors in ({"background": "#6a33a6"}, {"background": "#6a33a6", "text": "#fff"},
                           {"background": "#6a33a6; position: fixed", "text": "#ffffff"},
                           {"background": "red", "text": "#ffffff"},
                           {"background": "#6a33a6", "text": "#ffffff", "button": "#000000"}, ["#6a33a6", "#ffffff"]):
                with self.assertRaises(odoo.ServiceError):
                    persona(colors)

    def test_email_worker_routes_the_chat_address_and_keeps_the_secret_in_a_binding(self):
        calls = []

        def cloudflare(token, method, path, body=None, content_type="application/json"):
            calls.append((method, path, body))
            if path.startswith("/zones?name="):
                return [{"id": "zone", "account": {"id": "account"}}]
            if path.endswith("/email/routing/rules?per_page=50"):
                return [{"id": "rule-1", "name": odoo.ROUTING_RULE}]
            return {}

        with patch.object(odoo, "cloudflare", side_effect=cloudflare):
            odoo.deploy_email_worker("token", "chat@example.test", "https://odoo.example.test/platform_crm_intake/mail/inbound", "s3cret")
        upload = next(body for method, path, body in calls if path == "/accounts/account/workers/scripts/platform-chat-email")
        self.assertIn(b'{"type": "secret_text", "name": "INBOUND_SECRET", "text": "s3cret"}', upload)
        self.assertIn(b"export default", upload)
        self.assertIn(("PATCH", "/zones/zone/email/routing", {"support_subaddress": True}), calls)
        method, path, rule = calls[-1]
        self.assertEqual(("PUT", "/zones/zone/email/routing/rules/rule-1"), (method, path))
        self.assertEqual([{"type": "literal", "field": "to", "value": "chat@example.test"}], rule["matchers"])
        self.assertEqual([{"type": "worker", "value": ["platform-chat-email"]}], rule["actions"])
        self.assertNotIn("s3cret", json.dumps(rule))


if __name__ == "__main__":
    unittest.main()
