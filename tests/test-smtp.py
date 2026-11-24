#!/usr/bin/env python3
"""Guards for the dependency-free SMTP client (k8s/files/smtp.mjs), run against
a fake relay: it greets, authenticates, sends to every recipient once, and the
message decodes to the text; a refused recipient fails the send."""
import base64
import email
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Relay(socketserver.StreamRequestHandler):
    def handle(self):
        server = self.server
        say = lambda line: self.wfile.write((line + "\r\n").encode())  # noqa: E731
        say("220 fake relay")
        while True:
            line = self.rfile.readline().decode().rstrip("\r\n")
            if not line:
                return
            server.log.append(line)
            if line.startswith("EHLO"):
                say("250-fake")
                say("250 AUTH LOGIN")
            elif line == "AUTH LOGIN":
                say("334 VXNlcm5hbWU6")
                server.log.append("user:" + base64.b64decode(self.rfile.readline().strip()).decode())
                say("334 UGFzc3dvcmQ6")
                server.log.append("password:" + base64.b64decode(self.rfile.readline().strip()).decode())
                say("235 ok")
            elif line.startswith("MAIL FROM"):
                say("250 ok")
            elif line.startswith("RCPT TO"):
                say("550 no such user" if "refused@" in line else "250 ok")
            elif line == "DATA":
                say("354 go")
                data = []
                while True:
                    part = self.rfile.readline().decode()
                    if part == ".\r\n":
                        break
                    data.append(part)
                server.messages.append("".join(data))
                say("250 queued")
            elif line == "QUIT":
                say("221 bye")
                return


@unittest.skipUnless(shutil.which("node"), "node is required")
class Smtp(unittest.TestCase):
    def send(self, recipients):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Relay)
        server.log, server.messages = [], []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        script = (f"import {{ sendMail }} from {json.dumps(str(ROOT / 'k8s/files/smtp.mjs'))};\n"
                  f"await sendMail({{ host: '127.0.0.1', port: {server.server_address[1]}, tls: 'none', username: 'relay-user',"
                  " password: 'relay-pass', from: 'noreply@example.com', fromName: 'Plateforme' },"
                  f" {{ to: {json.dumps(recipients)}, subject: 'Accès : fin le 2026-12-01', text: 'Bonjour,\\n.leading dot\\nFin.' }});\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "send.mjs"
            path.write_text(script)
            result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        server.shutdown()
        return result, server

    def test_message_reaches_every_recipient(self):
        result, server = self.send(["ada@example.com", "bob@example.com"])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("user:relay-user", server.log)
        self.assertIn("password:relay-pass", server.log)
        self.assertEqual(["RCPT TO:<ada@example.com>", "RCPT TO:<bob@example.com>"], [l for l in server.log if l.startswith("RCPT")])
        message = email.message_from_string(server.messages[0])
        self.assertEqual("Bonjour,\n.leading dot\nFin.", message.get_payload(decode=True).decode())
        self.assertIn("=?UTF-8?B?", message["Subject"])
        self.assertNotIn("relay-pass", result.stdout + result.stderr)

    def test_refused_recipient_fails(self):
        result, _ = self.send(["refused@example.com"])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("SMTP RCPT TO refused (550)", result.stderr)


if __name__ == "__main__":
    unittest.main()
