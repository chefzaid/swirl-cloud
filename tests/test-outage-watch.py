#!/usr/bin/env python3
"""Guards for the outage watch: the relay password reaches Cloudflare only as a
Worker secret, only origin-reaching production hosts are watched, the Durable
Object class is created once, and the Worker's SMTP client emails each change
once over implicit TLS and STARTTLS and retries a send the relay refused."""
import base64
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import email.parser
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("outage_watch", ROOT / "scripts/configure-outage-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)
from lib.onboarding_services import Services  # noqa: E402

PASSWORD = 'p@ss "word\\ é'
SMTP = {"enabled": "true", "host": "smtp.example.com", "port": "465", "tls": "ssl", "username": "relay-user",
        "password": PASSWORD, "from_address": "cloud@example.com", "from_name": "Example Cloud",
        "alert_to": "ops@example.com"}


def ingress(namespace, *hosts):
    return {"metadata": {"namespace": namespace}, "spec": {"rules": [{"host": host} for host in hosts]}}


INGRESSES = [ingress("apps", "example.com", "www.example.com", "shop.example.org"), ingress("apps-int", "demo-int.example.com"),
             ingress("infra", "gitlab.example.com", "grafana.example.com"), ingress("corp", "odoo.example.com")]


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Configurator(unittest.TestCase):
    def test_only_origin_reaching_production_hosts_are_watched(self):
        endpoints = watch.watched_endpoints(INGRESSES, {"grafana.example.com"}, "example.com")
        # The apex first, then alphabetical; no environment or Access-gated host.
        self.assertEqual(["https://example.com/", "https://gitlab.example.com/", "https://odoo.example.com/",
                          "https://shop.example.org/", "https://www.example.com/"], endpoints)

    def test_relay_password_reaches_cloudflare_only_as_a_worker_secret(self):
        requests, calls = [], []

        def urlopen(request, timeout=None):
            requests.append(request)
            path = request.full_url.split("/client/v4")[1]
            if path.endswith("/settings"):
                raise watch.HTTPError(request.full_url, 404, "missing", {}, io.BytesIO(b"{}"))
            result = ([{"account": {"id": "a" * 32}}] if path.startswith("/zones")
                      else [{"domain": "grafana.example.com"}, {"domain": "gitlab.example.com/admin"}]
                      if "/access/apps" in path else {})
            return Response(json.dumps({"success": True, "result": result}).encode())

        def run(args, **_kwargs):
            calls.append(args)
            kind = args[3]
            data = ({"data": {"PLATFORM_DOMAIN": "example.com"}} if kind == "configmap" else {"items": INGRESSES})
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

        @contextmanager
        def vault(_self):
            class Api:
                def request(self, method, path, data=None, **_kwargs):
                    return {"data": {"data": SMTP, "metadata": {"version": 3}}}
            yield Api()

        output = io.StringIO()
        with patch.object(watch, "urlopen", urlopen), patch.object(watch.subprocess, "run", side_effect=run), \
                patch.object(Services, "_vault", vault), patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "cf-token"}), \
                patch.object(sys, "argv", ["configure-outage-watch.py", "--report"]), \
                redirect_stdout(output), redirect_stderr(output):
            watch.main()
        upload = next(r for r in requests if r.get_method() == "PUT" and r.full_url.endswith(watch.WORKER_NAME))
        metadata = json.loads(upload.data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])
        secrets = [b for b in metadata["bindings"] if b["type"] == "secret_text"]
        plain = [b for b in metadata["bindings"] if b["type"] == "plain_text"]
        self.assertEqual([{"type": "secret_text", "name": "SMTP_PASSWORD", "text": PASSWORD}], secrets)
        self.assertNotIn(PASSWORD, json.dumps(plain))
        self.assertEqual({"new_tag": "v1", "new_sqlite_classes": ["Watch"]}, metadata["migrations"])
        watched = json.loads(next(b["text"] for b in plain if b["name"] == "ENDPOINTS"))
        self.assertIn("https://gitlab.example.com/", watched)
        self.assertNotIn("https://grafana.example.com/", watched)
        self.assertTrue(any(b["name"] == "REPORT_ID" for b in plain))
        hidden = next(r for r in requests if r.get_method() == "POST" and r.full_url.endswith("/subdomain"))
        self.assertEqual({"enabled": False, "previews_enabled": False}, json.loads(hidden.data))
        schedule = next(r for r in requests if r.full_url.endswith("/schedules"))
        self.assertEqual([{"cron": "*/5 * * * *"}], json.loads(schedule.data))
        for text in (json.dumps(calls), output.getvalue(), " ".join(r.full_url for r in requests)):
            self.assertNotIn(PASSWORD, text)

    def test_durable_object_class_is_created_only_once(self):
        uploads = []

        def cloudflare(token, method, path, body=None, content_type=None, missing=False):
            if path.endswith("/settings"):
                return {"bindings": [{"type": "durable_object_namespace", "name": "WATCH"}]}
            if method == "PUT" and body and isinstance(body, bytes):
                uploads.append(json.loads(body.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]))
            return {}

        with patch.object(watch, "cloudflare", cloudflare):
            self.assertFalse(watch.deploy("token", "account", watch.bindings(SMTP, ["https://example.com/"], "example.com")))
        self.assertNotIn("migrations", uploads[0])

    def test_transient_cloudflare_errors_are_retried(self):
        answers = [watch.HTTPError(watch.API, 502, "bad gateway", {}, io.BytesIO(b"<html>")),
                   Response(json.dumps({"success": True, "result": {"ok": 1}}).encode())]

        def urlopen(_request, timeout=None):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with patch.object(watch, "urlopen", urlopen), patch.object(watch.time, "sleep"):
            self.assertEqual({"ok": 1}, watch.cloudflare("token", "GET", "/zones"))
        with patch.object(watch, "urlopen", side_effect=watch.HTTPError(watch.API, 403, "no", {}, io.BytesIO(
                b'{"success": false, "errors": [{"code": 10000}]}'))), patch.object(watch.time, "sleep") as pause:
            with self.assertRaises(watch.ServiceError):
                watch.cloudflare("token", "GET", "/zones")
            pause.assert_not_called()

    def test_platform_mail_must_be_on(self):
        @contextmanager
        def vault(_self):
            class Api:
                def request(self, *_args, **_kwargs):
                    return {"data": {"data": dict(SMTP, enabled="false", password=""), "metadata": {"version": 4}}}
            yield Api()

        with patch.object(Services, "_vault", vault), self.assertRaises(watch.ServiceError):
            watch.relay_settings()


