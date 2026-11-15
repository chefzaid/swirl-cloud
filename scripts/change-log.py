#!/usr/bin/env python3
"""Change records: every change to the platform and production in one list.

  ./install.sh --change-log [--since 2026-11-01] [--format markdown|json]

Assembled read-only from what already records changes: commits to this
repository's deployed configuration (k8s/, config/), Argo CD's sync history of
the root Application and production Applications, installer and Ansible runs
(/var/lib/swirl-cloud/install-runs), time-limited access grants (Elasticsearch
access-grants) and production deployment approvals (infra/swirl-cloud-prod-approvals). Each entry names who, the approver where one is
recorded, the revision, the result and the way back; entries without an approver
are flagged for review (ISO/IEC 27001 A.8.32). Secret values are never read.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNS = Path("/var/lib/swirl-cloud/install-runs")


def run(*command, stdin=None):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f"{command[0]}: {(result.stderr.strip().splitlines() or ['failed'])[-1][:200]}")
    return result.stdout


def when(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def entry(at, change, who, approved, revision, result, back, source):
    return {"at": when(at).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "change": change, "who": who or "unknown",
            "approved_by": approved or "", "revision": revision or "", "result": result, "way_back": back, "source": source}


def git_changes(since):
    output = run("git", "-C", str(ROOT), "log", f"--since={since.isoformat()}", "--format=%H%x1f%an%x1f%aI%x1f%s",
                 "--", "k8s", "config")
    rows = []
    for line in output.splitlines():
        sha, author, at, subject = line.split("\x1f", 3)
        rows.append(entry(at, f"Platform configuration: {subject}", author, "", sha[:12], "pushed to main",
                          f"git revert {sha[:12]}", "git"))
    return rows


def argocd_changes(since):
    applications = json.loads(run("kubectl", "--request-timeout=30s", "-n", "infra", "get", "applications.argoproj.io", "-o", "json"))
    rows = []
    for application in applications["items"]:
        name = application["metadata"]["name"]
        labels = application["metadata"].get("labels") or {}
        if name != "swirl-cloud" and labels.get("swirl-cloud/environment") != "prod":
            continue
        history = application.get("status", {}).get("history") or []
        for previous, item in zip([None] + history[:-1], history):
            if when(item.get("deployedAt", "1970-01-01T00:00:00Z")) < since:
                continue
            revision = item.get("revision") or ",".join(item.get("revisions") or [])
            back = f"argocd app rollback {name} {previous['id']}" if previous else "no earlier sync recorded"
            rows.append(entry(item["deployedAt"], f"Deployed {name}", item.get("initiatedBy", {}).get("username") or
                              ("automated sync" if item.get("initiatedBy", {}).get("automated") else ""),
                              "", revision[:12], "synced", back, "argocd"))
    return rows


def installer_changes(since):
    try:
        names = run("sudo", "-n", "find", str(RUNS), "-maxdepth", "1", "-name", "*.jsonl").split()
    except RuntimeError:
        return []
    rows = []
    for path in sorted(names):
        events = [json.loads(line) for line in run("sudo", "-n", "cat", path).splitlines() if line.startswith("{")]
        start = next((event for event in events if event.get("event") == "start"), None)
        if not start or when(start["at"]) < since:
            continue
        finish = next((event for event in events if event.get("event") == "finish"), {})
        rows.append(entry(start["at"], f"Installer run ({start.get('mode', 'install')}) on {start.get('host', '')}",
                          start.get("user", ""), "", start.get("revision", ""), finish.get("result", "no finish recorded"),
                          "rerun ./install.sh at the previous revision", "installer"))
    return rows


def grant_changes(since):
    script = ('curl -sS -u "elastic:$ELASTIC_PASSWORD" -H "Content-Type: application/json" --data-binary @- '
              '"localhost:9200/access-grants/_search?ignore_unavailable=true"')
    body = {"size": 500, "sort": [{"granted_at": "asc"}], "query": {"range": {"granted_at": {"gte": since.isoformat()}}}}
    response = json.loads(run("kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/elasticsearch", "--",
                              "sh", "-c", script, stdin=json.dumps(body)))
    def change(grant):
        kind = grant.get("kind") or "admin"
        what = {"admin": "Administrator access", "role": f"Role {grant.get('group', '')}", "guest": "Guest account end date"}[kind]
        option = {"admin": "", "role": f" --group {grant.get('group', '')}", "guest": " --guest"}[kind]
        return what, f"./install.sh --admin-access revoke --user {grant['user']}{option}"
    rows = []
    for hit in response.get("hits", {}).get("hits", []):
        grant = hit["_source"]
        what, way_back = change(grant)
        rows.append(entry(grant["granted_at"], f"{what} for {grant['user']}: {grant.get('reason', '')}",
                          grant.get("requested_by", ""), grant.get("approved_by", ""), "", grant.get("state", ""), way_back, "grants"))
    return rows


def approval_changes(since):
    try:
        found = run("kubectl", "--request-timeout=30s", "-n", "infra", "get", "configmap", "swirl-cloud-prod-approvals",
                    "--ignore-not-found", "-o", "json")
    except RuntimeError as error:
        raise RuntimeError(f"production approvals: {error}") from None
    if not found.strip():
        return []
    decisions = json.loads(json.loads(found).get("data", {}).get("approvals.json") or "[]")
    rows = []
    for decision in decisions:
        if when(decision["at"]) < since:
            continue
        approver = ", ".join(decision.get("approved_by") or [])
        if not approver and decision.get("waiver"):
            approver = f"waived by {decision['waiver']['accepted_by']} (review {decision['waiver']['review_by']})"
        rows.append(entry(decision["at"], f"Production deployment of {decision.get('application', '')} ({decision.get('merge_request') or 'no merge request'})",
                          decision.get("deployed_by", ""), approver, decision.get("commit", "")[:12], decision.get("pipeline", ""),
                          "deploy the previous release to prod (scripts/deploy-application.py --release-version)", "approvals"))
    return rows


def collect(since):
    rows, errors = [], []
    for name, source in (("Git", git_changes), ("Argo CD", argocd_changes), ("Installer runs", installer_changes),
                         ("Administrator grants", grant_changes), ("Production approvals", approval_changes)):
        try:
            rows += source(since)
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            errors.append(f"{name}: {error}")
    return sorted(rows, key=lambda row: row["at"]), errors


def render(rows, errors, since):
    cell = lambda text: str(text).replace("|", "/").replace("\n", " ")  # noqa: E731
    lines = [f"# Change records since {since:%Y-%m-%d}", "",
             f"{len(rows)} change(s); {sum(not row['approved_by'] for row in rows)} without a recorded approver (review them).", "",
             "| Time (UTC) | Change | Who | Approved by | Revision | Result | Way back |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {row['at'].replace('T', ' ')[:16]} | {cell(row['change'])} | {cell(row['who'])} | "
              f"{cell(row['approved_by']) or '**none recorded**'} | {row['revision']} | {cell(row['result'])} | `{cell(row['way_back'])}` |"
              for row in rows]
    if errors:
        lines += ["", "## Not collected", ""] + [f"- {error}" for error in errors]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", help="ISO date; default 30 days ago")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    since = when(args.since) if args.since else datetime.now(timezone.utc) - timedelta(days=30)
    rows, errors = collect(since)
    print(json.dumps({"since": since.isoformat(), "changes": rows, "errors": errors}, indent=2) if args.format == "json"
          else render(rows, errors, since), end="" if args.format == "markdown" else "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
