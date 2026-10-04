# ADR 0025: Isolate Host Agents So `infra` Can Enforce Baseline Pod Security

- Status: Accepted
- Date: 2026-10-04

## Context

`infra` runs every shared platform service but only audited and warned on Pod
Security. A server-side dry run of the `baseline` enforce label showed that just
three workloads would be rejected, all node-level telemetry agents:
`node-exporter` (host network, host PID, host port, hostPath `/`), Fluent Bit
(container logs, journal and state) and Filebeat (host logs and state). Pod
Security admission is namespace-wide, so enforcing `baseline` on `infra` while
those agents run there would block their pods.

## Decision

- Run the three agents in a dedicated `host-agents` namespace with
  `enforce: privileged` and `audit`/`warn: baseline`. Nothing else may run there.
- Keep their dependencies explicit: the Fluent Bit credential ExternalSecret
  lives in `host-agents`, the platform `vault-backend` store admits that
  namespace, node-exporter discovery follows the move, and the installer and
  Ansible wait for the agents and their secret in `HOST_AGENTS_NAMESPACE`.
- After the move is verified (logs, journal, Lynis records and node metrics
  still flowing), enforce `baseline` on `infra`, keeping `restricted` audit and
  warning to guide further hardening.
- Network isolation for `infra` (default-deny ingress) remains a separate
  decision.

## Rationale

A separate namespace bounds privileged admission to three reviewed DaemonSets
instead of exempting all of `infra`. Exemptions in the K3s admission
configuration would need a server restart and are invisible in Git.

## Consequences

New host-level agents must be added to `host-agents`, not `infra`. Enforcement
rejects new or restarted `infra` pods that need host access, so such workloads
fail visibly instead of running with unreviewed privileges.