# Runs the Worker module in Node with stand-ins for Cloudflare's socket and
# Durable Object modules, against the fake relay below.
SHIMS = {
    "loader.mjs": """
export async function resolve(specifier, context, next) {
  const shims = { 'cloudflare:sockets': './sockets.mjs', 'cloudflare:workers': './workers.mjs' };
  return shims[specifier] ? { url: new URL(shims[specifier], import.meta.url).href, shortCircuit: true } : next(specifier, context);
}""",
    "register.mjs": "import { register } from 'node:module';\nregister('./loader.mjs', import.meta.url);",
    "workers.mjs": "export class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }",
    "sockets.mjs": """
import net from 'node:net'; import tls from 'node:tls';
const options = (a) => ({ host: a.hostname, port: a.port, servername: a.hostname, ca: process.env.TEST_CA });
function wrap(sock, address) {
  let controller;
  const data = (chunk) => controller.enqueue(new Uint8Array(chunk));
  const end = () => { try { controller.close(); } catch {} };
  const fail = (error) => { try { controller.error(error); } catch {} };
  const readable = new ReadableStream({ start(c) { controller = c; sock.on('data', data); sock.on('end', end); sock.on('error', fail); } });
  const writable = new WritableStream({ write: (chunk) => new Promise((ok, no) => sock.write(chunk, (e) => (e ? no(e) : ok()))) });
  return { readable, writable, async close() { sock.destroy(); },
    startTls() { sock.off('data', data); sock.off('end', end); sock.off('error', fail); return wrap(tls.connect({ ...options(address), socket: sock }), address); } };
}
export function connect(address, { secureTransport } = {}) {
  return wrap(secureTransport === 'on' ? tls.connect(options(address)) : net.connect(options(address)), address);
}""",
    "driver.mjs": """
import http from 'node:http';
import { Watch } from './worker.mjs';
const [tls, port, password] = process.argv.slice(2);
let failing = false;
const server = http.createServer((request, response) => { response.statusCode = failing ? 503 : 200; response.end(); });
await new Promise((ok) => server.listen(0, '127.0.0.1', ok));
const store = new Map();
const env = (pass, extra) => ({ ENDPOINTS: JSON.stringify([`http://127.0.0.1:${server.address().port}/`]), SITE: 'example.com',
  SMTP_HOST: 'localhost', SMTP_PORT: port, SMTP_TLS: tls, SMTP_USERNAME: 'relay-user', SMTP_PASSWORD: pass,
  MAIL_FROM: 'cloud@example.com', MAIL_FROM_NAME: 'Example Cloud', MAIL_TO: 'ops@example.com,lead@example.com', ...extra });
const storage = { get: async (key) => structuredClone(store.get(key)), put: async (key, value) => { store.set(key, structuredClone(value)); } };
const t0 = Date.UTC(2026, 9, 9, 18, 0), minute = 60000;
const run = async (pass, offset, extra = {}) => (await new Watch({ storage }, env(pass, extra)).check(t0 + offset * minute)).changed;
const changes = [await run(password, 0, { REPORT_ID: 'once' })];
failing = true;
changes.push(await run(password, 5), await run('refused', 10), await run(password, 15), await run(password, 20));
failing = false;
changes.push(await run(password, 50, { REPORT_ID: 'once' }));
console.log(JSON.stringify(changes));
server.close();""",
}


