# Cost Showback

Swirl Cloud runs on servers with a fixed monthly price. Cost showback divides
that price among the environments, namespaces and workloads that use them, and
shows where capacity sits idle. It is an internal allocation, not usage billing:
the total never exceeds the recorded price. Prometheus computes it from data the
platform already collects; no billing service is involved.

## Record server costs

On each server, record its monthly rent, or its purchase price spread over its
service life plus hosting and power:

```bash
scripts/configure-node-cost.sh --monthly-cost 89.90 --currency EUR
```

Record platform-wide costs (off-site backup storage, domains, licences) on one
server only:

```bash
scripts/configure-node-cost.sh --monthly-cost 89.90 --currency EUR \
  --shared backup-storage=12.50 --shared domains=3
```

- Each run replaces that server's figures, so repeat every `--shared` entry when
  you change one.
- `--show` prints the recorded values; `--remove` deletes them.
- `--price-date` (default today) and `--price-source` (for example
  `"Hetzner AX52 order"`) date the prices. The Platform Report shows each
  server's price age and turns it red after six months, or when no date was
  recorded: providers change their prices, so recheck the offer and record it
  again.
- The currency defaults to `EUR`.
- The installer records the control plane's cost from the optional
  `SERVER_MONTHLY_COST` and `COST_CURRENCY` inputs. The setup hub offers
  **Maintenance → Record this server's monthly cost**.

The script writes `/var/lib/node_exporter/textfile_collector/swirl_cloud_cost.prom`,
which node-exporter publishes as `swirl_cloud_node_monthly_cost` and
`swirl_cloud_shared_monthly_cost`.

## How cost is allocated

1. Each server's cost is split 65 % CPU and 35 % memory, giving a monthly price
   per core and per GiB on that server.
2. Every running pod is charged for the larger of its requests and its usage of
   CPU and memory.
3. When bursting pods use more than the server's allocatable capacity, prices are
   based on total allocation instead, so charges never exceed the server's cost.
4. Whatever no pod requests or uses is **idle** cost on that server.
5. Shared costs are shown separately, not spread over workloads.

Storage is part of the server price: persistent volumes are shown by size but
not charged. Currencies are reported side by side, never converted.

| Series | Meaning |
| --- | --- |
| `swirl_cloud_cost_pod_cpu_cores`, `swirl_cloud_cost_pod_memory_bytes` | Allocated CPU and memory per pod (works without recorded costs) |
| `swirl_cloud_cost_cpu_core_monthly`, `swirl_cloud_cost_memory_gib_monthly` | Unit prices per server |
| `swirl_cloud_cost_pod_monthly` | Monthly cost per pod |
| `swirl_cloud_cost_workload_monthly{namespace,workload}` | Per workload: the pod's `app` label, otherwise its name without generated suffixes |
| `swirl_cloud_cost_namespace_monthly` | Per namespace |
| `swirl_cloud_cost_idle_monthly{node}` | Unallocated server cost |
| `swirl_cloud_cost_shared_monthly{item}`, `swirl_cloud_cost_total_monthly` | Shared costs and the platform total |

## Hyperscaler comparison

Prometheus also prices the same resources at AWS on-demand list prices in Paris,
so the fixed server cost can be compared with renting it from a hyperscaler.
`swirl_cloud_hyperscaler_equivalent_monthly` has two bases:

- `capacity`: every server's CPU and memory, as if rented whole;
- `allocated`: only what pods are charged for, as if sized exactly to the workloads.

Both add the requested volume sizes. Assumptions, stated on the series:

| Item | Price (USD a month) | Derived from |
| --- | --- | --- |
| vCPU | 32.70 | c7i.2xlarge and r7i.2xlarge Linux hourly prices in eu-west-3, solved for CPU and memory, × 730 hours |
| Memory (GiB) | 3.00 | the same two instances |
| Volume (GiB) | 0.0928 | gp3 storage |

The comparison leaves out traffic, backups, managed services, support plans,
taxes, Savings Plans and reserved discounts. One Kubernetes CPU counts as one AWS
vCPU (both are hardware threads). Longhorn replicas count once, because EBS
replicates within its zone. The figures are in USD and are not converted.

The prices come from the AWS price list published on 2026-10-23
(`price_date` label). To refresh them, download
`https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/eu-west-3/index.csv`.
Read the on-demand, shared-tenancy Linux rows for `c7i.2xlarge` and `r7i.2xlarge`
and the `gp3` storage row. Then update the three
`swirl_cloud_hyperscaler_unit_price_monthly` rules in
`k8s/files/prometheus/rules.yaml`, their `price_date`, and
`tests/prometheus/hyperscaler-cost.yaml`.

## Dashboard

Grafana's **Cost Showback** dashboard (Applications folder) shows the monthly
total, its allocated, idle and shared parts, and cost per environment, namespace
and workload, plus allocated and idle cost over time, and the same capacity and
the allocated capacity at AWS list prices. Environments are namespaces
labeled `swirl-cloud.io/application-environment`; everything else is shared
platform.

Three panels work without recorded costs: each namespace's share of cluster CPU,
persistent storage requested by namespace, and a rightsizing table of requested
against used CPU and memory. Large gaps there are candidates for smaller
requests. Lowering them moves cost from the namespace to idle and lowers the
capacity a new server must provide; the bill changes only when a server is
added, resized or removed.

## Related Guides

- [Operations](../operations.md)
- [Compliance evidence](compliance-evidence.md)
- [Features](../features.md)
