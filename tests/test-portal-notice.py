#!/usr/bin/env python3
"""Guards for portal notices, run with a fake kubectl: a notice needs a service
the portal lists (or *), an end after its start and in the future, at most 31
days away; times without a zone are UTC; adding one drops ended notices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICES = "- DevOps & Delivery:\n    - GitLab:\n        href: https://gitlab.example.com\n- Observability:\n    - Grafana:\n        href: https://grafana.example.com\n"
FAKE = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    from pathlib import Path
    args = [a for a in sys.argv[1:] if not a.startswith("--request-timeout")]
    store = Path(os.environ["STORE"])
    if args[:2] == ["apply", "-f"]:
        store.write_text(sys.stdin.read())
    elif "homepage-config" in args:
        print(json.dumps({"data": {"services.yaml": os.environ["SERVICES"]}}))
    elif "portal-notices" in args:
        if not store.exists():
            sys.exit(1)
        print(store.read_text())
    """)


class PortalNotice(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        fake = self.directory / "kubectl"
        fake.write_text(FAKE)
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.directory}:{os.environ['PATH']}", STORE=str(self.directory / "store.json"), SERVICES=SERVICES)

    def notice(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts/portal-notice.py"), *args], env=self.env, capture_output=True, text=True, timeout=60)

    def stored(self):
        return json.loads(json.loads((self.directory / "store.json").read_text())["data"]["notices.json"])

    def test_add_checks_service_and_times(self):
        from datetime import datetime, timedelta, timezone
        soon = datetime.now(timezone.utc) + timedelta(hours=2)
        start, end = soon.strftime("%Y-%m-%dT%H:%M"), (soon + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
        self.assertIn("No service 'Kibana'", self.notice("add", "--service", "Kibana", "--from", start, "--until", end, "--en", "Down").stderr)
        self.assertIn("after --from", self.notice("add", "--service", "GitLab", "--from", end, "--until", start, "--en", "Down").stderr)
        far = (soon + timedelta(days=40)).strftime("%Y-%m-%dT%H:%M")
        self.assertIn("at most 31 days", self.notice("add", "--service", "GitLab", "--from", start, "--until", far, "--en", "Down").stderr)
        result = self.notice("add", "--service", "GitLab", "--from", start, "--until", end, "--en", "GitLab  upgrade", "--fr", "Mise à jour")
        self.assertEqual(result.returncode, 0, result.stderr)
        [notice] = self.stored()
        self.assertEqual((notice["service"], notice["en"], notice["fr"]), ("GitLab", "GitLab upgrade", "Mise à jour"))
        self.assertTrue(notice["starts"].endswith("+00:00"))

    def test_adding_drops_ended_notices_and_remove_works(self):
        old = [{"id": "old", "service": "GitLab", "starts": "2020-01-01T00:00:00+00:00", "ends": "2020-01-01T01:00:00+00:00", "en": "Past"}]
        (self.directory / "store.json").write_text(json.dumps({"data": {"notices.json": json.dumps(old)}}))
        result = self.notice("add", "--service", "*", "--from", "2026-01-01T00:00", "--until", "2999-01-01T00:00+01:00", "--en", "Everything")
        self.assertIn("at most 31 days", result.stderr)
        from datetime import datetime, timedelta, timezone
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertEqual(self.notice("add", "--service", "*", "--from", "2026-01-01T00:00", "--until", end, "--en", "Everything").returncode, 0)
        [notice] = self.stored()
        self.assertEqual(notice["service"], "*")
        self.assertEqual(self.notice("remove", notice["id"]).returncode, 0)
        self.assertEqual(self.stored(), [])
        self.assertNotEqual(self.notice("remove", "nope").returncode, 0)


if __name__ == "__main__":
    unittest.main()
