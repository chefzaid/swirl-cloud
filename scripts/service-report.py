#!/usr/bin/env python3
"""Monthly service report: availability per endpoint against the objective.

  service-report.py --month 2026-10 [--objective 0.995] [--output FILE] [--sign-key KEY]

Reads the hourly availability of every probed endpoint that the platform-reports
snapshot keeps (platform-reports-YYYY.MM), leaves out the hours inside declared
maintenance windows (./install.sh --notice add --maintenance; a window for a
service covers the endpoints on that service's host, one for * covers all), and
reports each endpoint's availability for the month against the objective (99.5 %
by default, as on the Service Levels dashboard), with the hours measured, the
hours excluded and the hours below full availability. With --sign-key it signs
the report with ssh-keygen -Y sign (namespace swirl-cloud-service-report).
Incidents and support response times come from the incident register and the
support desk (TODO §39) and are left to fill in.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

import yaml

NAMESPACE = "infra"
SIGNATURE_NAMESPACE = "swirl-cloud-service-report"


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


def render(month, rows, windows, objective, now):
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
    lines += ["", "## Incidents", "", "[From the incident register: each incident of the month with its duration and impact.]",
              "", "## Support response times", "", "[From the support desk, once it exists (TODO §39).]", ""]
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
    text = render(args.month, rows, windows, args.objective, now)
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
