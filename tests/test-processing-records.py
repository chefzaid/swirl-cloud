#!/usr/bin/env python3
"""Guards for the GDPR Article 30 draft: an application's owner, classification,
hosts, volumes, data services and external processors come from its resources
(credential names, never values), and every platform activity is listed."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("records", ROOT / "scripts/processing-records.py")
records = importlib.util.module_from_spec(spec)
spec.loader.exec_module(records)

APP = {"metadata": {"name": "shop-prod"}, "status": {"resources": [
    {"kind": "Deployment", "namespace": "apps-shop-prod", "name": "shop"},
    {"kind": "Ingress", "namespace": "apps-shop-prod", "name": "shop"},
    {"kind": "PersistentVolumeClaim", "namespace": "apps-shop-prod", "name": "data"},
    {"kind": "ExternalSecret", "namespace": "apps-shop-prod", "name": "shop"}]}}
OBJECTS = {
    ("deployment", "shop"): {"metadata": {"labels": {"swirl-cloud.io/owner": "shop-team", "swirl-cloud.io/data-classification": "confidential"}}},
    ("ingress", "shop"): {"spec": {"rules": [{"host": "shop.example.com"}]}},
    ("pvc", "data"): {"spec": {"resources": {"requests": {"storage": "5Gi"}}, "storageClassName": "longhorn-encrypted"}},
    ("externalsecret", "shop"): {"spec": {"data": [{"remoteRef": {"key": "apps/shop/prod/database", "property": "password"}},
                                                   {"remoteRef": {"key": "apps/shop/prod/integrations", "property": "stripe_secret_key"}}],
                                          "dataFrom": [{"extract": {"key": "apps/shop/prod/redis"}}]}}}


class ProcessingRecords(unittest.TestCase):
    def test_application_facts_come_from_its_resources(self):
        with mock.patch.object(records, "kubectl_json", side_effect=lambda verb, kind, name, *rest: OBJECTS[(kind, name)]):
            app = records.application(APP)
        self.assertEqual(["shop-team"], app["owners"])
        self.assertEqual(["confidential"], app["classification"])
        self.assertEqual(["shop.example.com"], app["hosts"])
        self.assertEqual(["apps-shop-prod/data (5Gi, longhorn-encrypted)"], app["volumes"])
        self.assertEqual(["PostgreSQL (shared server in infra)", "Redis (shared cache in infra)"], app["services"])
        self.assertEqual(["Stripe (payments)"], app["processors"])

    def test_render_lists_platform_activities(self):
        text = records.render("Example SAS", ["node-1"], [], [("Container logs", "1 day")])
        for activity in ("Keycloak", "GitLab", "Zammad", "Vaultwarden", "Odoo"):
            self.assertIn(activity, text)
        self.assertIn("| Container logs | 1 day |", text)
        self.assertIn("for Example SAS", text)


if __name__ == "__main__":
    unittest.main()
