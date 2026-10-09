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
network, because Prometheus and Alertmanager fail with it; the
[outage watch](#outage-watch) checks from outside.

## Outage watch

`scripts/configure-outage-watch.py` deploys a Cloudflare Worker that checks the
public endpoints every five minutes from Cloudflare's network, outside the
cluster ([ADR 0030](../adr/0030-outage-watch-outside-the-cluster.md)). It emails
the alert recipients through the [platform mail](platform-mail.md) relay when an
endpoint goes down and when it answers again, so it still reports when the
cluster, its node or GitLab is down.

- It watches every Ingress host of the platform, Odoo and production
  applications that Cloudflare Access does not gate: Access answers at the edge
  without reaching the origin. Environment hosts (`int`, `uat`) are left out.
- An answer below 500 counts as up. A 5xx answer, a timeout or a failed
  connection is a failed check, and two in a row mark the endpoint down. Each
  change sends one email that also lists what is still down.
- The Worker has no route and is kept off `workers.dev`, and it keeps its state
  in a Durable Object; it fits Cloudflare's free plan. Cloudflare requires a
  `workers.dev` subdomain on the account for Durable Objects, so the
  configurator creates one named after the organization if there is none. The
  watch keeps no history: the probes above remain the measurement.

The installer deploys it when Cloudflare and platform mail are both configured.
Otherwise, or after changing the relay or adding public hosts, run it on a
control plane with `CLOUDFLARE_API_TOKEN` exported (the token also needs Account
**Workers Scripts Edit**):

```bash
scripts/configure-outage-watch.py              # deploy, or update the hosts and relay
scripts/configure-outage-watch.py --report     # email every endpoint's status after the next check
scripts/configure-outage-watch.py --drill 20   # a simulated endpoint fails for 20 minutes
scripts/configure-outage-watch.py --remove     # delete the Worker and its state
```

A drill sends a `[DOWN]` email within ten minutes and an `[UP]` email after the
drill; rerun without `--drill` afterwards to drop the simulated endpoint.

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

## Other indicators

Probes cover what users reach over HTTPS. These indicators cover the rest:

| Series | Meaning |
| --- | --- |
| `swirl_cloud_sli:datastore_up{service}` | Shared PostgreSQL, MongoDB, Redis and Kafka reachable by their exporters (1 or 0) |
| `swirl_cloud_sli:ci_queue_seconds:p90_6h` | Time nine in ten CI jobs waited for a runner over six hours |
| `swirl_cloud_sli:ci_platform_failures:ratio_rate6h` | CI jobs that failed because of the runner, the system or a timeout; job script failures and cancellations are excluded |
| `swirl_cloud_sli:argocd_sync_failures:ratio_rate6h` | Argo CD sync operations that failed |

[`CIJobsQueueingLong`](alert-runbooks.md#cijobsqueueinglong) fires when the CI
queue time stays above ten minutes for an hour; data stores already alert
through [`DatastoreDown`](alert-runbooks.md#datastoredown).

## Game days

`./install.sh --game-day --namespace apps-int --record` runs a pod-loss drill in
a non-production environment: it deletes one running pod of each Deployment,
measures how long each takes to be fully available again and probes the
environment's endpoints throughout. It refuses namespaces labelled
`swirl-cloud.io/application-environment: prod`. With `--record` the result is
kept in the [platform reports](platform-reports.md) (**Game days** table). Run it
monthly and after changing replica counts, probes or start-up behaviour; a
single-replica service is unavailable until its replacement is ready.

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
