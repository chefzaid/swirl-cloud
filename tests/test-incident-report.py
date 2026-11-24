#!/usr/bin/env python3
"""Guards for the customer notice of --incident-report: English and French, the
affected endpoints and times filled in, an ongoing incident said to be in
progress, and placeholders when nothing was measured as affected."""
from datetime import datetime
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("incident", ROOT / "scripts/incident-report.py")
incident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(incident)
START, END, AWARE = (datetime.fromisoformat(t) for t in ("2026-11-20T01:00+00:00", "2026-11-20T03:00+00:00", "2026-11-20T01:20+00:00"))


class CustomerNotice(unittest.TestCase):
    def test_finished_incident_in_both_languages(self):
        evidence = {"availability": {"https://registry.example.com/v2/": 0.8, "https://gitlab.example.com/": 1.0}}
        text = incident.customer_notice("Registry down", START, END, AWARE, evidence, ongoing=False)
        self.assertIn("## English", text)
        self.assertIn("## Français", text)
        self.assertIn("It lasted from 2026-11-20 01:00 UTC to 2026-11-20 03:00 UTC", text)
        self.assertIn("Il a duré du 2026-11-20 01:00 UTC au 2026-11-20 03:00 UTC", text)
        self.assertIn("affecting https://registry.example.com/v2/", text)
        self.assertNotIn("gitlab.example.com", text)

    def test_ongoing_incident_without_measured_impact(self):
        text = incident.customer_notice("Suspected intrusion", START, END, AWARE, {}, ongoing=True)
        self.assertIn("started on 2026-11-20 01:00 UTC and is still in progress", text)
        self.assertIn("n'est pas encore résolu", text)
        self.assertIn("[services affected]", text)


if __name__ == "__main__":
    unittest.main()
