# Node Enrollment And Private Networking

Run the node assistant from a registered control plane:

```bash
./add-node.sh
```

Choose the **role** (control plane or worker) and the **mode**: remote
enrollment from an existing control plane (recommended; it handles readiness,
quorum, scheduling and storage) or a local join on the new host. The assistant
only expands the shared platform; see
[application cluster nodes](#application-cluster-nodes) for other targets.

New hosts need Debian/Ubuntu, unique names and addresses, SSH keys, and root or
passwordless sudo. Prepare the [private transport](#private-node-network)
first; enrollment proves private SSH before applying the
[host exposure policy](../security.md#exposure-boundaries). Service redundancy
needs the [HA activation order](high-availability.md#activate-in-order).

## Private node network

| Transport | Use when | Prepare |
| --- | --- | --- |
| OVHcloud vRack | Every node is an eligible OVHcloud Dedicated Server | Activated vRack, attached interfaces, unused RFC1918 subnet and unique node addresses |
| Tailscale | Nodes span providers, locations or unrelated LANs | Tailnet and a temporary personal API access token |
| Local LAN | Every node is on one on-premises or home-lab network | Fixed RFC1918 address per node and a trusted node CIDR |

The [installer](../deployment.md#installation-prerequisites) and
[node assistant](../../add-node.sh) share this setup: prepare the private
interface, prove private SSH, then apply UFW. Keep console or rescue access
while configuring a new network.

### OVHcloud vRack

Record each server's service name, private NIC (name or MAC) and private
address. Attach servers in the OVHcloud Control Panel or let the wizard do it
through the API. Netplan or ifupdown is configured and the public route kept.

```bash
export K3S_NODE_TRANSPORT=vrack
export K3S_PRIVATE_ADDRESS=10.40.0.10
export K3S_PRIVATE_INTERFACE=eno2
export K3S_NODE_NETWORK_CIDR=10.40.0.0/24
export K3S_CONTROL_PLANE_IPS=10.40.0.11,10.40.0.12
export K3S_WORKER_IPS=10.40.0.20
export K3S_API_VIP=10.40.0.5
export K3S_CONTROL_PLANE_SSH_USER=admin K3S_WORKER_SSH_USER=admin
```

Optional: `K3S_PRIVATE_INTERFACE_MAC` (verify the NIC) and `K3S_VRACK_VLAN_ID`
(tagged VLAN). Node counts and scheduling are
[installer inputs](../deployment.md#unattended-installation).

API attachment also needs `OVH_VRACK_AUTOMATE_ACCOUNT=true`, `OVH_API_ENDPOINT`,
`OVH_VRACK_SERVICE_NAME`, `OVH_CONTROL_PLANE_SERVICE_NAME`,
`OVH_APPLICATION_KEY`, `OVH_APPLICATION_SECRET` and `OVH_CONSUMER_KEY`, with
these rights (revoke them after enrollment):

```text
GET  /vrack
GET  /vrack/*
POST /vrack/*/dedicatedServerInterface
GET  /dedicated/server/*/networking
```

### Tailscale

Create a short-lived personal API token (`tskey-api-`) under **Settings > Keys**
with an account that manages devices and policy; the wizard issues one-use
tagged device keys itself
([Tailscale API](https://tailscale.com/docs/reference/tailscale-api)).

```bash
export K3S_NODE_TRANSPORT=tailscale
export TAILSCALE_TAILNET='example.com' # '-' uses the token's tailnet
export TAILSCALE_MESH_NAME=swirl-cloud
export TAILSCALE_NODE_HOSTNAME=swirl-cloud-control-plane
read -rsp 'Tailscale personal API token: ' TAILSCALE_API_TOKEN
echo
export TAILSCALE_API_TOKEN
```

The cluster's role tags and grants are merged into the existing policy. K3s
binds to `tailscale0` with node CIDR `100.64.0.0/10`; etcd traffic is limited
to control planes. Unset and revoke the token afterwards.
`./scripts/configure-tailscale.sh --fleet` provisions only the mesh; use
`./add-node.sh` for K3s membership.

### Local LAN

Every node shares one switch, VLAN or router with a fixed RFC1918 address
(static or DHCP reservation). No host network changes: K3s binds to the
interface that owns each address, even a single NIC behind NAT.

```bash
export K3S_NODE_TRANSPORT=lan
export K3S_PRIVATE_ADDRESS=192.168.10.10
export K3S_NODE_NETWORK_CIDR=192.168.10.0/24
export K3S_CONTROL_PLANE_IPS=192.168.10.11,192.168.10.12
export K3S_WORKER_IPS=192.168.10.20
export K3S_API_VIP=192.168.10.5
export K3S_CONTROL_PLANE_SSH_USER=admin K3S_WORKER_SSH_USER=admin
```

Every address in `K3S_NODE_NETWORK_CIDR` can reach the K3s API, etcd, kubelet
and Longhorn ports: prefer a dedicated node VLAN or a narrow range without
personal or IoT devices. Private nodes accept SSH only from control planes
(`ssh -J admin@192.168.10.10 admin@192.168.10.20`).

vRack and LAN traffic never uses a public default route; that interface is
accepted only with an RFC1918 source. For internet exposure behind NAT, see
[Cloudflare origin behind NAT](../dns.md#origin-behind-nat).

## Role-specific installation

| Setting | Additional control plane | Worker |
| --- | --- | --- |
| K3s service | `k3s` server with embedded etcd | `k3s-agent` |
| Join credential | Existing server token | Node/agent-compatible token |
| K3s version | Exact bootstrap server version required | Remote joins inherit it; supply it for local joins |
| Scheduling | Inherit or explicitly select the cluster policy | Workload node |
| Persistent host controls | UFW and Lynis | UFW |

Both roles get AppArmor, the Registry mirror and Longhorn host prerequisites
(iSCSI/NFS). Added nodes get no public ingress, shared platform services,
Fail2ban or CrowdSec. Conflicting roles or identities are rejected.

With [HA](high-availability.md) active, new control planes also get an
ingress/Tunnel pair, the stored storage policy and Vault recovery material.
Update any [fencing inventory](node-fencing.md) for the new membership.

Align custom critical K3s flags before adding servers: network, component and
secrets-encryption settings must match
([embedded-etcd requirements](https://docs.k3s.io/datastore/ha-embedded)).

## Remote enrollment

Run on a registered control plane with its local K3s kubeconfig:

```bash
# Expand one control plane to three:
./add-node.sh --role control-plane --mode remote --count 2

# Add two workers:
./add-node.sh --role worker --mode remote --count 2
```

- `--count` is the number of additional nodes; each is still prompted for.
- The final control-plane count must be odd. Servers join one at a time and
  must become Ready.
- Control planes then serve the [API endpoint](#kubernetes-api-endpoint); on
  vRack and LAN this needs `--api-vip` until the endpoint is recorded.
- The registry host derives from the platform domain; `PLATFORM_DOMAIN`,
  `K3S_REGISTRY_HOST` and `K3S_REGISTRY_ENDPOINT` override it.

The first control-plane addition to a SQLite cluster backs up the database and
server credentials to `/var/backups/swirl-cloud/k3s/pre-etcd-<timestamp>` (root
only) and restarts the bootstrap server with embedded etcd. External or unknown
datastores are rejected. Worker additions never convert the datastore.

Unattended enrollment (the vRack addresses must already exist):

```bash
./add-node.sh --role control-plane --mode remote --transport vrack \
  --ips 10.40.0.12,10.40.0.13 --server-url https://10.40.0.10:6443 \
  --node-network-cidr 10.40.0.0/24 --api-vip 10.40.0.5 --ssh-user admin \
  --control-plane-schedulable preserve --non-interactive
```

- Workers: `--role worker` with the same options except `--api-vip`.
- LAN: `--transport lan` with LAN addresses.
- Tailscale: `--transport tailscale --hosts admin@host-02,admin@host-03`
  instead of `--ips`, plus the [Tailscale inputs](#tailscale).
- `--ips`, `--hosts`, `--non-interactive` and `--yes` all disable prompts.

To finish a failed batch, rerun with the same target list; joined members are
verified and reused. Never reset etcd or reinstall registered servers.

## Control-plane administration

Private nodes allow SSH only from control planes' private InternalIPs, and each
records its SSH account. After control-plane membership changes, refresh them
from a surviving control plane, using its own private address:

```bash
./scripts/reconcile-control-plane-access.sh \
  --node-network-cidr 10.40.0.0/24 --control-plane-ip 10.40.0.12 \
  --ssh-user admin
```

`--ssh-port` and `--identity-file` are optional. Only managed SSH rules change;
an unreachable node fails the run. K3s is not restarted.

`--server-url` may name any registered control plane; `--control-plane-ip` is
the enrolling host's SSH source. Once the [API endpoint](#kubernetes-api-endpoint)
is recorded, new nodes register through it, kubectl uses it, and agents use the
K3s
[client load balancer](https://docs.k3s.io/architecture#how-agent-node-registration-works).

## Kubernetes API endpoint

With three or more control planes, all of them serve one private API address,
so kubectl and joins survive any one server going down
([ADR 0028](../adr/0028-kubernetes-api-endpoint.md)):

| Transport | Endpoint | Failover |
| --- | --- | --- |
| vRack, LAN | `K3S_API_VIP`, an unused node-network address held by keepalived | Unicast VRRP moves the VIP when its holder stops or its `/readyz` fails |
| Tailscale | Tailscale Service `svc:<mesh>-api`, `https://<mesh>-api.<tailnet>.ts.net:6443` | Tailscale routes to hosts still advertising it; a server drains the Service while its API is not ready |

Control-plane enrollment sets it up. vRack and LAN need `--api-vip` or
`K3S_API_VIP`. Tailscale needs Tailscale Services enabled and Tailscale 1.86+
on control planes; enrollment creates the Service and grants admins, control
planes and workers TCP 6443. Every server certificate lists the endpoint
(servers missing it restart one at a time, keeping an etcd majority).
`kube-system/swirl-cloud-api-endpoint` records it.

Write an administrator kubeconfig (mode 0600) for the endpoint and copy it to a
trusted device over a private channel:

```bash
./scripts/configure-k3s-api-endpoint.sh --kubeconfig ~/cluster-admin.yaml
```

The VIP is reachable only inside the node network. From elsewhere, use
`ssh -L 6443:10.40.0.5:6443 admin@<control plane>` and server
`https://127.0.0.1:6443` (covered by every certificate). Tailscale clients
older than 1.94 need `tailscale set --accept-routes`.

`./scripts/configure-k3s-api-endpoint.sh --verify` checks it from a control
plane. On Tailscale, also run `kubectl get --raw /readyz` from an admin device
(a Service host may not reach its own Service). Repeat with each control plane
stopped during the [HA drill](high-availability.md#verify-before-relying-on-ha).

After removing or replacing a control plane, or on an older cluster, reconcile
it (needs an odd membership of three or more; omitted options come from the
record):

```bash
./scripts/configure-k3s-api-endpoint.sh --reconcile --transport lan \
  --address 192.168.10.5 --node-network-cidr 192.168.10.0/24 \
  --control-plane-ip 192.168.10.10 --ssh-user admin
```

To create the Tailscale Service on an existing cluster, run this with the
[Tailscale inputs](#tailscale), then reconcile with the printed values as
`--transport tailscale --address <IPv4> --hostname <name> --service svc:<mesh>-api`:

```bash
printf '%s\n' "$TAILSCALE_API_TOKEN" | \
  ./scripts/configure-tailscale.sh --role control-plane --api-service --api-token-stdin
```

## Scheduling and storage

New control planes inherit the scheduling policy; override it with
`--control-plane-schedulable true|false|preserve`. When workers join a
schedulable cluster, interactive runs offer controller-only mode; unattended
runs need an explicit choice. Without workers, control planes stay schedulable.

Topology reconciliation sets control-plane taints and Longhorn's settings, Helm
values, default StorageClass and volumes. The mode follows the
[stored HA profile](high-availability.md#reconciliation-and-recovery) (default
`false`). Turning stored HA off needs `HIGH_AVAILABILITY_ENABLED=false` plus
`--allow-ha-disable` and a separately planned migration; it does not migrate
applications or shrink replicas.

| Storage topology | Longhorn default replicas |
| --- | ---: |
| HA: at least three control planes and three storage-eligible nodes | 3 |
| Non-HA: no workers, regardless of control-plane count | 1 |
| Non-HA: one or more registered workers | Registered worker count |

- **HA:** schedulable control planes store data; controller-only ones do not,
  so that mode needs three workers
  ([host requirements](high-availability.md#prepare-hosts-and-capacity)). Same-host
  replicas are forbidden. Invalid topology fails before any change.
- **Non-HA with workers:** control-plane replicas move to workers.
- Replica counts are only ever raised, and NotReady nodes do not lower them.
  Reducing replicas or PVC size is a separate storage task.

Requested counts do not prove redundancy: before a failure test, check each
volume's healthy replicas sit on distinct nodes. Writer fencing is in
[HA recovery](high-availability.md#reconciliation-and-recovery).

After the [HA activation workflow](high-availability.md#activate-in-order), with
three control planes that run workloads:

```bash
HIGH_AVAILABILITY_ENABLED=true ./scripts/reconcile-cluster-topology.sh \
  --control-plane-schedulable true --update-longhorn-helm
```

## Local joins

Local mode joins only the current machine. Reach it over private SSH from the
bootstrap control plane so the SSH source check passes:

```bash
./add-node.sh --role worker --mode local
./add-node.sh --role control-plane --mode local
```

Get the join credential on the existing control plane:

```bash
# Additional control plane:
sudo cat /var/lib/rancher/k3s/server/token

# Worker (or a short-lived token: sudo k3s token create --ttl 1h):
sudo cat /var/lib/rancher/k3s/server/node-token
```

Paste it at the hidden prompt or use `--token-stdin`, never an argument.
Control-plane joins also need `--k3s-version` (exact server version), the
scheduling policy, and a cluster already on embedded etcd.

Local mode cannot convert the datastore or check final membership. Finish the
batch, then from the original control plane:

```bash
kubectl wait --for=condition=Ready node --all --timeout=5m
./scripts/reconcile-cluster-topology.sh \
  --control-plane-schedulable preserve --update-longhorn-helm
```

After local control-plane joins, also run the
[endpoint reconciliation](#kubernetes-api-endpoint).

`./add-node.sh --role <role> --mode <mode> --help` lists all options. See
[installation](../deployment.md#topology-and-scheduling) for initial planning;
[Ansible installation](../deployment.md#ansible-installation) uses the same
workflow, and `ansible/deploy.yml` does not add nodes.

## Application cluster nodes

`int`, `uat` and `prod` targets are separate clusters. Bootstrap the first host
with [`install-application-cluster.sh`](../../scripts/install-application-cluster.sh)
and register it per [installation](../deployment.md#application-deployment-clusters).

The bootstrap supports one control plane. More nodes are a manual native K3s
expansion: use the target's own token and exact K3s version; keep its pod CIDR,
API audiences, Tailscale addresses, registry mirror and private-port firewall;
plan its backups and quorum; keep platform credentials and HA helpers out.

Then add each node's Tailscale address to the environment's `nodeCIDRs`, update
tailnet grants, and rerun registration with the target's admin kubeconfig. It
checks each node's address, readiness and encrypted gateway route; it does not
provision nodes or copy Vault credentials.
