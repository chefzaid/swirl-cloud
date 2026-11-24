#!/usr/bin/env python3
"""Guards for the Cloudflare Tunnel preparation: it changes nothing at Cloudflare
without three Ready control planes, never takes over a locally managed tunnel or
another account's zone, leaves the candidate tunnel unpublished, and checks the
ingress origins on three distinct hosts before DNS may switch."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tunnel", ROOT / "scripts/configure-cloudflare-tunnel.py")
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)

DOMAIN = "example.com"
TOKEN = "cf-api-token-value"


def node(name, ready=True):
    return {"metadata": {"name": name}, "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def ingress_pod(name, host, ready=True, connector=True, deleting=False):
    pod = {"metadata": {"name": name}, "spec": {"nodeName": host},
           "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
                      "containerStatuses": [{"name": "traefik", "ready": True}, {"name": "cloudflared", "ready": connector}]}}
    if deleting:
        pod["metadata"]["deletionTimestamp"] = "2026-11-24T10:00:00Z"
    return pod


class FakeCloudflare:
    def __init__(self, zones=None, tunnels=None, token="tunnel-token"):
        self.zones = [{"id": "z1", "account": {"id": "acct-1"}}] if zones is None else zones
        self.tunnels = tunnels or []
        self.token = token
        self.calls = []

    def __call__(self, token):
        self.api_token = token
        return self

    def request(self, method, path, body=None):
        self.calls.append((method, path.split("?")[0], body))
        if path.startswith("/zones"):
            return self.zones
        if method == "GET" and path.endswith("/token"):
            return self.token
        if method == "GET":
            return self.tunnels
        if method == "POST":
            return {"id": "t-new", "config_src": body["config_src"]}
        return {}


class Kubectl:
    def __init__(self, nodes):
        self.nodes = nodes
        self.applied = []

    def __call__(self, *args, data=None):
        if args[:2] == ("get", "nodes"):
            return {"items": self.nodes}
        if args[:1] == ("apply",):
            self.applied.append(json.loads(data))
            return "applied\n"
        raise AssertionError(args)


def run_main(cloudflare, kube, env=None, argv=("--domain", DOMAIN)):
    environment = {"CLOUDFLARE_API_TOKEN": TOKEN, **(env or {})}
    with mock.patch.object(tunnel, "Cloudflare", cloudflare), mock.patch.object(tunnel, "kubectl", kube), \
            mock.patch.dict(tunnel.os.environ, environment, clear=True), \
            mock.patch.object(sys, "argv", ["configure-cloudflare-tunnel.py", *argv]), \
            mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        tunnel.main()
    return out.getvalue()


class TunnelConfiguration(unittest.TestCase):
    def test_the_zone_and_its_subdomains_go_to_the_verified_origin(self):
        ingress = tunnel.tunnel_configuration(DOMAIN)["config"]["ingress"]
        self.assertEqual([DOMAIN, f"*.{DOMAIN}"], [rule["hostname"] for rule in ingress[:-1]])
        for rule in ingress[:-1]:
            self.assertEqual("https://127.0.0.1:8444", rule["service"])
            self.assertEqual(DOMAIN, rule["originRequest"]["originServerName"])
            self.assertEqual("/etc/cloudflared/ca.pem", rule["originRequest"]["caPool"])
            self.assertNotIn("noTLSVerify", rule["originRequest"])
        self.assertEqual({"service": "http_status:404"}, ingress[-1])


class PrepareTunnel(unittest.TestCase):
    def test_fewer_than_three_ready_control_planes_change_nothing(self):
        cloudflare = FakeCloudflare()
        kube = Kubectl([node("a"), node("b"), node("c", ready=False)])
        with self.assertRaisesRegex(RuntimeError, "three Ready control planes"):
            run_main(cloudflare, kube)
        self.assertEqual([], cloudflare.calls)
        self.assertEqual([], kube.applied)

    def test_a_new_tunnel_is_prepared_but_not_published(self):
        cloudflare = FakeCloudflare()
        kube = Kubectl([node("a"), node("b"), node("c")])
        output = run_main(cloudflare, kube)
        self.assertEqual(TOKEN, cloudflare.api_token)
        methods = [(method, path) for method, path, _ in cloudflare.calls]
        self.assertIn(("POST", "/accounts/acct-1/cfd_tunnel"), methods)
        post = next(body for method, _, body in cloudflare.calls if method == "POST")
        self.assertEqual({"name": "swirl-cloud-example-com", "config_src": "cloudflare"}, post)
        put = next(body for method, _, body in cloudflare.calls if method == "PUT")
        self.assertEqual(tunnel.tunnel_configuration(DOMAIN), put)
        secret, state = kube.applied
        self.assertEqual(("Secret", "cloudflare-tunnel", "infra"),
                         (secret["kind"], secret["metadata"]["name"], secret["metadata"]["namespace"]))
        self.assertEqual("tunnel-token", secret["stringData"]["token"])
        self.assertEqual((ROOT / "config/cloudflare-origin-ca.pem").read_text(), secret["stringData"]["ca.pem"])
        self.assertEqual({"mode": "tunnel", "domain": DOMAIN, "tunnelID": "t-new", "publishedTunnelID": ""}, state["data"])
        self.assertNotIn("tunnel-token", output)
        self.assertNotIn(TOKEN, output)

    def test_an_existing_remote_tunnel_is_reused(self):
        cloudflare = FakeCloudflare(tunnels=[{"id": "t-old", "config_src": "cloudflare"}])
        kube = Kubectl([node("a"), node("b"), node("c")])
        run_main(cloudflare, kube)
        self.assertNotIn("POST", [method for method, _, _ in cloudflare.calls])
        self.assertIn(("PUT", "/accounts/acct-1/cfd_tunnel/t-old/configurations"), [(m, p) for m, p, _ in cloudflare.calls])
        self.assertEqual("t-old", kube.applied[1]["data"]["tunnelID"])

    def test_a_locally_managed_tunnel_is_never_replaced(self):
        cloudflare = FakeCloudflare(tunnels=[{"id": "t-local", "config_src": "local"}])
        kube = Kubectl([node("a"), node("b"), node("c")])
        with self.assertRaisesRegex(RuntimeError, "locally managed"):
            run_main(cloudflare, kube)
        self.assertNotIn("PUT", [method for method, _, _ in cloudflare.calls])
        self.assertEqual([], kube.applied)

    def test_duplicate_tunnels_stop_before_any_change(self):
        cloudflare = FakeCloudflare(tunnels=[{"id": "1", "config_src": "cloudflare"}, {"id": "2", "config_src": "cloudflare"}])
        with self.assertRaisesRegex(RuntimeError, "Multiple managed tunnels"):
            run_main(cloudflare, Kubectl([node("a"), node("b"), node("c")]))
        self.assertEqual({"GET"}, {method for method, _, _ in cloudflare.calls})

    def test_another_accounts_zone_is_refused(self):
        cloudflare = FakeCloudflare()
        with self.assertRaisesRegex(RuntimeError, "does not own the selected zone"):
            run_main(cloudflare, Kubectl([node("a"), node("b"), node("c")]), env={"CLOUDFLARE_ACCOUNT_ID": "acct-2"})
        self.assertEqual([("GET", "/zones")], [(m, p) for m, p, _ in cloudflare.calls])

    def test_a_missing_zone_points_to_the_zone_setup(self):
        with self.assertRaisesRegex(RuntimeError, "configure-cloudflare.sh"):
            run_main(FakeCloudflare(zones=[]), Kubectl([node("a"), node("b"), node("c")]))

    def test_an_empty_tunnel_token_is_not_stored(self):
        kube = Kubectl([node("a"), node("b"), node("c")])
        with self.assertRaisesRegex(RuntimeError, "no tunnel token"):
            run_main(FakeCloudflare(token=""), kube)
        self.assertEqual([], kube.applied)

    def test_the_domain_and_token_are_required(self):
        for argv, env in ((("--domain", "not a domain"), {}), (("--domain", DOMAIN), {"CLOUDFLARE_API_TOKEN": ""})):
            with self.subTest(argv=argv), mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
                run_main(FakeCloudflare(), Kubectl([]), env=env, argv=argv)


class CloudflareRequests(unittest.TestCase):
    def test_http_errors_name_the_permissions_not_the_token(self):
        error = urllib.error.HTTPError(tunnel.API + "/zones", 403, "Forbidden", {}, io.BytesIO(b"{}"))
        with mock.patch.object(tunnel.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                tunnel.Cloudflare(TOKEN).request("GET", "/zones")
        self.assertIn("HTTP 403", str(raised.exception))
        self.assertIn("Tunnel Edit", str(raised.exception))
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_an_unsuccessful_answer_is_an_error(self):
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({"success": False, "errors": [{"message": "x"}]}).encode())
        with mock.patch.object(tunnel.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "token permissions"):
                tunnel.Cloudflare(TOKEN).request("GET", "/zones")

    def test_the_token_goes_in_the_authorization_header(self):
        seen = {}

        def urlopen(request, timeout):
            seen["auth"] = request.get_header("Authorization")
            seen["timeout"] = timeout
            response = mock.MagicMock()
            response.__enter__.return_value = io.BytesIO(json.dumps({"success": True, "result": [1]}).encode())
            return response
        with mock.patch.object(tunnel.urllib.request, "urlopen", urlopen):
            self.assertEqual([1], tunnel.Cloudflare(TOKEN).request("GET", "/zones"))
        self.assertEqual("Bearer " + TOKEN, seen["auth"])
        self.assertEqual(45, seen["timeout"])


class VerifyOrigins(unittest.TestCase):
    def pods(self, *pods):
        return lambda *args, data=None: {"items": list(pods)}

    def test_three_distinct_ready_hosts_are_required(self):
        cases = {
            "two hosts": [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "b")],
            "connector not ready": [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "c", connector=False)],
            "pod not ready": [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "c", ready=False)],
            "pod deleting": [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "c", deleting=True)],
        }
        for label, pods in cases.items():
            with self.subTest(label), mock.patch.object(tunnel, "kubectl", self.pods(*pods)), \
                    mock.patch.object(tunnel, "forward_origin") as forward:
                with self.assertRaisesRegex(RuntimeError, "three distinct ready ingress hosts"):
                    tunnel.verify_origins("infra", DOMAIN)
                forward.assert_not_called()

    def test_every_ready_origin_is_verified_and_its_forward_stopped(self):
        pods = [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "c")]
        forwards = []

        def forward(namespace, pod):
            forwards.append(pod)
            return f"process-{pod}", 4000 + len(forwards)
        with mock.patch.object(tunnel, "kubectl", self.pods(*pods)), mock.patch.object(tunnel, "forward_origin", forward), \
                mock.patch.object(tunnel, "verify_origin") as verify, mock.patch.object(tunnel, "stop_forward") as stop, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            tunnel.verify_origins("infra", DOMAIN)
        self.assertEqual(["p1", "p2", "p3"], forwards)
        self.assertEqual([4001, 4002, 4003], [call.args[0] for call in verify.call_args_list])
        self.assertTrue(all(call.args[1] == DOMAIN for call in verify.call_args_list))
        self.assertEqual(["process-p1", "process-p2", "process-p3"], [call.args[0] for call in stop.call_args_list])

    def test_a_failing_origin_stops_the_switch_after_retries(self):
        pods = [ingress_pod("p1", "a"), ingress_pod("p2", "b"), ingress_pod("p3", "c")]
        with mock.patch.object(tunnel, "kubectl", self.pods(*pods)), \
                mock.patch.object(tunnel, "forward_origin", return_value=("process", 4001)), \
                mock.patch.object(tunnel, "verify_origin", side_effect=OSError("certificate verify failed")) as verify, \
                mock.patch.object(tunnel, "stop_forward") as stop, mock.patch.object(tunnel.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "Ingress TLS verification failed on a; public DNS was not changed"):
                tunnel.verify_origins("infra", DOMAIN)
        self.assertEqual(10, verify.call_count)
        stop.assert_called_once_with("process")


# Python's default context checks X.509 strictly: the CA needs its key usage.
CA_EXTENSIONS = ["-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign"]


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class VerifyOrigin(unittest.TestCase):
    """verify_origin against a real TLS listener: the CA and the zone's name must match."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        d = Path(cls.dir.name)
        quiet = {"check": True, "capture_output": True}
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Test CA", *CA_EXTENSIONS,
                        "-keyout", d / "ca.key", "-out", d / "ca.pem"], **quiet)
        for name in (DOMAIN, "other.example.net"):
            subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={name}",
                            "-keyout", d / f"{name}.key", "-out", d / f"{name}.csr"], **quiet)
            (d / f"{name}.ext").write_text(f"subjectAltName=DNS:{name},DNS:*.{name}\nbasicConstraints=CA:FALSE\n"
                                           "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
                                           "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid\n")
            subprocess.run(["openssl", "x509", "-req", "-in", d / f"{name}.csr", "-CA", d / "ca.pem", "-CAkey", d / "ca.key",
                            "-CAcreateserial", "-days", "1", "-extfile", d / f"{name}.ext", "-out", d / f"{name}.pem"], **quiet)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=Other CA", *CA_EXTENSIONS,
                        "-keyout", d / "other-ca.key", "-out", d / "other-ca.pem"], **quiet)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def serve(self, name, status):
        d = Path(self.dir.name)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(d / f"{name}.pem", d / f"{name}.key")
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)
        seen = {}

        def answer():
            connection, _ = listener.accept()
            try:
                with context.wrap_socket(connection, server_side=True) as tls:
                    seen["request"] = tls.recv(4096).decode()
                    tls.sendall(f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())
            except (OSError, ssl.SSLError):
                pass
        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        return listener.getsockname()[1], seen

    def test_a_listener_with_the_zone_certificate_passes(self):
        for status in (404, 302):
            with self.subTest(status=status):
                port, seen = self.serve(DOMAIN, status)
                tunnel.verify_origin(port, DOMAIN, Path(self.dir.name) / "ca.pem")
                self.assertIn(f"Host: {DOMAIN}\r\n", seen["request"])

    def test_a_server_error_fails(self):
        port, _ = self.serve(DOMAIN, 502)
        with self.assertRaisesRegex(RuntimeError, "HTTP 502"):
            tunnel.verify_origin(port, DOMAIN, Path(self.dir.name) / "ca.pem")

    def test_another_name_or_another_ca_fails(self):
        for name, ca in (("other.example.net", "ca.pem"), (DOMAIN, "other-ca.pem")):
            with self.subTest(name=name, ca=ca):
                port, _ = self.serve(name, 404)
                with self.assertRaises(ssl.SSLCertVerificationError):
                    tunnel.verify_origin(port, DOMAIN, Path(self.dir.name) / ca)


if __name__ == "__main__":
    unittest.main()
