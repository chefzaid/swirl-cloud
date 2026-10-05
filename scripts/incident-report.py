#!/usr/bin/env python3
"""Evidence, timeline and NIS2/GDPR report drafts for an incident window.

  incident-report.py --start 2026-10-05T01:00Z [--end 2026-10-05T03:00Z] [--aware 2026-10-05T01:20Z]

Collects, read-only, for the window: alerts that fired (with first and last
time), endpoint availability, pod restarts and Argo CD syncs from Prometheus;
endpoint availability per hour from the platform reports when the window is
older than Prometheus keeps; failed logins and Keycloak admin changes from the
identity events; and Kubernetes writes by people, RBAC changes and Secret reads
from the API audit log; and security detection findings. It prints a Markdown evidence report with a merged
timeline and drafts of the NIS2 early warning, incident notification and final
report, with due times counted from --aware (when the organization became
aware). Paste it into the GitLab incident (template "Incident").
Elasticsearch is queried inside its pod; no Secret leaves the cluster.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from urllib.parse import quote, urlencode

PROXY = "/api/v1/namespaces/infra/services/prometheus:9090/proxy"
SYSTEM_WRITERS = ("system:serviceaccount:", "system:node:", "system:k3s-supervisor", "system:kube-",
                  "system:apiserver")
ALERT_KEYS = ("namespace", "deployment", "statefulset", "pod", "instance", "service", "control", "node", "resource",
              "persistentvolumeclaim", "name", "user", "username", "administrator")


def parse_time(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def fmt(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def prometheus(path, **params):
    result = subprocess.run(["kubectl", "--request-timeout=60s", "get", "--raw",
                             f"{PROXY}{path}?{urlencode(params, quote_via=quote)}"],
                            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Prometheus query failed")
    body = json.loads(result.stdout)
    if body.get("status") != "success":
        raise RuntimeError(body.get("error", "Prometheus query failed"))
    return body["data"]["result"]


def elasticsearch(index, body):
    script = ('curl -sS -u "elastic:$ELASTIC_PASSWORD" -H "Content-Type: application/json" --data-binary @- '
              f'"localhost:9200/{index}/_search?ignore_unavailable=true&allow_no_indices=true"')
    result = subprocess.run(["kubectl", "--request-timeout=60s", "-n", "infra", "exec", "-i", "deploy/elasticsearch",
                             "--", "sh", "-c", script], input=json.dumps(body), capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Elasticsearch query failed")
    response = json.loads(result.stdout)
    if "error" in response:
        raise RuntimeError(str(response["error"].get("reason", response["error"]))[:200])
    return response


def window_filter(start, end):
    return {"range": {"@timestamp": {"gte": start.isoformat(), "lte": end.isoformat()}}}


def alerts(start, end, step):
    series = prometheus("/api/v1/query_range", query='ALERTS{alertstate="firing"}', start=f"{start.timestamp():.0f}",
                        end=f"{end.timestamp():.0f}", step=f"{step}s")
    found = []
    for item in series:
        labels = item["metric"]
        times = [float(t) for t, _ in item["values"]]
        if not times:
            continue
        context = ", ".join(f"{k}={labels[k]}" for k in ALERT_KEYS if labels.get(k))
        found.append({"alert": labels.get("alertname"), "severity": labels.get("severity", ""), "owner": labels.get("owner", ""),
                      "context": context, "first": datetime.fromtimestamp(min(times), timezone.utc),
                      "last": datetime.fromtimestamp(max(times), timezone.utc)})
    return sorted(found, key=lambda a: a["first"])


def availability_prometheus(start, end):
    seconds = max(int((end - start).total_seconds()), 60)
    rows = prometheus("/api/v1/query", query=f'avg by (instance) (avg_over_time(probe_success{{job="availability-probes"}}[{seconds}s]))',
                      time=f"{end.timestamp():.0f}")
    return {r["metric"]["instance"]: float(r["value"][1]) for r in rows}


def availability_reports(start, end):
    response = elasticsearch("platform-reports-*", {"size": 0, "query": {"bool": {"filter": [
        {"term": {"kind": "availability"}}, window_filter(start - timedelta(hours=1), end)]}},
        "aggs": {"endpoints": {"terms": {"field": "endpoint", "size": 500}, "aggs": {"ratio": {"avg": {"field": "ratio"}}}}}})
    return {b["key"]: b["ratio"]["value"] for b in response["aggregations"]["endpoints"]["buckets"]}


def restarts_and_syncs(start, end):
    seconds = max(int((end - start).total_seconds()), 60)
    restarts = prometheus("/api/v1/query", query=f"sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total[{seconds}s])) >= 1",
                          time=f"{end.timestamp():.0f}")
    syncs = prometheus("/api/v1/query", query=f'sum by (name, phase) (increase(argocd_app_sync_total[{seconds}s])) >= 0.5',
                       time=f"{end.timestamp():.0f}")
    return ([f"{r['metric']['namespace']}/{r['metric']['pod']}: {float(r['value'][1]):.0f} restart(s)" for r in restarts],
            [f"{r['metric']['name']}: {float(r['value'][1]):.0f} sync(s), {r['metric']['phase']}" for r in syncs])


def identity(start, end):
    failures = elasticsearch("identity-audit-*", {"size": 0, "query": {"bool": {"filter": [
        {"term": {"keycloak.type": "LOGIN_ERROR"}}, window_filter(start, end)]}},
        "aggs": {"users": {"terms": {"field": "keycloak.username", "size": 10}},
                 "addresses": {"terms": {"field": "keycloak.ipAddress", "size": 10}}}})
    logins = elasticsearch("identity-audit-*", {"size": 0, "query": {"bool": {"filter": [
        {"term": {"keycloak.type": "LOGIN"}}, window_filter(start, end)]}},
        "aggs": {"users": {"terms": {"field": "keycloak.username", "size": 20}}}})
    admin = elasticsearch("identity-audit-*", {"size": 0, "query": {"bool": {"filter": [
        {"term": {"event_category": "admin"}}, window_filter(start, end)],
        "must_not": [{"term": {"keycloak.operationType": "ACTION"}}]}},
        "aggs": {"changes": {"multi_terms": {"terms": [{"field": "keycloak.operationType"}, {"field": "keycloak.resourceType"}], "size": 20}}}})
    bucket = lambda response, name: [(b["key"], b["doc_count"]) for b in response["aggregations"][name]["buckets"]]
    return {"failed_logins": failures["hits"]["total"]["value"], "failed_users": bucket(failures, "users"),
            "failed_addresses": bucket(failures, "addresses"), "logins": bucket(logins, "users"),
            "admin_changes": [(" ".join(k), c) for k, c in bucket(admin, "changes")]}


def kubernetes_audit(start, end):
    writes = elasticsearch("kubernetes-audit-*", {"size": 0, "query": {"bool": {"filter": [
        {"terms": {"verb": ["create", "update", "patch", "delete"]}}, window_filter(start, end)],
        "must_not": [{"wildcard": {"requestURI": "*dryRun*"}}] + [{"prefix": {"user.username": p}} for p in SYSTEM_WRITERS]}},
        "aggs": {"users": {"terms": {"field": "user.username", "size": 10},
                           "aggs": {"objects": {"terms": {"field": "objectRef.resource", "size": 8}}}}}})
    secrets = elasticsearch("kubernetes-audit-*", {"size": 0, "query": {"bool": {"filter": [
        {"term": {"objectRef.resource": "secrets"}}, {"terms": {"verb": ["get", "list", "watch"]}}, window_filter(start, end)],
        "must_not": [{"prefix": {"user.username": p}} for p in SYSTEM_WRITERS]}},
        "aggs": {"users": {"terms": {"field": "user.username", "size": 10}}}})
    return {"writes": [(b["key"], b["doc_count"], ", ".join(f"{o['key']} {o['doc_count']}" for o in b["objects"]["buckets"]))
                       for b in writes["aggregations"]["users"]["buckets"]],
            "secret_reads": [(b["key"], b["doc_count"]) for b in secrets["aggregations"]["users"]["buckets"]]}


def findings(start, end):
    response = elasticsearch("security-findings-*", {"size": 200, "sort": [{"@timestamp": "asc"}],
                                                     "query": {"bool": {"filter": [window_filter(start, end)]}}})
    return [{"time": parse_time(hit["_source"]["@timestamp"]), "alert": hit["_source"].get("alertname"),
             "severity": hit["_source"].get("severity", ""), "description": hit["_source"].get("description", "")}
            for hit in response["hits"]["hits"]]


def collect(start, end):
    evidence, errors = {}, []
    retained = True
    try:
        oldest = prometheus("/api/v1/query", query="min_over_time(prometheus_tsdb_lowest_timestamp_seconds[1h])")
        retained = bool(oldest) and float(oldest[0]["value"][1]) <= start.timestamp()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        errors.append(f"Prometheus retention: {error}")
    evidence["prometheus_covers_window"] = retained
    step = max(60, int((end - start).total_seconds() // 600))
    sources = [("alerts", lambda: alerts(start, end, step)) if retained else None,
               ("availability", lambda: availability_prometheus(start, end) if retained else availability_reports(start, end)),
               ("restarts_syncs", lambda: restarts_and_syncs(start, end)) if retained else None,
               ("identity", lambda: identity(start, end)), ("kubernetes", lambda: kubernetes_audit(start, end)),
               ("findings", lambda: findings(start, end))]
    for source in filter(None, sources):
        name, function = source
        try:
            evidence[name] = function()
        except (RuntimeError, ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            errors.append(f"{name}: {error}")
    evidence["errors"] = errors
    return evidence


def render(title, start, end, aware, evidence):
    availability = evidence.get("availability", {})
    degraded = {endpoint: ratio for endpoint, ratio in availability.items() if ratio is not None and ratio < 0.999}
    fired = evidence.get("alerts", [])
    early, notification = aware + timedelta(hours=24), aware + timedelta(hours=72)
    final = notification + timedelta(days=30)
    lines = [f"# Incident evidence: {title}", "",
             f"Window {fmt(start)} to {fmt(end)}; aware since {fmt(aware)}. Generated {fmt(datetime.now(timezone.utc))}.", "",
             "## Reporting deadlines", "",
             "| Report | Due |", "|---|---|",
             f"| NIS2 early warning (24 h) | {fmt(early)} |",
             f"| NIS2 incident notification (72 h) | {fmt(notification)} |",
             f"| NIS2 final report (one month after the notification) | {fmt(final)} |",
             f"| GDPR personal data breach notification (72 h), if personal data is involved | {fmt(notification)} |", "",
             "## Affected endpoints", ""]
    if degraded:
        lines += ["| Endpoint | Availability in the window |", "|---|---|"]
        lines += [f"| {e} | {r:.2%} |" for e, r in sorted(degraded.items(), key=lambda item: item[1])]
    else:
        lines.append(f"All {len(availability)} probed endpoints stayed available." if availability else "No availability data.")
    lines += ["", "## Timeline", "", "| Time (UTC) | Event | Source |", "|---|---|---|"]
    events = []
    for alert in fired:
        events.append((alert["first"], f"{alert['alert']} ({alert['severity']}) firing: {alert['context'] or 'cluster'}", "Prometheus"))
        if alert["last"] < end - timedelta(minutes=2):
            events.append((alert["last"], f"{alert['alert']} last firing", "Prometheus"))
    for finding in evidence.get("findings", []):
        events.append((finding["time"], f"{finding['alert']} ({finding['severity']}): {finding['description']}", "Security detection"))
    lines += [f"| {fmt(t)} | {text} | {source} |" for t, text, source in sorted(events, key=lambda e: e[0])] or ["| | No alerts fired in the window. | |"]
    if not evidence.get("prometheus_covers_window"):
        lines += ["", "Prometheus no longer holds this window; alerts, restarts and syncs are missing and availability comes"
                      " from the hourly platform reports."]
    restarts, syncs = evidence.get("restarts_syncs", ([], []))
    lines += ["", "## Changes and restarts", "", "Argo CD syncs: " + ("; ".join(syncs) or "none") + ".",
              "", "Pod restarts: " + ("; ".join(restarts) or "none") + "."]
    kube = evidence.get("kubernetes", {})
    lines += ["", "Kubernetes writes by people (dry-runs excluded): " +
              ("; ".join(f"{u} {c} ({objects})" for u, c, objects in kube.get("writes", [])) or "none") + ".",
              "", "Secret reads by people: " + ("; ".join(f"{u} {c}" for u, c in kube.get("secret_reads", [])) or "none") + "."]
    ident = evidence.get("identity", {})
    lines += ["", "## Identity", "",
              f"Failed logins: {ident.get('failed_logins', 'unknown')}"
              + (" (" + ", ".join(f"{u} {c}" for u, c in ident.get("failed_users", [])) + ")" if ident.get("failed_users") else "")
              + (", from " + ", ".join(f"{a} {c}" for a, c in ident.get("failed_addresses", [])) if ident.get("failed_addresses") else "") + ".",
              "", "Successful logins: " + (", ".join(f"{u} {c}" for u, c in ident.get("logins", [])) or "none") + ".",
              "", "Keycloak admin changes: " + (", ".join(f"{k} {c}" for k, c in ident.get("admin_changes", [])) or "none") + "."]
    if evidence.get("errors"):
        lines += ["", "## Not collected", ""] + [f"- {error}" for error in evidence["errors"]]
    affected = ", ".join(sorted(degraded)) or "[services affected]"
    lines += ["", "## Draft: NIS2 early warning (within 24 hours)", "",
              f"On {fmt(aware)} we became aware of an incident affecting {affected}. "
              f"It started at about {fmt(start)}. [State whether it is suspected to be caused by unlawful or malicious acts"
              " and whether it could have a cross-border impact.]", "",
              "## Draft: NIS2 incident notification (within 72 hours)", "",
              f"Update of the early warning. Affected services: {affected}. Duration: {fmt(start)} to {fmt(end)}. "
              f"{len(fired)} monitoring alert(s) fired. [Initial assessment: severity and impact, indicators of compromise"
              " where available, measures taken.]", "",
              "## Draft: NIS2 final report (within one month of the notification)", "",
              "[Detailed description including severity and impact; type of threat or root cause; mitigation measures"
              " applied and ongoing; cross-border impact where applicable.] Attach this evidence report and the post-incident review."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True, help="incident start, ISO 8601 (UTC if no offset)")
    parser.add_argument("--end", help="incident end or now (default)")
    parser.add_argument("--aware", help="when the organization became aware; reporting clocks start here (default: start)")
    parser.add_argument("--title", default="incident")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    start = parse_time(args.start)
    end = parse_time(args.end) if args.end else datetime.now(timezone.utc).replace(microsecond=0)
    aware = parse_time(args.aware) if args.aware else start
    if end <= start:
        parser.error("--end must be after --start")
    evidence = collect(start, end)
    if args.format == "json":
        print(json.dumps(evidence, indent=2, default=lambda value: value.isoformat()))
    else:
        print(render(args.title, start, end, aware, evidence), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
