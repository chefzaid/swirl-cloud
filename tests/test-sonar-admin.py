#!/usr/bin/env python3
"""Guards for SonarQube's automation token: a first installation leaves no default
admin password and gets a token; a valid token is kept; the password reaches Vault
before SonarQube changes; credentials that match nothing stop with an error."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sonar_admin", ROOT / "scripts/configure-sonar-admin.py")
sonar_admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sonar_admin)


class FakeSonar(sonar_admin.Sonar):
    """Answers the API calls as SonarQube does, for one admin account."""

    def __init__(self, password="admin", tokens=()):
        super().__init__("http://sonar.invalid")
        self.password, self.tokens, self.calls = password, set(tokens), []

    def call(self, method, path, authorization=None, data=None):
        self.calls.append((method, path, dict(data or {})))
        valid = authorization in ({sonar_admin.basic("admin", self.password)} |
                                  {sonar_admin.bearer(token) for token in self.tokens})
        if path == "api/users/current":
            return {"isLoggedIn": valid, "login": "admin" if valid else None}
        if not valid:
            raise sonar_admin.SonarError(f"SonarQube {method} {path}: HTTP 401")
        if path == "api/users/change_password":
            assert data["previousPassword"] == self.password
            self.password = data["password"]
            return {}
        if path == "api/user_tokens/generate":
            token = "squ_" + str(len(self.tokens))
            self.tokens.add(token)
            return {"login": "admin", "name": data["name"], "token": token}
        raise AssertionError(path)


class SonarAdmin(unittest.TestCase):
    def setUp(self):
        self.vault, self.writes = {}, []

    def store(self, field, value):
        self.writes.append(field)
        self.vault[field] = value

    def test_a_first_installation_leaves_no_default_password_and_gets_a_token(self):
        sonar = FakeSonar()
        self.assertEqual("created", sonar_admin.ensure(sonar, dict(self.vault), self.store, lambda: "New-Passw0rd!"))
        self.assertEqual(["admin_password", "admin_token"], self.writes, "the password must reach Vault first")
        self.assertEqual("New-Passw0rd!", sonar.password)
        self.assertIsNone(sonar.login(sonar_admin.basic("admin", "admin")))
        self.assertEqual("admin", sonar.login(sonar_admin.bearer(self.vault["admin_token"])))

    def test_a_valid_token_is_kept(self):
        sonar = FakeSonar(password="Kept-Passw0rd!", tokens={"squ_kept"})
        self.vault.update(admin_password="Kept-Passw0rd!", admin_token="squ_kept")
        self.assertEqual("unchanged", sonar_admin.ensure(sonar, dict(self.vault), self.store))
        self.assertEqual([], self.writes)
        self.assertFalse(any(path != "api/users/current" for _, path, _ in sonar.calls))

    def test_a_revoked_token_is_replaced_with_the_stored_password(self):
        sonar = FakeSonar(password="Kept-Passw0rd!")
        self.vault.update(admin_password="Kept-Passw0rd!", admin_token="squ_revoked")
        self.assertEqual("created", sonar_admin.ensure(sonar, dict(self.vault), self.store))
        self.assertEqual(["admin_token"], self.writes)
        self.assertFalse(any(path == "api/users/change_password" for _, path, _ in sonar.calls))

    def test_a_password_matching_nothing_stops_without_changes(self):
        sonar = FakeSonar(password="Someone-Elses1!")
        self.vault.update(admin_password="Vault-Passw0rd!")
        with self.assertRaises(sonar_admin.SonarError):
            sonar_admin.ensure(sonar, dict(self.vault), self.store)
        self.assertEqual([], self.writes)
        self.assertEqual("Someone-Elses1!", sonar.password)

    def test_generated_passwords_meet_the_sonarqube_policy(self):
        for _ in range(50):
            value = sonar_admin.generate_password()
            self.assertGreaterEqual(len(value), 12)
            self.assertTrue(any(c.islower() for c in value) and any(c.isupper() for c in value)
                            and any(c.isdigit() for c in value) and any(not c.isalnum() for c in value))


if __name__ == "__main__":
    unittest.main()
