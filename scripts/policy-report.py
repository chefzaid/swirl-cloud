#!/usr/bin/env python3
"""Report admission policy violations of existing application workloads.

Admission policies only see new requests. This command submits every
Deployment, StatefulSet, DaemonSet and CronJob in application namespaces
(labelled swirl-cloud.io/application-workloads=true) as an unchanged
server-side dry-run update and collects the policy warnings, so existing
workloads are checked by the same rules (ADR 0027). Nothing is changed.
"""
import argparse
from collections import Counter
import json
import re
import subprocess
import sys

KINDS = "deployments.apps,statefulsets.apps,daemonsets.apps,cronjobs.batch"
WARNING = re.compile(r"^Warning: Validation failed for ValidatingAdmissionPolicy '([^']+)' with binding '[^']+': (.*)$")


def kubectl(*args, stdin=None):
    return subprocess.run(["kubectl", "--request-timeout=60s", *args], input=stdin, capture_output=True, text=True, timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("--namespace", action="append", help="limit to a namespace (repeatable)")
    args = parser.parse_args()
    namespaces = args.namespace or kubectl("get", "namespaces", "-l", "swirl-cloud.io/application-workloads=true",
                                           "-o", "jsonpath={.items[*].metadata.name}").stdout.split()
    results = []
    for namespace in namespaces:
        listing = kubectl("get", KINDS, "-n", namespace, "-o", "json")
        if listing.returncode:
            raise SystemExit(listing.stderr.strip())
        for item in json.loads(listing.stdout)["items"]:
            for field in ("status",):
                item.pop(field, None)
            item["metadata"].pop("managedFields", None)
            result = kubectl("replace", "--dry-run=server", "-f", "-", stdin=json.dumps(item))
            violations = [m.group(2) for line in result.stderr.splitlines() if (m := WARNING.match(line.strip()))]
            results.append({"namespace": namespace, "kind": item["kind"], "name": item["metadata"]["name"],
                            "violations": violations,
                            "error": "" if result.returncode == 0 else result.stderr.strip().splitlines()[-1]})
    rules = Counter(v.rsplit(": ", 1)[0] for r in results for v in r["violations"])
    failing = [r for r in results if r["violations"] or r["error"]]
    if args.format == "json":
        print(json.dumps({"workloads": len(results), "violating": len(failing), "rules": rules, "results": results}, indent=2))
        return 0
    header = f"{len(results)} application workloads in {len(namespaces)} namespaces; {len(failing)} violate a policy."
    if args.format == "markdown":
        print(f"# Application policy report\n\n{header}\n\n| Rule | Workloads |\n|---|---|")
        print("\n".join(f"| {rule} | {count} |" for rule, count in rules.most_common()))
        print("\n| Namespace | Workload | Violations |\n|---|---|---|")
        for r in failing:
            print(f"| {r['namespace']} | {r['kind']} {r['name']} | {'; '.join(r['violations']) or r['error']} |")
    else:
        print(header)
        for rule, count in rules.most_common():
            print(f"  {count:3}  {rule}")
        for r in failing:
            print(f"\n{r['namespace']}/{r['kind']}/{r['name']}")
            for violation in r["violations"] or [r["error"]]:
                print(f"  - {violation}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
