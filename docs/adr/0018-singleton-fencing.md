# ADR 0018: Require Physical Fencing Before Recovering Persistent Singletons

- Status: Accepted
- Date: 2026-09-10

## Context

GitLab Omnibus, SonarQube Community and Odoo keep one writer on a Longhorn volume.
If their host becomes unreachable, Kubernetes' timeout-based force detach can start
a replacement while the old writer may still be running, corrupting data. A
network partition is not proof that a host has stopped.

## Decision

- In HA, disable timeout-based force detach on every control plane
  (`disable-force-detach-on-timeout=true`) and keep Longhorn's
  `node-down-pod-deletion-policy` at `do-nothing`.
- Offer an optional fencing CronJob that powers off an explicitly inventoried host
  through its Redfish BMC (`ForceOff`), verifies the off state and quorum, then
  applies `node.kubernetes.io/out-of-service:NoExecute`.
- Act on at most one host per run, only after five minutes of NotReady and stale
  Lease, with a majority of pinned control planes healthy.
- Without verified hardware access, keep fencing disabled and recover manually
  through the Kubernetes non-graceful shutdown procedure.

## Rationale

Fencing proves the old writer cannot write before storage moves. Redfish is a
vendor-neutral, verifiable power interface; SSH or provider accounts are not.

## Consequences

Automatic singleton recovery requires BMC access reachable from surviving control
planes, which the current OVHcloud host does not provide. Returning a fenced host
is a manual operation. Membership changes pause fencing until the inventory is
refreshed.
