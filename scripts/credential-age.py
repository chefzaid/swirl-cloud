#!/usr/bin/env python3
"""Age of every credential stored in Vault, against a rotation limit.

Lists every secret in Vault's KV mounts with its current version and when it
was last written, oldest first, and flags those older than --max-age-days
(default 365). Values are never read: only KV metadata is queried. The Vault
bootstrap token is read with passwordless sudo and passed to the Vault pod on
standard input, never in arguments or the environment of this host.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TOKEN_FILE = "/var/lib/swirl-cloud/vault-bootstrap-token"

# Runs inside the Vault pod: walk each KV mount and print metadata as JSON
# lines between markers. The token arrives on standard input.
WALK = r'''
IFS= read -r token
export VAULT_TOKEN="$token" VAULT_ADDR=http://127.0.0.1:8200
unset token
walk() {
  vault kv list "$1" 2>/dev/null | tail -n +3 | while IFS= read -r key; do
    case "$key" in
      */) walk "$1$key" ;;
      *) echo "##SECRET $1$key"; vault kv metadata get -format=json "$1$key" 2>/dev/null || echo '{}' ;;
    esac
  done
}
for mount in $(vault secrets list 2>/dev/null | awk '$2 == "kv" {print $1}'); do
  walk "$mount"
done
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-age-days", type=int, default=365)
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    args = parser.parse_args()
    token = subprocess.run(["sudo", "-n", "cat", TOKEN_FILE], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if token.returncode or not token.stdout.strip():
        raise SystemExit(f"Cannot read {TOKEN_FILE} with passwordless sudo; run on a control plane.")
    pod = subprocess.run(["bash", "-c", f"source {ROOT / 'scripts/lib/vault-access.sh'}; vault_runtime_pod"],
                         capture_output=True, text=True)
    if pod.returncode:
        raise SystemExit(pod.stderr.strip() or "No unsealed Vault pod")
    result = subprocess.run(["kubectl", "--request-timeout=120s", "-n", "infra", "exec", "-i", pod.stdout.strip(), "-c", "vault",
                             "--", "sh", "-c", WALK], input=token.stdout.strip() + "\n", capture_output=True, text=True, timeout=600)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "Vault metadata listing failed")
    now = datetime.now(timezone.utc)
    rows, path, buffer = [], None, []

    def flush():
        if not path:
            return
        data = json.loads("\n".join(buffer) or "{}").get("data", {})
        updated = data.get("updated_time") or data.get("created_time")
        when = datetime.fromisoformat(updated.replace("Z", "+00:00")[:26] + "+00:00") if updated else None
        rows.append({"path": path, "version": data.get("current_version"), "updated": when.isoformat(timespec="minutes") if when else "",
                     "age_days": (now - when).days if when else None,
                     "overdue": bool(when) and (now - when).days > args.max_age_days})

    for line in result.stdout.splitlines():
        if line.startswith("##SECRET "):
            flush()
            path, buffer = line.removeprefix("##SECRET "), []
        else:
            buffer.append(line)
    flush()
    rows.sort(key=lambda r: -(r["age_days"] or 0))
    overdue = [r for r in rows if r["overdue"]]
    if args.format == "json":
        print(json.dumps({"generated_at": now.isoformat(timespec="minutes"), "max_age_days": args.max_age_days, "secrets": rows}, indent=2))
        return 0
    header = (f"{len(rows)} Vault secrets; {len(overdue)} older than {args.max_age_days} days; "
              f"oldest {rows[0]['age_days'] if rows else 0} days ({rows[0]['path'] if rows else '-'}).")
    if args.format == "markdown":
        print(f"# Credential age\n\n{header}\n\n| Secret | Version | Last written (UTC) | Age (days) |\n|---|---|---|---|")
        for r in rows:
            print(f"| {r['path']}{' **(over limit)**' if r['overdue'] else ''} | {r['version']} | {r['updated']} | {r['age_days']} |")
    else:
        print(header)
        for r in rows:
            print(f"  {'OVER ' if r['overdue'] else '     '}{r['age_days']:>5}d  v{r['version']:<3} {r['path']}")
    return 1 if overdue else 0


if __name__ == "__main__":
    sys.exit(main())
