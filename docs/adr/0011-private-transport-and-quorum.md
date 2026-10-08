# ADR 0011: Keep Nodes On A Private Transport With An Odd Embedded-etcd Quorum

- Status: Accepted
- Date: 2026-08-23
- Amended: 2026-09-06, 2026-09-09, 2026-10-04, 2026-10-08

## Context

Additional hosts may be OVHcloud dedicated servers in one vRack, machines at
other providers and locations, or on-premises hosts on one local network. Kubernetes API, etcd, kubelet and Longhorn traffic
must never cross the public internet, and new hosts must be enrollable without
opening public ports.

## Decision

- Support three private transports: OVHcloud vRack (optional API attachment,
  Netplan/ifupdown, VLAN and MAC verification), Tailscale (role-tagged mesh,
  one-use device keys, K3s bound to `tailscale0`, etcd grants limited to control
  planes) and a local LAN (existing fixed RFC1918 addresses, no host network
  changes; the default-route NIC is accepted only behind NAT).
- Enroll remotely over private SSH with [`add-node.sh`](../../add-node.sh); prove
  the private SSH source before applying UFW.
- Require an odd final control-plane count; convert SQLite to embedded etcd with
  an integrity-checked backup before adding servers; join servers sequentially.
- Keep role-specific installation: servers with embedded etcd, workers as agents.

## Rationale

vRack gives a provider-native layer-2 network for same-provider fleets; Tailscale
covers hybrid fleets without managing VPN servers; a LAN serves on-premises
fleets that already share a trusted network. Odd quorum avoids split-brain
membership. Private-first enrollment prevents a mistyped firewall from isolating
a remote host.

## Consequences

Tailscale adds a coordination-service dependency and temporary API tokens that
operators must revoke. LAN traffic is unencrypted and the node CIDR is trusted,
so the LAN must be dedicated or narrowly scoped. Clients reach the API through
a floating control-plane endpoint ([ADR 0028](0028-kubernetes-api-endpoint.md)).
Application clusters are separate K3s clusters, not worker pools
([ADR 0024](0024-deployment-environments.md)).
