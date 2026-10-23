# October 2026 service upgrades

## Longhorn 1.12.1 to 1.13.0 (2026-10-05)

Longhorn 1.13.0 bundles CSI provisioner v6.3.0, removing provisioner v5.3.0's Go
`crypto/tls` and gRPC findings (no v5.3 patch exists). It needs Kubernetes 1.34 or
later (installed: 1.36.4). Downgrades are not supported; the next upgrade is 1.13.x
or 1.14.x.

Preconditions: no faulted volumes or backing images, every volume on the v1 data
engine, a fresh recovery archive, and a `<volume>-pre-1-13` Longhorn snapshot of
each attached volume. The guaranteed instance-manager CPU was first lowered from
12 % to 5 %: Longhorn applies the new value only to the instance manager the
upgrade creates, so no volume was interrupted.

`scripts/reconcile-platform-release.sh longhorn` ran the upgrade. It sets
`longhornGlobalManager.replicas` to the replica count (one here; the chart default
of three only spreads across servers) and the guaranteed CPU from
`DEFAULT_LONGHORN_GUARANTEED_INSTANCE_MANAGER_CPU`.

Result: all 21 engines upgraded live, every volume attached and healthy,
`./install.sh --status` 12/12. Replicas moved to the new instance manager (400m
request); engines stayed in the 1.12.1 one (960m) until their volume reattached.
The same day, with automated sync paused on the owning Argo CD Applications, each
of the 16 stateful workloads was scaled to zero until its volumes detached, then
back to one. Longhorn removed the old instance manager and node CPU requests fell
from 94.8 % to 81 %. Vault's `OnDelete` StatefulSet reports no rollout status;
check `vault-0` readiness and seal status instead.

The pre-upgrade snapshots were deleted after verification. Each stays listed as
removed because Longhorn keeps a removed snapshot that is the volume head's direct
parent; a one-off trim with snapshot unmapping enabled returned the freed blocks.
