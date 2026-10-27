# Service Levels

Every public endpoint of the platform and its applications has an availability
objective of **99.5 %** per 30 days: an error budget of 0.5 %, about 3.6 hours a
month. The platform measures it continuously and alerts when the budget burns too
fast; the same figures back the service levels promised to users or customers.

## How availability is measured

The blackbox exporter in `infra` probes `https://<host>/` every 30 seconds for every
Ingress host (wildcards skipped). Prometheus discovers the hosts, so a new endpoint
is measured as soon as its Ingress exists.

Probes go out through public DNS, Cloudflare and Traefik, the users' path. Success
is valid TLS with 2xx, a redirect, 401, 403 or 404, so login redirects and
Access-protected hosts count as available. Failure is 5xx, a Cloudflare origin
error, a TLS error, a timeout or a DNS failure.

In-cluster probes cannot report a whole-cluster or network outage, because
Prometheus and Alertmanager fail with it; the [outage watch](#outage-watch) covers
that.

## Outage watch

`scripts/configure-outage-watch.py` deploys a Cloudflare Worker that checks public
endpoints every five minutes from outside the cluster
([ADR 0030](../adr/0030-outage-watch-outside-the-cluster.md)). It emails the alert
recipients through the [platform mail](platform-mail.md) relay when an endpoint goes
down and when it recovers, so it still reports when the cluster, its node or GitLab
is down.

- Scope: every Ingress host of the platform, Odoo and production applications not
  gated by Cloudflare Access (Access answers at the edge). `int` and `uat` hosts are
  excluded.
- An answer below 500 is up. Two failed checks in a row (5xx, timeout or failed
  connection) mark an endpoint down. Each change sends one email that also lists
  what is still down.
- The Worker has no route, is kept off `workers.dev` and keeps state in a Durable
  Object, within Cloudflare's free plan. Durable Objects need a `workers.dev`
  subdomain on the account, so the configurator creates one named after the
  organization if missing. No history is kept; the probes remain the measurement.

The installer deploys it when Cloudflare and platform mail are both configured.
Otherwise, or after changing the relay or adding public hosts, run it on a control
plane with `CLOUDFLARE_API_TOKEN` exported (the token also needs Account **Workers
Scripts Edit**):

```bash
scripts/configure-outage-watch.py              # deploy, or update the hosts and relay
scripts/configure-outage-watch.py --report     # email every endpoint's status after the next check
scripts/configure-outage-watch.py --drill 20   # a simulated endpoint fails for 20 minutes
scripts/configure-outage-watch.py --remove     # delete the Worker and its state
```

A drill sends `[DOWN]` within ten minutes and `[UP]` after it ends; then rerun
without `--drill` to drop the simulated endpoint.

## Alerts

[Multi-window burn-rate](https://sre.google/workbook/alerting-on-slos/) alerts page
for real outages and ignore single failed probes:

| Alert | Fires when | Budget left at this rate |
| --- | --- | --- |
| `SLOAvailabilityBudgetBurnFast` (critical) | over 7.2 % of probes failed in the last hour and the last 5 minutes | about 2 days |
| `SLOAvailabilityBudgetBurnSlow` (warning) | over 3 % failed in the last 6 hours and the last 30 minutes | about 5 days |

Both carry `owner: application` for application namespaces and `owner: platform`
otherwise, and link to their [runbook](alert-runbooks.md#sloavailabilitybudgetburnfast).

| Series | Meaning |
| --- | --- |
| `probe_success{job="availability-probes",instance,namespace}` | 1 when the last probe succeeded |
| `swirl_cloud_slo:probe_success:ratio_rate5m` (also `30m`, `1h`, `6h`) | Share of successful probes per endpoint |
| `swirl_cloud_slo:objective` | The availability objective (0.995) |

## Other indicators

| Series | Meaning |
| --- | --- |
| `swirl_cloud_sli:datastore_up{service}` | Shared PostgreSQL, MongoDB, Redis and Kafka reachable by their exporters (1 or 0) |
| `swirl_cloud_sli:ci_queue_seconds:p90_6h` | Time nine in ten CI jobs waited for a runner over six hours |
| `swirl_cloud_sli:ci_platform_failures:ratio_rate6h` | CI jobs failed by the runner, the system or a timeout (script failures and cancellations excluded) |
| `swirl_cloud_sli:argocd_sync_failures:ratio_rate6h` | Argo CD sync operations that failed |
| `probe_success{job="registry-probes",instance}` | The registry API answers an anonymous `GET /v2/` with `401` and its version header, through the public edge and inside the cluster (1 or 0); a 403 challenge page counts as available for the generic probe but not here |

[`CIJobsQueueingLong`](alert-runbooks.md#cijobsqueueinglong) fires when CI queue
time stays above ten minutes for an hour; data stores alert through
[`DatastoreDown`](alert-runbooks.md#datastoredown), and the registry through
[`RegistryApiUnavailable`](alert-runbooks.md#registryapiunavailable).

## Game days

`./install.sh --game-day --namespace apps-int --record` runs a pod-loss drill: it
deletes one running pod of each Deployment, measures how long each takes to be
fully available again (`--timeout`, default 300 seconds) and probes the
environment's endpoints throughout. The namespace defaults to `apps-int`. It
refuses namespaces labelled `swirl-cloud.io/application-environment: prod`, and
unlabelled ones unless `--force`. `--record` stores the result in the
[platform reports](platform-reports.md) (**Game days** table). Run it monthly and
after changing replica counts, probes or start-up behaviour; a single-replica
service is down until its replacement is ready.

## Dashboard and reports

Grafana's **Service Levels** dashboard (Applications folder) shows each endpoint's
state, availability, remaining error budget, probe time, HTTP status and certificate
lifetime, plus failing endpoints over time. Prometheus keeps only recent history;
monthly availability comes from hourly snapshots in the
[platform reports](platform-reports.md). The `service-availability` control of the
[compliance evidence](compliance-evidence.md) fails while a budget-burn alert fires.

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Operations](../operations.md)
- [Compliance evidence](compliance-evidence.md)
