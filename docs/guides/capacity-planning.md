# Capacity Planning

Kubernetes schedules pods by their **requests**, the CPU and memory a pod
reserves whether it uses them or not. A cluster is full when requests reach its
allocatable capacity, even if usage is low. Plan on requests, watch usage, and
keep room to lose a server.

## Targets

| Target | Why |
| --- | --- |
| Requests below 70 % of allocatable CPU and memory | Room for rollouts, which briefly run old and new pods, and for growth. |
| Requests below 100 % of the capacity left without the largest server | Pods from a failed host can restart elsewhere; a prerequisite for [high availability](high-availability.md). |
| More than 30 days until any filesystem or volume fills | Time to clean up or expand calmly. |

## Dashboard and alerts

Grafana's **Capacity Planning** dashboard (Applications folder) shows requested
and used CPU and memory, requests per server, requests after losing the largest
server (empty on a single server), the cores and memory to add, and the storage
running out with days left at the current rate.

| Alert | Fires when |
| --- | --- |
| [`ClusterCapacityRequestsHigh`](alert-runbooks.md#clustercapacityrequestshigh) | Requests stay above 90 % of allocatable CPU or memory for 30 minutes |
| [`ClusterCannotSurviveNodeLoss`](alert-runbooks.md#clustercannotsurvivenodeloss) | Requests exceed the capacity left without the largest server for an hour |

Filesystem and volume alerts are in the [capacity runbooks](alert-runbooks.md#capacity).

| Series | Meaning |
| --- | --- |
| `swirl_cloud_capacity:requests:ratio{resource}` | Requested share of allocatable CPU or memory |
| `swirl_cloud_capacity:node_requests:ratio{node,resource}` | The same per server |
| `swirl_cloud_capacity:requests_after_node_loss:ratio{resource}` | Requests against capacity without the largest server (multi-server clusters only) |
| `swirl_cloud_capacity:additional_needed{resource}` | CPU cores or memory bytes to add so requests stay below 70 % |
| `swirl_cloud_capacity:filesystem_days_until_full`, `swirl_cloud_capacity:volume_days_until_full` | Days until full at the six-hour trend, only while shrinking |

## Deciding what to do

1. **Lower requests first** when requests are high but usage is low. The
   rightsizing table in [Cost Showback](cost-showback.md#dashboard) lists
   namespaces that reserve far more than they use. Lower requests in the owning
   manifests; application repositories own theirs.
2. **Add a server** when usage nears the targets, or to survive a host failure.
   Size it from the cores and memory to add, then follow
   [node enrollment](node-enrollment.md).
3. **Grow storage** when a volume will fill within a month: clean up through the
   service or expand the claim.

The [platform reports](platform-reports.md) keep hourly allocation per namespace
for 13 months, for long-term trends.

## Related Guides

- [Cost showback](cost-showback.md)
- [Platform reports](platform-reports.md)
- [Node enrollment](node-enrollment.md)
