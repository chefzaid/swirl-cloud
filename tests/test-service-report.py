#!/usr/bin/env python3
"""Guards for the monthly service report: hours inside a declared maintenance
window for an endpoint's service (or for *) are left out, other endpoints keep
them, and an endpoint under the objective is marked."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("report", ROOT / "scripts/service-report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
HOURS = [START + timedelta(hours=n) for n in range(100)]


class ServiceReport(unittest.TestCase):
    def test_month_bounds(self):
        self.assertEqual((START, datetime(2026, 11, 1, tzinfo=timezone.utc)), report.month_bounds("2026-10"))
        self.assertEqual(datetime(2027, 1, 1, tzinfo=timezone.utc), report.month_bounds("2026-12")[1])

    def test_maintenance_hours_are_left_out_for_that_service_only(self):
        gitlab = {hour: (0.0 if 10 <= n < 12 else 1.0) for n, hour in enumerate(HOURS)}
        grafana = dict(gitlab)
        windows = [{"service": "GitLab", "starts": "2026-10-01T10:00:00+00:00", "ends": "2026-10-01T12:00:00+00:00", "en": "Upgrade"}]
        hosts = {"GitLab": "gitlab.example.com", "Grafana": "grafana.example.com"}
        rows = report.summarise({"https://gitlab.example.com/": gitlab, "https://grafana.example.com/": grafana}, windows, hosts, 0.995)
        by = {r["endpoint"]: r for r in rows}
        self.assertEqual((98, 2, 0, True), tuple(by["https://gitlab.example.com/"][k] for k in ("hours", "excluded", "degraded_hours", "met")))
        self.assertEqual((100, 0, 2, False), tuple(by["https://grafana.example.com/"][k] for k in ("hours", "excluded", "degraded_hours", "met")))
        everything = [{"service": "*", "starts": "2026-10-01T10:30:00+00:00", "ends": "2026-10-01T11:15:00+00:00", "en": "Network"}]
        rows = report.summarise({"https://grafana.example.com/": grafana}, everything, hosts, 0.995)
        self.assertEqual(2, rows[0]["excluded"])
        text = report.render("2026-10", rows, everything, 0.995, datetime(2026, 11, 2, tzinfo=timezone.utc))
        self.assertIn("1 of 1 endpoints met the objective", text)
        self.assertIn("2026-10-01 10:30 to 2026-10-01 11:15 UTC, *: Network", text)


if __name__ == "__main__":
    unittest.main()
