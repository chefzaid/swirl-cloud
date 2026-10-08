# ADR 0028: Serve The Kubernetes API From A Floating Control-Plane Endpoint

- Status: Accepted
- Date: 2026-10-08

## Context

With several control planes, embedded etcd keeps the cluster running when one
server fails, but administrator kubeconfigs and new nodes pointed at a single
server's address and lost the API with that server. K3s agents balance across
servers only after they have registered. The endpoint has to work on all three
[private transports](0011-private-transport-and-quorum.md), stay off the public
internet, and fail over without an operator.

## Decision

- Every control plane serves one private API endpoint once the cluster has an
  odd membership of at least three servers:
  - **vRack and LAN:** keepalived holds an operator-chosen VIP (`K3S_API_VIP`)
    from the node network. VRRP adverts are unicast between control planes
    (UFW allows VRRP from the node CIDR on the cluster interface), and a
    peerless instance faults instead of falling back to multicast.
  - **Tailscale:** each control plane hosts the Tailscale Service
    `svc:<mesh>-api` on TCP 6443. Policy auto-approves the control-plane tag
    as host and grants tailnet admins, control planes and workers access.
- A server holds the VIP, or keeps advertising the Service, only while its own
  `/readyz` succeeds. The probe authenticates with the K3s scheduler client
  certificate, not the administrator's.
- Each server's certificate lists the endpoint address and Tailscale name
  (`tls-san`). Joining servers receive the names before K3s first starts.
  Existing servers restart one at a time, only when their served certificate
  lacks a name, and only while a fresh Ready etcd majority survives.
- `kube-system/swirl-cloud-api-endpoint` records the endpoint. Nodes register
  through it when it answers from the enrolling control plane, and
  `configure-k3s-api-endpoint.sh --kubeconfig` writes administrator
  kubeconfigs for it.

## Rationale

A VIP or Service address keeps clients on standard TLS verification and needs no
client-side configuration. keepalived is a distribution package that runs
outside Kubernetes, so the endpoint keeps working while the API that would
schedule a load balancer is down. Tailscale cannot carry a layer-2 VIP, but its
Services route to healthy hosts natively. A DNS name with several addresses
would neither detect a failed server nor route around it.

## Consequences

vRack and LAN installations need one reserved address in the node network.
keepalived and VRRP become control-plane dependencies, and the VIP fails over
only within one layer-2 network. Tailscale installations depend on Tailscale
Services (hosts on 1.86 or later; clients before 1.94 must accept routes). A
service host is not guaranteed to reach its own Service, so the authoritative
Tailscale check runs from another tailnet device. Removing a control plane
requires `configure-k3s-api-endpoint.sh --reconcile` to update the VRRP peers.
The endpoint is private. Public traffic still uses Cloudflare and Traefik
([ADR 0009](0009-cloudflare-edge.md), [ADR 0021](0021-traefik-ingress.md)).
