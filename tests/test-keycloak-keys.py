#!/usr/bin/env python3
"""Guards for Keycloak realm key rotation: rotate adds one key of each generated
kind above the current priority and makes only the active generated ones
passive; retire deletes passive keys only once the newest rotation of their
kind is 31 days old; keys of other providers are never touched."""
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("keys", ROOT / "scripts/rotate-keycloak-keys.py")
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


def key(name, provider, priority, active=True, **config):
    return {"id": name, "name": name, "providerId": provider,
            "config": {"priority": [str(priority)], "active": ["true" if active else "false"], **config}}


DEFAULTS = [key("rsa-generated", "rsa-generated", 100), key("rsa-enc-generated", "rsa-enc-generated", 100),
            key("hmac-generated-hs512", "hmac-generated-hs512", 100, algorithm=["HS512"]),
            key("aes-generated", "aes-generated", 100), key("imported", "rsa", 200)]


class KeycloakKeys(unittest.TestCase):
    def test_rotation_adds_above_and_passivates_only_generated_keys(self):
        additions, passive = keys.plan_rotation(DEFAULTS, "realm-id", "20261115")
        self.assertEqual(sorted(a["name"] for a in additions),
                         ["aes-generated-20261115", "hmac-generated-hs512-20261115", "rsa-generated-20261115"])
        self.assertTrue(all(a["config"]["priority"] == ["200"] and a["parentId"] == "realm-id" for a in additions))
        self.assertEqual(sorted(p["name"] for p in passive), ["aes-generated", "hmac-generated-hs512", "rsa-generated"])
        hmac = next(a for a in additions if a["providerId"] == "hmac-generated-hs512")
        self.assertEqual(hmac["config"]["algorithm"], ["HS512"])

    def test_retirement_waits_for_the_newest_rotation_to_age(self):
        rotated = [key("rsa-generated", "rsa-generated", 100, active=False), key("rsa-generated-20261001", "rsa-generated", 200),
                   key("hmac-generated-hs512", "hmac-generated-hs512", 100, active=False, algorithm=["HS512"]),
                   key("hmac-generated-hs512-20261110", "hmac-generated-hs512", 200, algorithm=["HS512"]),
                   key("imported", "rsa", 50, active=False)]
        now = datetime(2026, 11, 15, tzinfo=timezone.utc)
        self.assertEqual([c["name"] for c in keys.plan_retirement(rotated, now, 31)], ["rsa-generated"])
        self.assertEqual(keys.plan_retirement(rotated, datetime(2026, 10, 20, tzinfo=timezone.utc), 31), [])
        # An undated active key (made by hand) keeps everything.
        manual = rotated + [key("rsa-by-hand", "rsa-generated", 300)]
        self.assertEqual([c["name"] for c in keys.plan_retirement(manual, now, 31)], [])

    def test_legacy_hs256_hmac_keys_are_not_managed(self):
        self.assertIsNone(keys.kind(key("hmac-generated", "hmac-generated", 100, algorithm=["HS256"])))
        self.assertEqual(keys.kind(key("hmac-generated", "hmac-generated", 100, algorithm=["HS512"])), "hmac-generated-hs512")


if __name__ == "__main__":
    unittest.main()
