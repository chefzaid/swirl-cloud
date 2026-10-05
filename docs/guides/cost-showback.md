# Cost Showback

Swirl Cloud runs on servers with a fixed monthly cost. Cost showback divides
that cost among the environments, namespaces and workloads that use the
servers, so each team or customer project sees what it costs and where capacity
sits idle. Prometheus computes it from data the platform already collects; no
billing service or external account is involved.

## Record server costs

Run on each server, with its monthly rent, or its purchase price spread over its
service life plus hosting and power:

```bash
scripts/configure-node-cost.sh --monthly-cost 89.90 --currency EUR
```

Record costs that belong to the whole platform, such as off-site backup
storage, domains or licences, on one server only:

```bash
scripts/configure-node-cost.sh --monthly-cost 89.90 --currency EUR \
  --shared backup-storage=12.50 --shared domains=3
```

Each run replaces that server's figures, so repeat every `--shared` entry when
you change one. `--show` prints the recorded values and `--remove` deletes
them. The installer records the control plane's cost from the optional
`SERVER_MONTHLY_COST` and `COST_CURRENCY` inputs, and the setup hub offers
**Maintenance → Record this server's monthly cost**. The script writes
`/var/lib/node_exporter/textfile_collector/swirl_cloud_cost.prom`, which
node-exporter publishes as `swirl_cloud_node_monthly_cost` and
`swirl_cloud_shared_monthly_cost`.

## How cost is allocated

1. Each server's monthly cost is split into 65 % CPU and 35 % memory, a common
   split for general-purpose servers. A core and a GiB of memory on that server
   then have a monthly price.
2. Every running pod is charged for the larger of its requests and its current
   usage of CPU and memory on the server it runs on. Requests reserve capacity
   whether it is used or not; usage above requests is charged too.
3. When bursting pods use more than the server's allocatable capacity, prices
   are based on the total allocation instead, so charges never exceed the
   server's cost.
4. Whatever no pod requests or uses is **idle** cost on that server.
5. Shared costs are shown separately and are not spread over workloads.

Storage is part of the server price, so persistent volumes are shown by size
but not charged separately. Costs in different currencies are reported side by
side, never converted.

| Series | Meaning |
| --- | --- |
| `swirl_cloud_cost_pod_cpu_cores`, `swirl_cloud_cost_pod_memory_bytes` | Allocated CPU and memory per pod (works without recorded costs) |
| `swirl_cloud_cost_cpu_core_monthly`, `swirl_cloud_cost_memory_gib_monthly` | Unit prices per server |
| `swirl_cloud_cost_pod_monthly` | Monthly cost per pod |
| `swirl_cloud_cost_workload_monthly{namespace,workload}` | Per workload: the pod's `app` label, otherwise its name without generated suffixes |
| `swirl_cloud_cost_namespace_monthly` | Per namespace |
| `swirl_cloud_cost_idle_monthly{node}` | Unallocated server cost |
| `swirl_cloud_cost_shared_monthly{item}`, `swirl_cloud_cost_total_monthly` | Shared costs and the platform total |

## Dashboard

Grafana's **Cost Showback** dashboard (Applications folder) shows the monthly
total, the allocated, idle and shared parts, cost per environment (namespaces
labelled `swirl-cloud.io/application-environment`, everything else is shared
platform), per namespace and per workload, and how allocation and idle cost
change over time. Two panels work without recorded costs: each namespace's
share of cluster CPU, and a rightsizing table comparing requested with used CPU
and memory. Large gaps between requested and used are candidates for smaller
requests, which lowers both the namespace's charge and the capacity a new
server must provide.

## Related Guides

- [Operations](../operations.md)
- [Compliance evidence](compliance-evidence.md)
- [Features](../features.md)
