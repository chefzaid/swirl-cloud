# Service Levels

Every public endpoint of the platform and its applications has an availability
objective of **99.5 %** per 30 days, an error budget of 0.5 % (about 3.6 hours
a month). The platform measures it continuously and alerts when the budget is
being used up too fast. The same figures support the service levels promised
to users or customers.

## How availability is measured

The blackbox exporter in `infra` probes `https://<host>/` for every host in
every Ingress every 30 seconds. Prometheus discovers the hosts, so a new
application endpoint is measured as soon as its Ingress exists. Wildcard hosts
are skipped.

Probes leave the cluster and come back through public DNS, Cloudflare and
Traefik, the path users take. A probe succeeds when the endpoint answers over
valid TLS with 2xx, a redirect, 401, 403 or 404: login redirects and
Access-protected hosts count as available. It fails on 5xx, Cloudflare origin
errors, TLS errors, timeouts and DNS failures.

In-cluster probes cannot report an outage of the whole cluster or of its
network, because Prometheus and Alertmanager fail with it. A check from outside
the platform is planned in [TODO.md](../../TODO.md) (independent outage
detection).

## Alerts

The [multi-window burn-rate](https://sre.google/workbook/alerting-on-slos/)
method pages for real outages and ignores single failed probes:

| Alert | Fires when | Budget left at this rate |
| --- | --- | --- |
| `SLOAvailabilityBudgetBurnFast` (critical) | over 7.2 % of probes failed in the last hour and the last 5 minutes | about 2 days |
| `SLOAvailabilityBudgetBurnSlow` (warning) | over 3 % failed in the last 6 hours and the last 30 minutes | about 5 days |

Both carry `owner: application` for endpoints in application namespaces and
`owner: platform` otherwise, and link to their [runbook](alert-runbooks.md#sloavailabilitybudgetburnfast).

| Series | Meaning |
| --- | --- |
| `probe_success{job="availability-probes",instance,namespace}` | 1 when the last probe succeeded |
| `swirl_cloud_slo:probe_success:ratio_rate5m` (also `30m`, `1h`, `6h`) | Share of successful probes per endpoint |
| `swirl_cloud_slo:objective` | The availability objective (0.995) |

## Dashboard and reports

Grafana's **Service Levels** dashboard (Applications folder) lists every
endpoint with its current state, availability and remaining error budget over
the selected range, probe time, HTTP status and certificate lifetime, plus
failing endpoints over time. Prometheus keeps only recent history; monthly
availability per endpoint comes from the hourly snapshots in the
[platform reports](platform-reports.md).

The `service-availability` control of the
[compliance evidence](compliance-evidence.md) fails while a budget-burn alert
fires.

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Operations](../operations.md)
- [Compliance evidence](compliance-evidence.md)