class FakeRelay:
    """SMTP relay on 127.0.0.1 with a throwaway certificate: implicit TLS or STARTTLS."""

    def __init__(self, directory, starttls):
        self.messages, self.starttls = [], starttls
        self.context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self.context.load_cert_chain(directory / "cert.pem", directory / "key.pem")
        self.server = socket.create_server(("127.0.0.1", 0))
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            connection, _ = self.server.accept()
            if not self.starttls:
                connection = self.context.wrap_socket(connection, server_side=True)
            threading.Thread(target=self.session, args=(connection,), daemon=True).start()

    def session(self, connection):
        try:
            self.converse(connection)
        finally:
            connection.close()

    def converse(self, connection):
        stream, secure, authenticated = connection.makefile("rwb"), not self.starttls, False

        def reply(*lines):
            stream.write(b"".join(line.encode() + b"\r\n" for line in lines))
            stream.flush()

        reply("220 relay ready")
        while line := stream.readline().decode().rstrip("\r\n"):
            verb = line.split(" ")[0].upper()
            if verb == "EHLO":
                reply("250-relay", "250-AUTH PLAIN", "250-STARTTLS" if not secure else "250-8BITMIME", "250 SIZE 100000")
            elif verb == "STARTTLS":
                reply("220 go ahead")
                stream.close()
                connection = self.context.wrap_socket(connection, server_side=True)
                stream, secure = connection.makefile("rwb"), True
            elif verb == "AUTH":
                _, user, password = base64.b64decode(line.split(" ")[2]).decode().split("\0")
                authenticated = secure and (user, password) == ("relay-user", PASSWORD)
                # A careless relay may echo what it received; the Worker must not repeat it.
                reply("235 ok" if authenticated else "535 rejected " + password)
            elif verb in ("MAIL", "RCPT"):
                reply("250 ok" if authenticated else "530 authenticate first")
            elif verb == "DATA":
                reply("354 go")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    data += stream.readline()
                self.messages.append(email.parser.BytesParser().parsebytes(data[:-5]))
                reply("250 queued")
            elif verb == "QUIT":
                reply("221 bye")
                break
        stream.close()


class Worker(unittest.TestCase):
    def test_worker_emails_each_change_once_over_tls_and_starttls(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
                            "-keyout", str(directory / "key.pem"), "-out", str(directory / "cert.pem")],
                           check=True, capture_output=True)
            for file, content in SHIMS.items():
                (directory / file).write_text(content)
            shutil.copy(ROOT / "scripts/lib/outage_watch_worker.js", directory / "worker.mjs")
            for tls in ("ssl", "starttls"):
                with self.subTest(tls=tls):
                    relay = FakeRelay(directory, starttls=tls == "starttls")
                    result = subprocess.run(["node", "--import", "./register.mjs", "driver.mjs", tls,
                                             str(relay.server.getsockname()[1]), PASSWORD], cwd=directory,
                                            capture_output=True, text=True, timeout=120,
                                            env={**os.environ, "TEST_CA": (directory / "cert.pem").read_text()})
                    self.assertEqual(0, result.returncode, result.stderr)
                    # up; one failure; down but the relay refused; down retried; still down; up again.
                    self.assertEqual([0, 0, 1, 1, 0, 1], json.loads(result.stdout.strip().splitlines()[-1]))
                    self.assertNotIn(PASSWORD, result.stdout + result.stderr)
                    subjects = [message["Subject"] for message in relay.messages]
                    self.assertEqual(["[STATUS] 1 of 1 example.com endpoints answer",
                                      "[DOWN] 1 of 1 example.com endpoints unreachable",
                                      "[UP] every example.com endpoint answers again"], subjects)
                    down = relay.messages[1]
                    self.assertEqual('"Example Cloud" <cloud@example.com>', down["From"])
                    self.assertEqual("ops@example.com, lead@example.com", down["To"])
                    self.assertIn("HTTP 503 (failed the last 2 checks)", down.get_payload(decode=True).decode())
                    self.assertIn("(down for 35 minutes)", relay.messages[2].get_payload(decode=True).decode())
                    relay.server.close()


if __name__ == "__main__":
    unittest.main()
