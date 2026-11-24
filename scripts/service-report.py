#!/usr/bin/env python3
"""Monthly service report: availability per endpoint against the objective.

  service-report.py --month 2026-10 [--objective 0.995] [--output FILE] [--sign-key KEY]

Reads the hourly availability of every probed endpoint that the platform-reports
snapshot keeps (platform-reports-YYYY.MM), leaves out the hours inside declared
maintenance windows (./install.sh --notice add --maintenance; a window for a
service covers the endpoints on that service's host, one for * covers all), and
reports each endpoint's availability for the month against the objective (99.5 %
by default, as on the Service Levels dashboard), with the hours measured, the
hours excluded and the hours below full availability. It lists the month's
incidents from the incident register (GitLab issues of type Incident or labelled
`incident`) with their severity, times and services as the Incident template
records them, and the support desk's first response times (Zammad tickets opened
in the month). A source it cannot read is named in the report instead. With
--sign-key it signs the report with ssh-keygen -Y sign (namespace
swirl-cloud-service-report).
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
from urllib.parse import urlparse

import yaml

NAMESPACE = "infra"
SIGNATURE_NAMESPACE = "swirl-cloud-service-report"
ROOT = Path(__file__).resolve().parents[1]

# The register: incidents (issue type or label) open at any time in the month.
INCIDENTS = r'''
require "json"
start_at, end_at = STDIN.read.split.map { |t| Time.iso8601(t) }
open_in = ->(scope) { scope.where("issues.created_at < ?", end_at).where("issues.closed_at IS NULL OR issues.closed_at >= ?", start_at) }
found = open_in.(Issue.joins(:labels).where(labels: { title: "incident" })).to_a
begin
  found += open_in.(Issue.with_issue_type(:incident)).to_a
rescue StandardError
end
puts "SWIRL_JSON" + found.uniq(&:id).sort_by(&:created_at).map { |i|
  { reference: i.to_reference(full: true), title: i.title, created_at: i.created_at&.iso8601, closed_at: i.closed_at&.iso8601,
    description: i.description.to_s[0, 6000] }
}.to_json
'''
# First response times of the tickets opened in the month, in minutes of wall-clock time.
SUPPORT = ("SELECT json_build_object('number', number, 'created_at', created_at, 'first_response_at', first_response_at, "
           "'close_at', close_at) FROM tickets WHERE created_at >= :'start'::timestamptz AND created_at < :'end'::timestamptz;")
FIELD = re.compile(r"^\s*-\s*\*\*(Severity|Started \(estimated\)|Resolved|Services affected):\*\*\s*(.*?)\s*$", re.M)


def month_bounds(text):
    start = datetime.strptime(text, "%Y-%m").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def elasticsearch(index, body):
    script = ('curl -sS -u "elastic:$ELASTIC_PASSWORD" -H "Content-Type: application/json" --data-binary @- '
              f'"localhost:9200/{index}/_search?ignore_unavailable=true"')
    result = subprocess.run(["kubectl", "--request-timeout=60s", "-n", NAMESPACE, "exec", "-i", "deploy/elasticsearch", "--", "sh", "-c", script],
                            input=json.dumps(body), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "Elasticsearch query failed")
    response = json.loads(result.stdout)
    if "error" in response:
        raise SystemExit(str(response["error"])[:300])
    return response


def hourly(start, end):
    """{endpoint: {hour: ratio}} from the hourly platform reports."""
    body = {"size": 0, "query": {"bool": {"filter": [{"term": {"kind": "availability"}},
                                                     {"range": {"@timestamp": {"gte": start.isoformat(), "lt": end.isoformat()}}}]}},
            "aggs": {"endpoints": {"terms": {"field": "endpoint", "size": 500},
                                   "aggs": {"hours": {"date_histogram": {"field": "@timestamp", "fixed_interval": "1h"},
                                                      "aggs": {"ratio": {"avg": {"field": "ratio"}}}}}}}}
    response = elasticsearch(f"platform-reports-{start:%Y.%m}", body)
    found = {}
    for bucket in response.get("aggregations", {}).get("endpoints", {}).get("buckets", []):
        found[bucket["key"]] = {datetime.fromtimestamp(hour["key"] / 1000, timezone.utc): hour["ratio"]["value"]
                                for hour in bucket["hours"]["buckets"] if hour["ratio"]["value"] is not None}
    return found


def run(*command, stdin=None, timeout=300):
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr.strip() or "command failed").splitlines()[-1][:300])
    return result.stdout


def incidents(start, end):
    output = run("kubectl", "--request-timeout=60s", "-n", NAMESPACE, "exec", "-i", "deploy/gitlab", "--", "gitlab-rails", "runner",
                 INCIDENTS, stdin=f"{start.isoformat()} {end.isoformat()}\n")
    line = next((line for line in output.splitlines() if line.startswith("SWIRL_JSON")), None)
    if line is None:
        raise RuntimeError("GitLab returned no answer")
    return json.loads(line.removeprefix("SWIRL_JSON"))


def when(text):
    """A time the Incident template records (YYYY-MM-DD HH:MM, UTC), or None."""
    match = re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", text or "")
    return datetime.fromisoformat(match[0].replace(" ", "T")).replace(tzinfo=timezone.utc) if match else None


def incident_rows(found):
    rows = []
    for incident in found:
        fields = {name: value for name, value in FIELD.findall(incident.get("description") or "")}
        started = when(fields.get("Started (estimated)")) or datetime.fromisoformat(incident["created_at"])
        resolved = when(fields.get("Resolved")) or (datetime.fromisoformat(incident["closed_at"]) if incident.get("closed_at") else None)
        severity = re.match(r"SEV[1-3]\b", fields.get("Severity", ""))
        rows.append({"reference": incident["reference"], "title": incident["title"], "severity": severity[0] if severity else "not set",
                     "started": started, "resolved": resolved, "services": fields.get("Services affected") or "not recorded",
                     "minutes": round((resolved - started).total_seconds() / 60) if resolved else None})
    return rows


def support_tickets(start, end):
    target = run("bash", "-c", 'source "$1"; postgres_runtime_target infra', "report", str(ROOT / "scripts/lib/postgres-access.sh")).strip()
    script = ('IFS= read -r start; IFS= read -r end; '
              'exec psql -X -qAt --set=ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d zammad --set=start="$start" --set=end="$end"')
    out = run("kubectl", "-n", NAMESPACE, "exec", "-i", target, "-c", "postgres", "--", "sh", "-c", script,
              stdin=f"{start.isoformat()}\n{end.isoformat()}\n{SUPPORT}\n")
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def support_summary(tickets, now):
    """Counts and first response times (minutes) of the month's tickets."""
    def moment(text):
        value = datetime.fromisoformat(text)
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    answered = [(moment(t["first_response_at"]) - moment(t["created_at"])).total_seconds() / 60
                for t in tickets if t.get("first_response_at")]
    waiting = [t for t in tickets if not t.get("first_response_at")]
    return {"opened": len(tickets), "answered": len(answered), "closed": sum(1 for t in tickets if t.get("close_at")),
            "waiting": len(waiting), "median": statistics.median(answered) if answered else None,
            "p90": sorted(answered)[max(0, -(-len(answered) * 9 // 10) - 1)] if answered else None,
            "longest_waiting": max(((now - moment(t["created_at"])).total_seconds() / 60 for t in waiting), default=None)}


def duration(minutes):
    if minutes is None:
        return "ongoing"
    hours, rest = divmod(int(round(minutes)), 60)
    return f"{hours} h {rest:02d} min" if hours else f"{rest} min"


def attempt(function, *args):
    """(result, error text): a source that cannot be read is named, not fatal."""
    try:
        return function(*args), None
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        return None, str(error)


def configmap(name, key):
    result = subprocess.run(["kubectl", "--request-timeout=30s", "-n", NAMESPACE, "get", "configmap", name, "-o", "json"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        return None
    return json.loads(result.stdout).get("data", {}).get(key)


def service_hosts():
    """{service name: host} from the portal's service list."""
    text = configmap("homepage-config", "services.yaml") or "[]"
    hosts = {}
    for group in yaml.safe_load(text) or []:
        for entries in group.values():
            for entry in entries or []:
                for name, settings in entry.items():
                    if isinstance(settings, dict) and settings.get("href"):
                        hosts[name] = urlparse(settings["href"]).hostname
    return hosts


def excluded(endpoint, hour, windows, hosts):
    """Whether the hour overlaps a maintenance window covering the endpoint."""
    host = urlparse(endpoint).hostname
    for window in windows:
        if window["service"] != "*" and hosts.get(window["service"]) != host:
            continue
        if datetime.fromisoformat(window["starts"]) < hour + timedelta(hours=1) and datetime.fromisoformat(window["ends"]) > hour:
            return True
    return False


def summarise(samples, windows, hosts, objective):
    rows = []
    for endpoint, hours in sorted(samples.items()):
        kept = {hour: ratio for hour, ratio in hours.items() if not excluded(endpoint, hour, windows, hosts)}
        availability = sum(kept.values()) / len(kept) if kept else None
        rows.append({"endpoint": endpoint, "availability": availability, "hours": len(kept), "excluded": len(hours) - len(kept),
                     "degraded_hours": sum(1 for ratio in kept.values() if ratio < 1), "met": availability is None or availability >= objective})
    return rows


def render(month, rows, windows, objective, now, incident_list=None, support=None):
    lines = [f"# Service report, {month}", "", f"Generated {now:%Y-%m-%d %H:%M} UTC. Objective: {objective:.1%} monthly availability per endpoint,"
             " measured hourly by the platform's probes over the public path; hours inside declared maintenance windows are left out.", "",
             f"{sum(r['met'] for r in rows)} of {len(rows)} endpoints met the objective.", "",
             "| Endpoint | Availability | Objective met | Hours measured | Hours excluded (maintenance) | Hours below 100 % |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        value = f"{r['availability']:.3%}" if r["availability"] is not None else "no data"
        lines.append(f"| {r['endpoint']} | {value} | {'yes' if r['met'] else '**no**'} | {r['hours']} | {r['excluded']} | {r['degraded_hours']} |")
    lines += ["", "## Declared maintenance windows", ""]
    lines += [f"- {w['starts'][:16].replace('T', ' ')} to {w['ends'][:16].replace('T', ' ')} UTC, {w['service']}: {w['en']}" for w in windows] or ["None."]
    lines += ["", "## Incidents", ""]
    found, error = incident_list if incident_list is not None else (None, "not read")
    if error:
        lines.append(f"Not read from the incident register: {error}.")
    elif not found:
        lines.append("None recorded in the incident register.")
    else:
        lines += ["| Incident | Severity | Started | Resolved | Duration | Services affected |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['reference']} {r['title']} | {r['severity']} | {r['started']:%Y-%m-%d %H:%M} | "
                  f"{r['resolved'].strftime('%Y-%m-%d %H:%M') if r['resolved'] else 'not resolved'} | {duration(r['minutes'])} | {r['services']} |"
                  for r in found]
    lines += ["", "## Support response times", ""]
    summary, error = support if support is not None else (None, "not read")
    if error:
        lines.append(f"Not read from the support desk: {error}.")
    elif not summary["opened"]:
        lines.append("No ticket opened this month.")
    else:
        lines.append(f"{summary['opened']} tickets opened, {summary['answered']} answered, {summary['closed']} closed. First response"
                     f" (wall-clock time): median {duration(summary['median'])}, 90th percentile {duration(summary['p90'])}.")
        if summary["waiting"]:
            lines.append(f"{summary['waiting']} without a first response yet, the oldest waiting {duration(summary['longest_waiting'])}.")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--objective", type=float, default=0.995)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sign-key", type=Path)
    args = parser.parse_args()
    start, end = month_bounds(args.month)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if start > now:
        parser.error("--month is in the future")
    windows = [w for w in json.loads(configmap("swirl-cloud-maintenance-windows", "windows.json") or "[]")
               if datetime.fromisoformat(w["starts"]) < end and datetime.fromisoformat(w["ends"]) > start]
    rows = summarise(hourly(start, end), windows, service_hosts(), args.objective)
    found, error = attempt(incidents, start, end)
    incident_list = (incident_rows(found), None) if found is not None else (None, error)
    tickets, error = attempt(support_tickets, start, end)
    support = (support_summary(tickets, now), None) if tickets is not None else (None, error)
    text = render(args.month, rows, windows, args.objective, now, incident_list, support)
    if not args.output:
        print(text, end="")
        return 0 if all(r["met"] for r in rows) else 1
    args.output.write_text(text)
    if args.sign_key:
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(args.sign_key), "-n", SIGNATURE_NAMESPACE, str(args.output)], check=True)
        print(f"Wrote {args.output} and {args.output}.sig")
    else:
        print(f"Wrote {args.output}")
    return 0 if all(r["met"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
