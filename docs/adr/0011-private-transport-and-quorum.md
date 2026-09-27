# ADR 0011: Keep Nodes On A Private Transport With An Odd Embedded-etcd Quorum

- Status: Accepted
- Date: 2026-08-23
- Amended: 2026-09-06, 2026-09-09

## Context

Additional hosts may be OVHcloud dedicated servers in one vRack, or machines at
other providers and locations. Kubernetes API, etcd, kubelet and Longhorn traffic
must never cross the public internet, and new hosts must be enrollable without
opening public ports.

## Decision

- Support two private transports: OVHcloud vRack (optional API attachment,
  Netplan/ifupdown, VLAN and MAC verification) and Tailscale (role-tagged mesh,
  one-use device keys, K3s bound to `tailscale0`, etcd grants limited to control planes).
- Enroll remotely over private SSH with [`add-node.sh`](../../add-node.sh); prove
  the private SSH source before applying UFW.
- Require an odd final control-plane count; convert SQLite to embedded etcd with
  an integrity-checked backup before adding servers; join servers sequentially.
- Keep role-specific installation: servers with embedded etcd, workers as agents.

## Rationale

vRack gives a provider-native layer-2 network for same-provider fleets; Tailscale
covers hybrid fleets without managing VPN servers. Odd quorum avoids split-brain
membership. Private-first enrollment prevents a mistyped firewall from isolating
a remote host.

## Consequences

Tailscale adds a coordination-service dependency and temporary API tokens that
operators must revoke. There is no floating API endpoint; external clients must
select a reachable server. Application clusters are separate K3s clusters, not
worker pools ([ADR 0024](0024-deployment-environments.md)).
