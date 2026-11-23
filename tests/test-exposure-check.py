#!/usr/bin/env python3
"""Guards for the outside exposure check: the scan finds a listening port and
nothing else, a port outside the allowed list and an administration host that
answers without the sign-in gate are reported, DNSSEC must validate, and DNS
records pointing at a missing name or at a foreign address are reported."""
import asyncio
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("exposure", ROOT / "scripts/exposure-check.py")
exposure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exposure)


def fake_fetch(answers):
    def fetch(url, timeout=15):
        for prefix in sorted(answers, key=len, reverse=True):
            if url.startswith(prefix):
                return answers[prefix]
        raise OSError("unreachable " + url)
    return fetch


class ExposureCheck(unittest.TestCase):
    def test_scan_finds_only_the_listening_port(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        free = closed.getsockname()[1]
        closed.close()
        try:
            self.assertEqual([port], asyncio.run(exposure.scan("127.0.0.1", [port, free], timeout=1)))
        finally:
            listener.close()

    def test_gate_answers(self):
        self.assertTrue(exposure.gate_answer(302, "https://keycloak.example.com/oauth2/start?rd=%2F", ""))
        self.assertTrue(exposure.gate_answer(302, "https://team.cloudflareaccess.com/cdn-cgi/access/login", ""))
        self.assertTrue(exposure.gate_answer(403, "", "blocked"))
        self.assertFalse(exposure.gate_answer(200, "", "<html>Kibana</html>"))
        self.assertFalse(exposure.gate_answer(302, "/app/home", ""))

    def test_gated_hosts_come_from_the_ingress_manifests(self):
        hosts = exposure.gated_hosts("example.com")
        for host in ("kibana.example.com", "longhorn.example.com", "support.example.com"):
            self.assertIn(host, hosts)
        self.assertNotIn("passwords.example.com", hosts)  # the password manager's API has no gate

    def test_differences_are_reported(self):
        answers = {"https://kibana.": (200, "", "<html>Kibana</html>"), "https://": (302, "https://keycloak.example.com/oauth2/start", ""),
                   exposure.RESOLVER + "?name=example.com&type=SOA": (200, "", json.dumps({"Status": 0, "AD": False})),
                   exposure.RESOLVER + "?name=old.herokuapp.com": (200, "", json.dumps({"Status": 3})),
                   exposure.RESOLVER + "?name=live.example.net": (200, "", json.dumps({"Status": 0}))}
        records = [{"name": "old.example.com", "type": "CNAME", "content": "old.herokuapp.com"},
                   {"name": "www.example.com", "type": "CNAME", "content": "live.example.net"},
                   {"name": "mail.example.com", "type": "A", "content": "198.51.100.7", "proxied": False},
                   {"name": "app.example.com", "type": "A", "content": "198.51.100.8", "proxied": True},
                   {"name": "ssh.example.com", "type": "A", "content": "203.0.113.10", "proxied": False}]
        args = SimpleNamespace(address=["203.0.113.10"], domain="example.com", allowed_ports=["22"])
        result = exposure.check(args, fetcher=fake_fetch(answers), scanner=lambda address: [22, 443], records=records)
        text = "\n".join(result["findings"])
        self.assertIn("203.0.113.10: TCP 443 accepts connections", text)
        self.assertNotIn("TCP 22 ", text)
        self.assertIn("https://kibana.example.com/ answered HTTP 200", text)
        self.assertIn("DNSSEC does not validate", text)
        self.assertIn("old.example.com CNAME old.herokuapp.com: the target does not exist", text)
        self.assertIn("mail.example.com A 198.51.100.7: not one of the platform's addresses", text)
        self.assertNotIn("www.example.com", text)
        self.assertNotIn("app.example.com", text)
        self.assertNotIn("ssh.example.com", text)
        self.assertIn("## Differences", exposure.markdown(result))

    def test_a_clean_run_has_no_findings(self):
        answers = {"https://": (302, "https://keycloak.example.com/oauth2/start", ""),
                   exposure.RESOLVER: (200, "", json.dumps({"Status": 0, "AD": True}))}
        args = SimpleNamespace(address=["203.0.113.10"], domain="example.com", allowed_ports=["22"])
        result = exposure.check(args, fetcher=fake_fetch(answers), scanner=lambda address: [22])
        self.assertEqual([], result["findings"])
        self.assertTrue(result["dnssec"])


if __name__ == "__main__":
    unittest.main()
