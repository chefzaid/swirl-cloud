# Platform Reports

Prometheus keeps only recent history, but service levels, cost and compliance
are reported per month. At seven minutes past every hour the
`platform-reports-snapshot` CronJob in `infra` copies the past hour's figures
from Prometheus into Elasticsearch, kept for 13 months. Grafana's **Platform
Report** dashboard turns them into a monthly report.

## What each snapshot records

| `kind` | Fields | Source |
| --- | --- | --- |
| `availability` | `endpoint`, `namespace`, `ratio` (share of successful probes in the hour) | [service levels](service-levels.md) |
| `cost` | `scope` (`namespace`, `workload`, `idle`, `shared`, `total`, and `aws_capacity` and `aws_allocated` for the [hyperscaler comparison](cost-showback.md#hyperscaler-comparison)), `namespace`, `workload`, `currency`, `monthly_cost` | [cost showback](cost-showback.md) |
| `usage` | `namespace`, `cpu_cores`, `memory_bytes` (larger of requests and usage) | cost showback allocation |
| `compliance` | `control`, `status`, `passing` (1 or 0) | [compliance evidence](compliance-evidence.md) |
| `compliance_framework` | `framework`, `ratio` (share of automated controls passing) | compliance evidence |
| `vulnerabilities` | `severity`, `open`, `past_deadline`, `deadline_days` | Trivy findings in running images, without unexpired [exceptions](../security.md#vulnerability-triage) |
| `game_day` | `namespace`, `scope` (scenario), `ratio` (endpoint availability during the drill), `value` (slowest recovery, seconds) | [game days](service-levels.md#game-days), written by `./install.sh --game-day --record`, not the hourly snapshot |
| `sli` | `indicator` (`datastore:<service>`, `ci_queue_p90_seconds`, `ci_platform_failure_ratio`, `argocd_sync_failure_ratio`), `value` | [service indicators](service-levels.md#other-indicators) |

Vulnerability counts rest on the `platform-reports-vulnerability-tracking`
index, which records when each image and Critical or High vulnerability was
first and last seen, with no lifecycle deletion. Deadlines default to 15 days
for Critical and 30 for High; set `VULNERABILITY_DEADLINE_DAYS` on the CronJob
to change them (for example `Critical=7,High=30`).
`./install.sh --vulnerability-report` lists items past their deadline (`--all`
lists every open item).

Snapshot documents are stamped with the start of the hour they cover, in monthly
indices `platform-reports-YYYY.MM`. Their IDs derive from the hour, so a rerun
replaces that hour instead of duplicating it. The `swirl-cloud-platform-reports`
lifecycle policy deletes each index 400 days after it starts. Snapshots write
with the Logstash account; Grafana reads through the **Platform reports** data
source.

## Monthly report

Open Grafana **Platform Report** and pick the month in the time picker (for
example *Previous month*). It shows:

- availability overall and per endpoint against the 99.5 % objective;
- average monthly platform cost, idle capacity cost, and cost per namespace and
  workload;
- the same capacity and the allocated capacity at AWS list prices;
- the share of hours each compliance control passed;
- open Critical and High vulnerabilities and those past their deadline;
- service indicators and recorded game days;
- availability and CPU allocation over time;
- each server's price age, red after six months (live, from Prometheus, not the
  snapshots).

Export a table with **Inspect → Data → Download CSV**. For audits, attach the
month's compliance evidence pack.

## Check the snapshots

```bash
kubectl -n infra get cronjob platform-reports-snapshot
kubectl -n infra create job --from=cronjob/platform-reports-snapshot platform-reports-manual
kubectl -n infra logs job/platform-reports-manual
```

A run logs how many documents it indexed. A failed run raises
[KubernetesJobFailed](alert-runbooks.md#kubernetesjobfailed). The next run
continues normally; a missed hour stays a gap in the report.

## Related Guides

- [Service levels](service-levels.md)
- [Cost showback](cost-showback.md)
- [Compliance evidence](compliance-evidence.md)
