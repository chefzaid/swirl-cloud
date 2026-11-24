#!/usr/bin/env python3
"""Guards for the monthly service report: hours inside a declared maintenance
window for an endpoint's service (or for *) are left out, other endpoints keep
them, and an endpoint under the objective is marked. Incidents come from the
register as the Incident template records them, support response times from the
month's tickets, and a source that cannot be read is named, not fatal."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

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


DESCRIPTION = """## Summary

- **Aware since:** 2026-10-05 01:20 UTC (the reporting clocks start here)
- **Started (estimated):** 2026-10-05 01:00
- **Resolved:** 2026-10-05 03:15 UTC
- **Detected by:** alert
- **Severity:** SEV2 (degraded or contained) / SEV3 (minor)
- **Services affected:** GitLab, Registry
"""


class Incidents(unittest.TestCase):
    def test_the_template_fields_give_severity_times_and_services(self):
        rows = report.incident_rows([
            {"reference": "acme/swirl-cloud#12", "title": "Registry unavailable", "created_at": "2026-10-05T01:30:00+00:00",
             "closed_at": "2026-10-06T09:00:00+00:00", "description": DESCRIPTION},
            {"reference": "acme/swirl-cloud#14", "title": "Slow sign-in", "created_at": "2026-10-20T08:00:00+00:00",
             "closed_at": None, "description": "- **Severity:** SEV1 / SEV2 / SEV3\n"}])
        first, second = rows
        self.assertEqual(("SEV2", "GitLab, Registry", 135), (first["severity"], first["services"], first["minutes"]))
        self.assertEqual(datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc), first["started"])
        self.assertEqual(("SEV1", None, None, "not recorded"), (second["severity"], second["resolved"], second["minutes"], second["services"]))
        self.assertEqual(datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc), second["started"])
        text = report.render("2026-10", [], [], 0.995, datetime(2026, 11, 2, tzinfo=timezone.utc), (rows, None), ({"opened": 0}, None))
        self.assertIn("| acme/swirl-cloud#12 Registry unavailable | SEV2 | 2026-10-05 01:00 | 2026-10-05 03:15 | 2 h 15 min | GitLab, Registry |", text)
        self.assertIn("| acme/swirl-cloud#14 Slow sign-in | SEV1 | 2026-10-20 08:00 | not resolved | ongoing | not recorded |", text)
        self.assertIn("No ticket opened this month.", text)

    def test_closing_time_stands_in_for_an_unfilled_resolved_field(self):
        row = report.incident_rows([{"reference": "r#1", "title": "t", "created_at": "2026-10-01T10:00:00+00:00",
                                     "closed_at": "2026-10-01T10:45:00+00:00", "description": "- **Resolved:**\n"}])[0]
        self.assertEqual((45, "not set"), (row["minutes"], row["severity"]))

    def test_the_register_is_asked_for_the_month(self):
        with mock.patch.object(report, "run", return_value='noise\nSWIRL_JSON[{"reference": "r#1"}]\n') as run:
            self.assertEqual([{"reference": "r#1"}], report.incidents(START, datetime(2026, 11, 1, tzinfo=timezone.utc)))
        self.assertEqual("2026-10-01T00:00:00+00:00 2026-11-01T00:00:00+00:00\n", run.call_args.kwargs["stdin"])
        self.assertIn("gitlab-rails", run.call_args.args)
        with mock.patch.object(report, "run", return_value="no marker"), self.assertRaisesRegex(RuntimeError, "no answer"):
            report.incidents(START, START)


class Support(unittest.TestCase):
    def test_first_response_times(self):
        tickets = [{"number": str(n), "created_at": "2026-10-02T09:00:00", "first_response_at": f"2026-10-02T{9 + n:02d}:00:00",
                    "close_at": "2026-10-03T09:00:00" if n < 3 else None} for n in range(1, 11)]
        tickets.append({"number": "99", "created_at": "2026-10-31T12:00:00+00:00", "first_response_at": None, "close_at": None})
        summary = report.support_summary(tickets, datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc))
        self.assertEqual((11, 10, 2, 1), (summary["opened"], summary["answered"], summary["closed"], summary["waiting"]))
        self.assertEqual((330.0, 540.0, 1440.0), (summary["median"], summary["p90"], summary["longest_waiting"]))
        text = report.render("2026-10", [], [], 0.995, datetime(2026, 11, 2, tzinfo=timezone.utc), ([], None), (summary, None))
        self.assertIn("11 tickets opened, 10 answered, 2 closed. First response (wall-clock time): median 5 h 30 min,"
                      " 90th percentile 9 h 00 min.", text)
        self.assertIn("1 without a first response yet, the oldest waiting 24 h 00 min.", text)
        self.assertIn("None recorded in the incident register.", text)

    def test_the_month_goes_to_psql_as_variables(self):
        calls = []

        def run(*command, stdin=None, timeout=300):
            calls.append((command, stdin))
            return "postgres-0\n" if command[0] == "bash" else '{"number": "1"}\nnot json\n'
        with mock.patch.object(report, "run", run):
            self.assertEqual([{"number": "1"}], report.support_tickets(START, datetime(2026, 11, 1, tzinfo=timezone.utc)))
        command, stdin = calls[1]
        self.assertIn("postgres-0", command)
        self.assertIn("-d zammad", command[-1])
        self.assertTrue(stdin.startswith("2026-10-01T00:00:00+00:00\n2026-11-01T00:00:00+00:00\nSELECT"))
        self.assertIn(":'start'::timestamptz", stdin)


class UnreadSources(unittest.TestCase):
    def test_a_source_that_cannot_be_read_is_named(self):
        def broken(*args):
            raise RuntimeError("deployments.apps \"gitlab\" not found")
        result = report.attempt(broken, START, START)
        self.assertEqual((None, 'deployments.apps "gitlab" not found'), result)
        text = report.render("2026-10", [], [], 0.995, datetime(2026, 11, 2, tzinfo=timezone.utc), result, (None, "psql failed"))
        self.assertIn('Not read from the incident register: deployments.apps "gitlab" not found.', text)
        self.assertIn("Not read from the support desk: psql failed.", text)


if __name__ == "__main__":
    unittest.main()
