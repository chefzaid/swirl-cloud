# October 2026 service upgrades

## Longhorn 1.12.1 to 1.13.0 (2026-10-05)

Longhorn 1.13.0 bundles CSI provisioner v6.3.0, which removes the Go
`crypto/tls` and gRPC findings of provisioner v5.3.0 (no v5.3 patch exists). It
requires Kubernetes 1.34 or later (installed: 1.36.4).

Before the upgrade: no faulted volumes or backing images, all volumes on the v1
data engine, a fresh recovery archive, and a Longhorn snapshot of every attached
volume (`<volume>-pre-1-13`). The snapshots were deleted once the volumes were
verified healthy; Longhorn postpones removing them while an engine still runs in
the previous instance manager ("snapshot deletion delayed: volume engine ... is
upgrading"), so each disappears when its volume is next reattached. They hold
only the blocks changed since the snapshot. The guaranteed
instance-manager CPU was lowered from 12 % to 5 % first: Longhorn keeps the
request of an instance manager that runs engines and applies the new value to the
instance manager the upgrade creates, so no volume was interrupted.

`scripts/reconcile-platform-release.sh longhorn` performed the upgrade. It also
sets `longhornGlobalManager.replicas` to the replica count (one here; the chart
default of three only spreads across servers) and the guaranteed CPU from
`DEFAULT_LONGHORN_GUARANTEED_INSTANCE_MANAGER_CPU`.

Result: all 21 engines upgraded live, every volume attached and healthy,
`./install.sh --status` 12/12. Replica processes moved to the new instance
manager (400m request); engine processes stay in the 1.12.1 instance manager
(960m) until each volume is next detached and reattached, for example when its
pod restarts, after which Longhorn removes the old instance manager and frees
its CPU reservation.

Downgrades are not supported; the next upgrade is 1.13.x or 1.14.x.
