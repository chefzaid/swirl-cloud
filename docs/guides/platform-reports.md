# Platform Reports

Prometheus keeps only recent history, but service levels, cost and compliance
are reported per month. Every hour the `platform-reports-snapshot` CronJob in
`infra` copies the past hour's figures from Prometheus into Elasticsearch, where
they are kept for 13 months. Grafana's **Platform Report** dashboard turns them
into a monthly report.

## What each snapshot records

| `kind` | Fields | Source |
| --- | --- | --- |
| `availability` | `endpoint`, `namespace`, `ratio` (share of successful probes in the hour) | [service levels](service-levels.md) |
| `cost` | `scope` (`namespace`, `workload`, `idle`, `shared`, `total`), `namespace`, `workload`, `currency`, `monthly_cost` | [cost showback](cost-showback.md) |
| `usage` | `namespace`, `cpu_cores`, `memory_bytes` (larger of requests and usage) | cost showback allocation |
| `compliance` | `control`, `status`, `passing` (1 or 0) | [compliance evidence](compliance-evidence.md) |
| `compliance_framework` | `framework`, `ratio` (share of automated controls passing) | compliance evidence |
| `vulnerabilities` | `severity`, `open`, `past_deadline`, `deadline_days` | Trivy findings in running images |
| `game_day` | `namespace`, `scope` (scenario), `ratio` (endpoint availability during the drill), `value` (slowest recovery, seconds) | [game days](service-levels.md#game-days) |
| `sli` | `indicator` (`datastore:<service>`, `ci_queue_p90_seconds`, `ci_platform_failure_ratio`, `argocd_sync_failure_ratio`), `value` | [service indicators](service-levels.md#other-indicators) |

The vulnerability counts rest on `platform-reports-vulnerability-tracking`, which
keeps, without lifecycle deletion, when each image and vulnerability pair
(Critical and High) was first and last seen. Deadlines default to 15 days for
Critical and 30 days for High; change them with `VULNERABILITY_DEADLINE_DAYS`
(for example `Critical=7,High=30`) on the CronJob. `./install.sh
--vulnerability-report` lists the items past their deadline (`--all` lists every
open item).

Documents are stamped with the start of the hour they cover, in monthly indices
`platform-reports-YYYY.MM`. Their IDs derive from the hour, so rerunning a
snapshot replaces that hour instead of duplicating it. The
`swirl-cloud-platform-reports` lifecycle policy deletes each index 400 days
after it starts. Snapshots use the Logstash writer account; Grafana reads them
through the **Platform reports** data source.

## Monthly report

Open Grafana **Platform Report** and choose the month in the time picker (for
example *Previous month*). It shows:

- availability across all endpoints and per endpoint against the 99.5 % objective;
- the average monthly platform cost, idle capacity cost, and cost per namespace and workload;
- the share of hours each compliance control passed;
- open Critical and High vulnerabilities and those past their deadline;
- availability and CPU allocation over time.

Export a table with **Inspect → Data → Download CSV** to send it on, and attach
the month's compliance evidence pack for audits.

## Check the snapshots

```bash
kubectl -n infra get cronjob platform-reports-snapshot
kubectl -n infra create job --from=cronjob/platform-reports-snapshot platform-reports-manual
kubectl -n infra logs job/platform-reports-manual
```

A run logs how many documents it indexed. A failed run raises
[KubernetesJobFailed](alert-runbooks.md#kubernetesjobfailed); the next hourly
run continues normally, and a missed hour stays a gap in the report.

## Related Guides

- [Service levels](service-levels.md)
- [Cost showback](cost-showback.md)
- [Compliance evidence](compliance-evidence.md)
