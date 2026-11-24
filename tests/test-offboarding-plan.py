#!/usr/bin/env python3
"""Guards for the offboarding plan: it lists what each source holds for the
application, reports a source it cannot read instead of stopping, and only
matches the application's own names."""
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("plan", ROOT / "scripts/offboarding-plan.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)

APPS = {"items": [
    {"metadata": {"name": "shop-prod"}, "spec": {"source": {"repoURL": "https://gitlab.example.com/acme/shop.git"}, "destination": {"namespace": "apps-shop-prod"}}},
    {"metadata": {"name": "shopping-int"}, "spec": {"source": {"repoURL": "https://gitlab.example.com/acme/shopping.git"}, "destination": {"namespace": "apps-shopping-int"}}},
    {"metadata": {"name": "swirl-cloud"}, "spec": {"source": {"repoURL": "https://gitlab.example.com/acme/swirl-cloud.git"}, "destination": {"namespace": "infra"}}}]}


class OffboardingPlan(unittest.TestCase):
    def test_applications_match_only_the_application(self):
        with mock.patch.object(plan, "kubectl_json", return_value=APPS):
            self.assertEqual(["shop-prod"], [a["name"] for a in plan.applications("shop")])

    def test_a_failing_source_is_listed_not_fatal(self):
        def broken(app):
            raise RuntimeError("Kafka is not running")
        sources = {"applications": lambda app: [{"name": "shop-prod", "namespace": "apps-shop-prod", "repository": "r"}],
                   "namespaced": lambda namespaces, app: (["shop.example.com"], ["apps-shop-prod/data (5Gi)"], ["apps/shop/prod/database"]),
                   "postgres": lambda app: ["database shop_prod", "role shop_prod"], "kafka": broken,
                   "keycloak": lambda app: ["staff: shop-prod"]}
        result = plan.plan("shop", sources)
        self.assertEqual(["Kafka: Kafka is not running"], result["not_checked"])
        text = plan.render(result)
        for expected in ("- shop.example.com", "- apps/shop/prod/database", "- role shop_prod", "- staff: shop-prod", "## Not checked"):
            self.assertIn(expected, text)
        self.assertIn("Read-only inventory; nothing was changed.", text)

    def test_postgres_prefix_matches_underscores_literally(self):
        captured = {}
        def run(*command, stdin=None):
            if command[0] == "bash":
                return "postgres-abc\n"
            captured["sql"] = stdin
            return "role shop_prod\n"
        with mock.patch.object(plan, "run", side_effect=run):
            self.assertEqual(["role shop_prod"], plan.postgres("shop"))
        self.assertIn("LIKE 'shop\\_%' ESCAPE '\\'", captured["sql"])


if __name__ == "__main__":
    unittest.main()
