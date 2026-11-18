#!/usr/bin/env python3
"""Guards for the portal gate (ADR 0031): administrators get Homepage unchanged;
anyone else gets only the services their access groups open, the ones they can
ask for greyed, and nothing else of the platform, not even its names. Notices
show only to people who can open their service, and to administrators, who
also get each tile's running version from its pods."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SERVICES = [
    {"name": "Applications - PROD", "services": [
        {"name": "Shop", "href": "https://shop.example.com", "icon": "/icons/shop.svg", "description": "Sells <b>things</b> & more",
         "namespace": "apps", "podSelector": "app=shop-web"}]},
    {"name": "Applications - UAT", "services": [
        {"name": "Shop UAT", "href": "https://shop-uat.example.com", "icon": "mdi-cart-outline", "description": "Test shop",
         "namespace": "apps-uat", "podSelector": "app=shop-web"}]},
    {"name": "DevOps & Delivery", "services": [
        {"name": "GitLab", "href": "https://gitlab.example.com", "icon": "gitlab.png", "description": "Source",
         "namespace": "infra", "podSelector": "app=gitlab"},
        {"name": "Argo CD", "href": "https://argocd.example.com", "icon": "argo-cd.png", "description": "Delivery",
         "namespace": "infra", "podSelector": "app.kubernetes.io/part-of=argocd"},
        {"name": "SonarQube", "href": "https://sonarqube.example.com", "icon": "sonarqube.png", "description": "Quality"},
        {"name": "Mailpit", "href": "https://cloud.example.com/mailpit/", "icon": "mdi-email-search-outline", "description": "Mail"}]},
    {"name": "Cluster Management", "services": [
        {"name": "Portainer", "href": "https://portainer.example.com", "icon": "portainer.png", "description": "Cluster"},
        {"name": "K3s", "icon": "k3s.png", "description": "No link"}]},
    {"name": "Observability & Logging", "services": [
        {"name": "Grafana", "href": "https://grafana.example.com", "icon": "grafana.png", "description": "Dashboards",
         "namespace": "infra", "podSelector": "app=grafana"},
        {"name": "Kibana", "href": "https://kibana.example.com", "icon": "kibana.png", "description": "Logs"}]},
]


NOTICES = [
    {"id": "n1", "service": "GitLab", "starts": "2026-01-01T22:00:00+00:00", "ends": "2999-01-01T23:00:00+00:00",
     "en": "GitLab <upgrade> tonight", "fr": "Mise à jour de GitLab ce soir"},
    {"id": "n2", "service": "Grafana", "starts": "2026-01-01T22:00:00+00:00", "ends": "2999-01-01T23:00:00+00:00", "en": "Grafana restarts"},
    {"id": "n3", "service": "*", "starts": "2998-01-01T22:00:00+00:00", "ends": "2999-01-01T23:00:00+00:00", "en": "Network maintenance"},
    {"id": "n4", "service": "GitLab", "starts": "2020-01-01T22:00:00+00:00", "ends": "2020-01-01T23:00:00+00:00", "en": "Long over"},
]


TOKEN = "service-account-token"
ARGOCD = "v3.6.1-sha256-296e3d74f449"


def pod(image, version=None):
    labels = {"app.kubernetes.io/version": version} if version else {}
    return {"metadata": {"labels": labels}, "spec": {"containers": [{"image": image}, {"image": "docker.io/library/sidecar:9.9.9"}]}}


# Running pods by namespace and label selector, as the API server answers.
PODS = {
    ("apps", "app=shop-web"): [pod("registry.example.com/shop/web:2.4.0@sha256:" + "a" * 64)],
    # A build tag that names no version, then a digest alone: no version.
    ("apps-uat", "app=shop-web"): [pod("registry.example.com/shop/web:main-1a2b3c"), pod("registry.example.com/shop/web@sha256:" + "b" * 64)],
    ("infra", "app=gitlab"): [pod("docker.io/gitlab/gitlab-ce:19.5.6-ce.0@sha256:" + "c" * 64)],
    # The chart's label wins over a sidecar image (Redis), and the most common version over a pod mid-rollout.
    ("infra", "app.kubernetes.io/part-of=argocd"): [pod("quay.io/argoproj/argocd:v3.6.1", ARGOCD), pod("docker.io/library/redis:8.10.3", ARGOCD),
                                                    pod("quay.io/argoproj/argocd:v3.6.0", "v3.6.0"), pod("quay.io/argoproj/argocd:v3.6.1")],
    ("infra", "app=grafana"): [pod("docker.io/grafana/grafana@sha256:" + "d" * 64)],
}


class Kubernetes(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        parts = url.path.split("/")
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            status, body = 401, {}
        elif url.path == "/version":
            status, body = 200, {"gitVersion": "v1.36.6+k3s1"}
        elif len(parts) == 6 and parts[1:4] == ["api", "v1", "namespaces"] and parts[5] == "pods" \
                and query.get("fieldSelector") == ["status.phase=Running"]:
            status, body = 200, {"items": PODS.get((parts[4], query.get("labelSelector", [""])[0]), [])}
        else:
            status, body = 404, {}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


class Homepage(BaseHTTPRequestHandler):
    def do_GET(self):
        answers = {"/api/services": ("application/json", json.dumps(SERVICES)), "/icons/swirl.svg": ("image/svg+xml", "<svg/>"),
                   "/api/config/custom.css": ("text/css", "html{}"), "/api/widgets": ("application/json", '["cluster"]'),
                   "/": ("text/html", "<html>homepage</html>")}
        kind, body = answers.get(self.path, ("text/plain", ""))
        self.send_response(200 if self.path in answers else 404)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PortalGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Homepage)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.port = free_port()
        cls.kubernetes = ThreadingHTTPServer(("127.0.0.1", 0), Kubernetes)
        threading.Thread(target=cls.kubernetes.serve_forever, daemon=True).start()
        cls.account = tempfile.TemporaryDirectory()
        (Path(cls.account.name) / "token").write_text(TOKEN + "\n")
        cls.notices = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(NOTICES, cls.notices)
        cls.notices.close()
        cls.gate = subprocess.Popen(
            ["node", str(ROOT / "k8s/files/portal-gate.mjs")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": __import__("os").environ["PATH"], "GATE_PORT": str(cls.port), "UPSTREAM_PORT": str(cls.upstream.server_port),
                 "GATE_RULES": str(ROOT / "k8s/files/portal-access.json"), "PORTAL_TITLE": "Example Cloud",
                 "ACCESS_CONTACT": "admin@example.com", "ACCOUNT_URL": "https://keycloak.example.com/account",
                 "SIGN_OUT_URL": "https://keycloak.example.com/oauth2/sign_out", "GATE_NOTICES": cls.notices.name,
                 "KUBERNETES_API": f"http://127.0.0.1:{cls.kubernetes.server_port}", "SERVICE_ACCOUNT_DIR": cls.account.name})
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/gate/healthz", timeout=1)
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("the portal gate did not start")

    @classmethod
    def tearDownClass(cls):
        cls.gate.terminate()
        cls.gate.wait(timeout=5)
        cls.upstream.shutdown()
        cls.kubernetes.shutdown()
        cls.account.cleanup()
        Path(cls.notices.name).unlink()

    def get(self, path, groups=None, method="GET", language="en"):
        headers = {"Accept-Language": language, "X-Auth-Request-Email": "ada@example.com"}
        if groups is not None:
            headers["X-Auth-Request-Groups"] = groups
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, response.read().decode(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(), error.headers

    def test_administrators_get_homepage_unchanged(self):
        self.assertEqual((200, json.dumps(SERVICES)), self.get("/api/services", "developers,platform-admins")[:2])
        self.assertEqual((200, "<html>homepage</html>"), self.get("/", "platform-admins")[:2])
        self.assertEqual((200, '["cluster"]'), self.get("/api/widgets", "platform-admins")[:2])

    def test_a_person_without_groups_sees_only_open_and_requestable_services(self):
        status, page, headers = self.get("/")
        self.assertEqual(200, status)
        for shown in ("Shop", "GitLab", 'href="https://shop.example.com"', 'href="https://gitlab.example.com"'):
            self.assertIn(shown, page)
        # Greyed, with a request instead of the service's address.
        for locked in ("Grafana", "Argo CD", "Mailpit"):
            self.assertIn(locked, page)
        self.assertIn("mailto:admin%40example.com?subject=Access%20request%3A%20Grafana", page)
        for address in ("grafana.example.com", "argocd.example.com", "/mailpit/"):
            self.assertNotIn(address, page)
        # Not even the names of what they cannot ask for.
        for hidden in ("Portainer", "Kibana", "SonarQube", "K3s", "Shop UAT", "Cluster Management"):
            self.assertNotIn(hidden, page)
        self.assertIn("ada@example.com", page)
        self.assertIn("script-src 'nonce-", headers["Content-Security-Policy"])
        self.assertEqual("private, no-store", headers["Cache-Control"])

    def test_access_groups_open_their_services(self):
        _, page, _ = self.get("/", "access-grafana, access-environments")
        self.assertIn('href="https://grafana.example.com"', page)
        self.assertIn("Shop UAT", page)
        self.assertNotIn("subject=Access%20request%3A%20Grafana", page)
        self.assertIn("subject=Access%20request%3A%20Argo%20CD", page)
        self.assertNotIn("Portainer", page)

    def test_service_text_is_escaped(self):
        _, page, _ = self.get("/")
        self.assertIn("Sells &lt;b&gt;things&lt;/b&gt; &amp; more", page)
        self.assertNotIn("<b>things</b>", page)

    def test_nothing_else_of_homepage_is_reachable(self):
        for path in ("/api/services", "/api/widgets", "/api/kubernetes/status/infra/gitlab", "/_next/data/build/index.json",
                     "/api/config/custom.js", "/icons/../api/services"):
            self.assertEqual(404, self.get(path)[0], path)
            self.assertEqual(404, self.get(path, "access-grafana")[0], path)
        self.assertEqual(405, self.get("/", method="POST")[0])
        self.assertEqual(200, self.get("/icons/swirl.svg")[0])
        self.assertEqual(200, self.get("/api/config/custom.css")[0])

    def test_french_follows_the_browser(self):
        _, page, _ = self.get("/", language="fr-FR,fr;q=0.9")
        self.assertIn('lang="fr"', page)
        self.assertIn("Demander l&#39;accès", page)
        self.assertIn("subject=Demande%20d&#39;acc%C3%A8s%3A%20Grafana", page)

    def test_notices_show_to_who_can_open_the_service(self):
        _, page, _ = self.get("/")
        self.assertIn("GitLab &lt;upgrade&gt; tonight", page)
        self.assertIn("Network maintenance", page)
        self.assertNotIn("Grafana restarts", page)
        self.assertNotIn("Long over", page)
        _, page, _ = self.get("/", "access-grafana")
        self.assertIn("Grafana restarts", page)
        _, page, _ = self.get("/", language="fr")
        self.assertIn("Mise à jour de GitLab ce soir", page)
        self.assertIn("Plateforme", page)
        status, body, _ = self.get("/gate/notices.json", "platform-admins")
        self.assertEqual((200, ["n1", "n2", "n3"]), (status, [notice["id"] for notice in json.loads(body)]))
        self.assertEqual(404, self.get("/gate/notices.json")[0])


    def test_administrators_get_each_tiles_running_version(self):
        status, body, headers = self.get("/gate/versions.json", "platform-admins")
        self.assertEqual(200, status)
        self.assertEqual("private, no-store", headers["Cache-Control"])
        # Tiles are keyed by group: one application has a tile per environment. A tile
        # whose pods name no version (Shop UAT, Grafana) or that has no pod is left out.
        self.assertEqual({"Applications - PROD": {"Shop": "2.4.0"},
                          "DevOps & Delivery": {"GitLab": "19.5.6", "Argo CD": "3.6.1"},
                          "Cluster Management": {"K3s": "1.36.6"}}, json.loads(body))
        for groups in (None, "access-grafana"):
            self.assertEqual(404, self.get("/gate/versions.json", groups)[0])


if __name__ == "__main__":
    unittest.main()
