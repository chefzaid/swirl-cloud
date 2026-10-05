#!/usr/bin/env python3
"""Game day: a recorded pod-loss drill in a non-production environment.

  game-day.py [--namespace apps-int] [--timeout 300] [--record]

For each Deployment in the namespace, deletes one ready pod and measures how
long the Deployment takes to be fully available again, while probing the
environment's public endpoints every few seconds. Refuses namespaces whose
swirl-cloud.io/application-environment label is prod (or missing, unless
--force). With --record, the results are stored in the platform-reports
index (kind: game_day) so the drill history appears in the reports.
"""
import argparse
from datetime import datetime, timezone
import json
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def kubectl_json(*args):
    result = subprocess.run(["kubectl", "--request-timeout=30s", *args, "-o", "json"], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise SystemExit(result.stderr.strip())
    return json.loads(result.stdout)


def probe(url):
    try:
        with build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": "swirl-cloud-game-day"}), timeout=5) as response:
            return response.status < 500
    except HTTPError as error:
        return error.code < 500
    except (URLError, TimeoutError, OSError):
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", default="apps-int")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for each recovery")
    parser.add_argument("--record", action="store_true", help="store the results in the platform reports")
    parser.add_argument("--force", action="store_true", help="allow a namespace without an environment label")
    args = parser.parse_args()
    labels = kubectl_json("get", "namespace", args.namespace)["metadata"].get("labels", {})
    environment = labels.get("swirl-cloud.io/application-environment")
    if environment == "prod" or (environment is None and not args.force):
        raise SystemExit(f"Refusing {args.namespace}: environment is {environment or 'unlabelled'}; game days run outside prod.")
    hosts = sorted({rule["host"] for ingress in kubectl_json("get", "ingress", "-n", args.namespace)["items"]
                    for rule in ingress["spec"].get("rules", []) if rule.get("host")})
    started = datetime.now(timezone.utc).replace(microsecond=0)
    samples, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            samples.extend(probe(f"https://{host}/") for host in hosts)
            stop.wait(3)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    results = []
    for deployment in kubectl_json("get", "deployments", "-n", args.namespace)["items"]:
        name = deployment["metadata"]["name"]
        selector = ",".join(f"{k}={v}" for k, v in deployment["spec"]["selector"].get("matchLabels", {}).items())
        pods = [p for p in kubectl_json("get", "pods", "-n", args.namespace, "-l", selector)["items"]
                if p["status"].get("phase") == "Running"]
        if not pods:
            results.append({"deployment": name, "outcome": "skipped: no running pod"})
            continue
        victim = pods[0]["metadata"]["name"]
        print(f"Deleting {args.namespace}/{victim} ...", flush=True)
        begin = time.monotonic()
        subprocess.run(["kubectl", "-n", args.namespace, "delete", "pod", victim, "--wait=false"], capture_output=True, check=True)
        time.sleep(2)
        wait = subprocess.run(["kubectl", "-n", args.namespace, "rollout", "status", f"deployment/{name}",
                               f"--timeout={args.timeout}s"], capture_output=True, text=True)
        seconds = round(time.monotonic() - begin, 1)
        outcome = "recovered" if wait.returncode == 0 else "not recovered"
        results.append({"deployment": name, "pod": victim, "recovery_seconds": seconds, "outcome": outcome})
        print(f"  {name}: {outcome} in {seconds} s", flush=True)
    time.sleep(6)
    stop.set()
    watcher.join()
    availability = sum(samples) / len(samples) if samples else None
    summary = {"@timestamp": started.isoformat(), "kind": "game_day", "namespace": args.namespace, "scenario": "pod-loss",
               "endpoints": hosts, "endpoint_availability": availability, "results": results,
               "passed": all(r["outcome"] != "not recovered" for r in results)}
    print(f"\nEndpoint availability during the drill: "
          f"{'n/a' if availability is None else f'{availability:.1%}'} over {len(samples)} probes of {', '.join(hosts) or 'no endpoint'}")
    print("Drill passed." if summary["passed"] else "Drill FAILED: a deployment did not recover in time.")
    if args.record:
        document = {**summary, "ratio": availability, "scope": "pod-loss",
                    "value": max((r.get("recovery_seconds", 0) for r in results), default=0)}
        index = f"platform-reports-{started:%Y.%m}"
        script = ('curl -sS -u "elastic:$ELASTIC_PASSWORD" -X POST -H "Content-Type: application/json" '
                  f'--data-binary @- "localhost:9200/{index}/_doc"')
        stored = subprocess.run(["kubectl", "-n", "infra", "exec", "-i", "deploy/elasticsearch", "--", "sh", "-c", script],
                                input=json.dumps(document), capture_output=True, text=True)
        print("Recorded in the platform reports." if '"result":"created"' in stored.stdout else f"Not recorded: {stored.stdout[:200]}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
