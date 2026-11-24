#!/usr/bin/env python3
"""Acceptance record for a vendor-assisted installation (TODO §39).

  acceptance-record.py --customer "Example SAS" [--output FILE]

Run on the control plane once the installation is finished. It gathers the
acceptance evidence: `verify-platform.py --strict` (every check, warnings
included), the restore drill and the recovery kit's export and check from the
control calendar (both within the last 30 days), and a working sign-in through
Keycloak from the sign-in probe. It writes a Markdown record with the results, the
handover list and signature lines for the customer and the installer, and exits
1 unless every item passed. Docs: docs/guides/customer-installation.md.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import duties  # noqa: E402

PROXY = "/api/v1/namespaces/infra/services/prometheus:9090/proxy"
RECENT = timedelta(days=30)


def verification():
    """(passed, summary lines) from the strict platform verification."""
    result = subprocess.run([sys.executable, str(ROOT / "scripts/verify-platform.py"), "--json", "--strict"],
                            capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    try:
        checks = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, [f"verify-platform.py did not run: {(result.stderr.strip().splitlines() or ['no output'])[-1]}"]
    failing = [f"{c['status']}: {c['check']}: {c['summary']}" for c in checks if c["status"] != "PASS"]
    return result.returncode == 0 and not failing, failing or [f"all {len(checks)} checks passed"]


def duty(name, completions, now):
    entry = completions.get(name)
    if not entry:
        return False, "not recorded"
    done = datetime.strptime(entry["done"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    note = f"done {entry['done'][:10]} by {entry.get('by', 'unknown')}" + (f" ({entry['note']})" if entry.get("note") else "")
    return now - done <= RECENT, note if now - done <= RECENT else f"{note}, more than 30 days ago"


def sign_in():
    result = subprocess.run(["kubectl", "--request-timeout=60s", "get", "--raw",
                             f"{PROXY}/api/v1/query?{urlencode({'query': 'min(swirl_signin_probe_success)'}, quote_via=quote)}"],
                            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if result.returncode:
        return False, "Prometheus could not be queried"
    values = json.loads(result.stdout).get("data", {}).get("result", [])
    if not values:
        return False, "no sign-in probe result (is the signin-probe Deployment running?)"
    return values[0]["value"][1] == "1", "the probe account signs in through Keycloak" if values[0]["value"][1] == "1" else "the sign-in probe fails"


def record(customer, items, now):
    passed = all(ok for _, ok, _ in items)
    lines = [f"# Installation acceptance: {customer}", "", f"Recorded {now:%Y-%m-%d %H:%M} UTC.", "", "| Item | Result | Detail |", "|---|---|---|"]
    for name, ok, detail in items:
        lines.append(f"| {name} | {'passed' if ok else '**not passed**'} | {'; '.join(detail) if isinstance(detail, list) else detail} |")
    lines += ["", "## Handed over to the customer", "",
              "- [ ] The recovery kit and its passphrase, in two separate places the customer chose; the installer keeps no copy",
              "- [ ] The administrator accounts: the first Keycloak administrator's password changed by the customer",
              "- [ ] The Vault unseal key and root token handling, as recorded in the recovery kit",
              "- [ ] The servers' provider console access, in the customer's name",
              "- [ ] The installation's inputs file and the documentation of the installed version", "",
              "## Signatures", "",
              f"Accepted: {'yes' if passed else 'no, see the items not passed'}", "",
              "| | Name | Date | Signature |", "|---|---|---|---|",
              f"| Customer ({customer}) | | | |", "| Installer | | | |", ""]
    return "\n".join(lines), passed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--customer", required=True)
    parser.add_argument("--output", help="write the record here instead of standard output")
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    completions = duties.recorded()
    items = [("Platform verification (strict)", *verification()),
             ("Restore drill on a disposable host", *duty("restore-drill", completions, now)),
             ("Recovery kit exported and checked", *duty("recovery-kit", completions, now)),
             ("Sign-in through Keycloak", *sign_in())]
    text, passed = record(args.customer, items, now)
    if args.output:
        Path(args.output).write_text(text)
        print(f"Wrote {args.output}: {'accepted' if passed else 'not accepted yet'}.")
    else:
        print(text, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
