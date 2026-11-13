#!/usr/bin/env python3
"""Ensure SonarQube's automation token, Vault secret/infra/sonarqube admin_token.

A new SonarQube starts with admin/admin, and nothing else creates the token that
its SSO bootstrap Job and namespace discovery use. Keep a token that still signs in
as admin; otherwise set the admin password to Vault's admin_password (generated and
stored first when missing), generate a user token and store it. Secrets reach
kubectl and Vault through stdin only.
"""
import base64
from datetime import datetime, timezone
import json
import os
import secrets
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PASSWORD = "admin"
TOKEN_NAME = "swirl-cloud-automation"
VAULT_PATH = "secret/infra/sonarqube"


class SonarError(RuntimeError):
    pass


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def bearer(token):
    return "Bearer " + token


class Sonar:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, authorization=None, data=None):
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(f"{self.base}/{path}", method=method, headers=headers,
                                         data=None if data is None else urllib.parse.urlencode(data).encode())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as error:
            raise SonarError(f"SonarQube {method} {path}: HTTP {error.code}") from None
        except (urllib.error.URLError, OSError) as error:
            raise SonarError(f"SonarQube {method} {path}: {error}") from None

    def login(self, authorization):
        """The login these credentials sign in as, or None."""
        try:
            current = self.call("GET", "api/users/current", authorization)
        except SonarError:
            return None
        return current.get("login") if current.get("isLoggedIn") else None

    def wait_until_up(self, timeout=600):
        deadline = time.monotonic() + timeout
        while True:
            try:
                if self.call("GET", "api/system/status").get("status") == "UP":
                    return
            except SonarError:
                pass
            if time.monotonic() > deadline:
                raise SonarError("SonarQube did not report status UP")
            time.sleep(5)


def generate_password(length=32):
    """A password that meets SonarQube's policy: every character class, 12+ characters."""
    alphabet = string.ascii_letters + string.digits + "!#%+-=?@_"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value)):
            return value


def ensure(sonar, stored, store, new_password=generate_password, now=None):
    """Make stored["admin_token"] a working admin token; returns "unchanged" or "created".

    stored holds the current Vault fields; store(field, value) writes one to Vault."""
    token = stored.get("admin_token")
    if token and sonar.login(bearer(token)) == "admin":
        return "unchanged"
    password = stored.get("admin_password")
    if not password:
        password = new_password()
        # Store it before SonarQube changes, so a failure never loses the password.
        store("admin_password", password)
    admin = basic("admin", password)
    if sonar.login(admin) != "admin":
        default = basic("admin", DEFAULT_PASSWORD)
        if sonar.login(default) != "admin":
            raise SonarError("SonarQube's admin password matches neither Vault infra/sonarqube admin_password "
                             "nor the default; restore it, or store a working admin_token")
        sonar.call("POST", "api/users/change_password", default,
                   {"login": "admin", "previousPassword": DEFAULT_PASSWORD, "password": password})
    moment = now or datetime.now(timezone.utc)
    created = sonar.call("POST", "api/user_tokens/generate", admin, {"name": f"{TOKEN_NAME}-{moment:%Y%m%dT%H%M%SZ}"})
    store("admin_token", created["token"])
    return "created"


def kubectl(*args, stdin=None):
    return subprocess.run(["kubectl", *args], input=stdin, capture_output=True, text=True, check=True).stdout


def main():
    namespace = os.environ.get("SONAR_NAMESPACE", "infra")
    pod = os.environ.get("VAULT_POD", "vault-0")
    token_file = os.environ.get("VAULT_TOKEN_FILE", "/var/lib/swirl-cloud/vault-bootstrap-token")
    vault_token = subprocess.run(["sudo", "cat", token_file], capture_output=True, text=True, check=True).stdout.strip()

    def vault(script, values=()):
        return kubectl("-n", "infra", "exec", "-i", pod, "-c", "vault", "--", "sh", "-ceu",
                       "IFS= read -r VAULT_TOKEN; export VAULT_TOKEN VAULT_ADDR=http://127.0.0.1:8200; " + script,
                       stdin="\n".join([vault_token, *values]) + "\n")

    def store(field, value):
        vault(f'IFS= read -r value; vault kv patch {VAULT_PATH} {field}="$value" >/dev/null', [value])

    stored = json.loads(vault(f"vault kv get -format=json {VAULT_PATH}"))["data"]["data"]
    service = json.loads(kubectl("-n", namespace, "get", "service", "sonarqube", "-o", "json"))
    sonar = Sonar(os.environ.get("SONAR_URL") or f"http://{service['spec']['clusterIP']}:9000")
    sonar.wait_until_up()
    result = ensure(sonar, stored, store)
    if result == "created":
        # The SSO bootstrap Job waits for this Secret; refresh it now rather than at the next interval.
        kubectl("-n", namespace, "annotate", "externalsecret", "sonarqube-sso-admin-token",
                f"force-sync={int(time.time())}", "--overwrite")
        print("Created SonarQube's automation token and stored it in Vault infra/sonarqube")
    else:
        print("SonarQube's automation token in Vault is valid")


if __name__ == "__main__":
    try:
        main()
    except (SonarError, subprocess.CalledProcessError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        sys.exit(1)
