# Security Reference

Vault owns infrastructure credentials; External Secrets supplies namespace-scoped
Kubernetes Secrets. Keep credentials, local environment files, setup tokens and
recovery material outside Git. Rotate bootstrap credentials and revoke temporary
setup tokens after use. See [image maintenance](operations.md#image-updates) for
container updates and Trivy findings.

## Security Posture Summary

| Layer | Implemented controls |
| --- | --- |
| Edge | Cloudflare proxied DNS, Full (strict) Origin CA TLS, DNSSEC, WAF and login rate limits, Access in front of administration hosts. See [DNS and ingress](dns.md#cloudflare). |
| Host | UFW default deny, origin HTTP/HTTPS limited to Cloudflare, SSH hardening, Fail2ban, CrowdSec, Lynis and AppArmor for containerd. |
| Cluster | Secrets encryption at rest, Pod Security Admission, admission policies denying hostPath and gitRepo volumes, hardened system controllers, default-deny ingress in application namespaces. |
| Identity | Keycloak realm with brute-force protection and password policy; native OIDC or OAuth2 Proxy for every administrative UI. |
| Secrets | Vault KV-v2 with Kubernetes and OIDC auth, two audit devices, External Secrets projection, host-only recovery material. |
| Supply chain | Every platform image pinned by digest, pinned GitHub Actions, in-cluster Trivy Operator reports. |

Known gaps are tracked as open items in [TODO.md](../TODO.md).

## Trust Boundaries

- Public traffic reaches Traefik only through Cloudflare. Direct mode trusts
  Cloudflare ranges; HA Tunnel mode trusts only the loopback connector.
- Administrative hosts require Cloudflare Access and a Keycloak session.
- Private node traffic stays on vRack, Tailscale or a trusted LAN; workers
  accept SSH only from control planes.
- Application namespaces receive only their own scoped Vault paths, data
  credentials and runners; they never receive platform administrator secrets.
- Control-plane root access includes Vault recovery authority.

## Exposure boundaries

| Node | Inbound access |
| --- | --- |
| First control plane | Default local or internet exposure; direct public HTTP/HTTPS is restricted to Cloudflare networks. HA ingress uses an outbound Tunnel instead of public ServiceLB |
| Additional control plane | Private SSH from verified control planes, Kubernetes API TCP 6443 and etcd TCP 2379–2380 plus node peers; HA adds an outbound Tunnel with local ingress, without public ServiceLB advertisement |
| Worker | Private SSH from verified control planes and required K3s/Longhorn peer traffic; no inbound server API, etcd or public ingress |

Added nodes use default-deny inbound UFW. Both host input and forwarded
Docker/Kubernetes traffic are restricted on non-cluster interfaces for IPv4
and IPv6; outbound connections and their replies remain available. Private
SSH must originate from the declared control-plane address before the
provider-facing path is closed. See [host policy](#host-policy) for controls
and [host audits](#host-audits), and [node enrollment](guides/node-enrollment.md) for role-specific setup.

## Host policy

Installer and Ansible use [configure-node-security.sh](../scripts/configure-node-security.sh).
Transport setup and required private ports are covered in [node enrollment](guides/node-enrollment.md#private-node-network).

| Node | Installed host controls |
| --- | --- |
| Internet-facing control plane | UFW, Lynis, Fail2ban, CrowdSec and SSH hardening. |
| Local/private control plane | UFW and Lynis. |
| Worker | UFW; no Fail2ban, CrowdSec or persistent Lynis installation. |

Internet-facing control planes retain password SSH, disable root SSH and limit
authentication attempts and connection bursts. Fail2ban escalates repeat bans;
CrowdSec also detects slow brute-force and user-enumeration patterns. The shared
script owns ban periods, authentication-log retention and rules. Follow
[the private SSH preflight](guides/node-enrollment.md#private-node-network) before applying a node's firewall.

Every node also receives a host baseline that never touches the firewall or
Kubernetes networking: a legal login banner in `/etc/issue` and
`/etc/issue.net`, and the package audit tools `libpam-tmpdir`, `debsums`,
`apt-show-versions` and `apt-listchanges` (text frontend, so APT never waits in
a pager). debsums verifies package checksums weekly and logs mismatches to the
system journal under the `debsums` tag. Configuration left behind by removed
kernel packages is purged; other removed packages keep their configuration.
Kernel settings in `/etc/sysctl.d/90-swirl-cloud-hardening.conf` restrict kernel
pointers, `dmesg`, unprivileged BPF, FIFO/regular-file creation in sticky
directories and ICMP redirects. Forwarding, reverse-path filtering, module
loading and SysRq stay at the values Kubernetes and console recovery need.
The unused DCCP, RDS, SCTP and TIPC protocol modules cannot be loaded; a
Service that needs SCTP must remove that entry first. USB storage remains
available for remote-console recovery media.
SSH session options (`ClientAliveInterval 300`, `ClientAliveCountMax 2`,
`TCPKeepAlive no`, `MaxSessions 4`, `LogLevel VERBOSE`) are validated with
`sshd -t` before a reload, which keeps existing sessions open. A full `--apply` includes it; to reconcile only the baseline
on a live host without resetting UFW, run:

```bash
./scripts/configure-node-security.sh --host-baseline
```

## Host audits

Control planes run `swirl-cloud-lynis.timer` monthly; the distro's daily
`lynis.timer` is masked so it cannot overwrite the shared report. Check its next run and inspect the
root-only reports under `/var/log/lynis-reports`; the latest report is
`/var/log/lynis-report.dat`. Schedule and archive retention are maintained in
[configure-lynis-schedule.sh](../scripts/configure-lynis-schedule.sh).

```sh
systemctl list-timers swirl-cloud-lynis.timer
swirl-cloud-audit-nodes
```

Run the audit assistant with the user and SSH identity used for enrollment.
It discovers workers, asks for SSH settings, temporarily copies Lynis to each
worker, runs it with passwordless `sudo`, retrieves reports into
`~/.local/state/swirl-cloud/lynis-reports`, and removes the temporary copy.
Use `--targets user@host,user@host` for explicit targets or `--help` for automation.
Filebeat ships control-plane findings to Kibana's **Lynis Security Audits**
dashboard; audit retention is separate from ordinary logs.

## Administrator sign-in

Normal browser access uses the administrator login selected during installation.
[Keycloak reconciliation](../k8s/platform/keycloak-sso.yaml) maintains matching
managed identities in `master` and the configured `KEYCLOAK_REALM`. A username
gets the primary email `<username>@<your-domain>`; an email login stays unchanged.
The `master` identity
can administer every realm at
`https://keycloak.<your-domain>/auth/admin/master/console/`.

Membership in `platform-admins` grants broad platform administration. Native
OIDC integrations establish service sessions where supported. Other UIs use the
Keycloak proxy boundary; administrative public hosts also sit behind Cloudflare
Access. Internal automation retains token-based endpoints. See [Cloudflare](dns.md#cloudflare)
for the hostname inventory and boundary configuration.

| Service | Managed administrator access |
| --- | --- |
| Keycloak | Master `admin` and configured SSO realm administration. |
| GitLab | Existing canonical `root` instance administrator. |
| Grafana / Argo CD | Grafana server administrator / Argo CD `role:admin`. |
| Vault | `platform-admin`, including all paths and `sudo`. |
| Portainer | Application administrator and Kubernetes `cluster-admin`. |
| Odoo / SonarQube | Existing Odoo Settings administrator / Sonar global administration. |
| Kibana / Elasticsearch | A distinct named Elastic identity with `superuser`. |
| DBGate / Kafka UI | Database administrator connections / full Kafka write access. |
| Longhorn / Homepage | Full UI access through the administrator gate. |

GitLab links SSO to its existing root account, preserving project ownership;
its bootstrap removes a duplicate matching the selected login. Profile sync
preserves locally edited GitLab names. Kibana validates the signed session and
keeps individual profiles, favorites and preferences. Its reconciler disables
managed identities that leave `platform-admins`; the dashboard-import account
is separate from browser identities. Give automation only its required service
credentials rather than this full administrator access.

## Recovery credentials

Read credentials only in a private terminal. For example, retrieve the managed
SSO login or password individually from `infra/keycloak-sso-credentials` using
keys `SSO_BOOTSTRAP_USERNAME` and `SSO_BOOTSTRAP_PASSWORD`:

```sh
kubectl get secret -n infra keycloak-sso-credentials \
  -o jsonpath='{.data.SSO_BOOTSTRAP_USERNAME}' | base64 -d
```

The same command works with the namespace, Secret and key below. These local
credentials serve recovery or automation; normal access uses SSO.

| Service | Namespace / Secret | Password key |
| --- | --- | --- |
| Grafana | `infra/grafana-admin-secret` | `GF_SECURITY_ADMIN_PASSWORD` |
| Keycloak bootstrap | `infra/keycloak-admin-secret` | `KC_BOOTSTRAP_ADMIN_PASSWORD` |
| Elasticsearch `admin` / `elastic` | `infra/elasticsearch-security-bootstrap` | `ADMIN_PASSWORD` / `ELASTIC_PASSWORD` |
| MongoDB | `infra/mongodb-secret` | `MONGO_INITDB_ROOT_PASSWORD` |
| PostgreSQL | `infra/postgres-secret` | `POSTGRES_PASSWORD` |
| Portainer | `infra/portainer-auth-secret` | `ADMIN_PASSWORD` |
| Odoo | `corp/odoo-secret` | `ODOO_ADMIN_PASSWORD` |

Username fields and Vault mappings live with the
[ExternalSecrets](../k8s/platform/vault.yaml) and [Odoo manifest](../k8s/corp/odoo.yaml).
Vault's `secret` KV engine also holds these recovery and automation fields:

| Service | Path | Field |
| --- | --- | --- |
| GitLab | `infra/gitlab` | `root_password` |
| SonarQube | `infra/sonarqube` | `admin_password`, `admin_token` |
| Longhorn origin login | `infra/platform-ui` | `password` |

Use Vault's authenticated UI to read those fields. During an SSO outage, reach
Vault locally with `kubectl port-forward -n infra service/vault 8200:8200` and
open `http://127.0.0.1:8200/ui`. Its Token login accepts the root-only recovery
token stored at `/var/lib/swirl-cloud/vault-bootstrap-token`. Keep it out of
command arguments and logs. Argo CD's local `admin` is disabled. DBGate signs in
through its own Keycloak client and admits only `platform-admins`; Kafka UI,
Longhorn and Homepage rely on their authentication boundary.

## Rotate local administrator passwords

The optional installer rotation step aligns infrastructure-local administrator
passwords. It does not run implicitly in unattended `--yes` installs; automation
must set `ROTATE_LOCAL_ADMIN_PASSWORDS=true` and provide `LOCAL_ADMIN_PASSWORD`
through its secret environment. To run it later in Bash:

```bash
read -rsp 'Local administrator password: ' LOCAL_ADMIN_PASSWORD
printf '\n'
printf '%s\n' "$LOCAL_ADMIN_PASSWORD" | \
  scripts/rotate-local-admin-passwords.sh --password-stdin
unset LOCAL_ADMIN_PASSWORD
```

[The rotation helper](../scripts/rotate-local-admin-passwords.sh) updates service
credentials, persists them in Vault, refreshes External Secrets, restarts affected
consumers and checks availability. Application users, Keycloak SSO identities,
API tokens and ingress Basic Auth credentials have separate lifecycles.

## Vault Operations

Vault stores platform credentials in integrated Raft storage. Installer and
Ansible render [the shared Helm values](../config/vault-values.yaml), including
the pinned [upstream image](operations.md#image-updates). Changes to those values need
a Helm reconciliation to update an existing release. External Secrets reads
Vault through Kubernetes authentication; its store and secret mappings are in
[vault.yaml](../k8s/platform/vault.yaml).

### Storage and audit

The server has a read-only root filesystem and a bounded memory-backed `/tmp`
for the chart's generated startup configuration. Its writable home, Raft data
and audit logs have separate mounts. Preserve the deployed file-owner identity
when updating the image: changing only its UID/GID can prevent Vault from
opening existing Raft and audit files.

Keep both audit devices enabled. Stdout reaches Fluent Bit; the file device
writes to the dedicated node-local `vault-audit` PVC, avoiding a Longhorn
dependency during startup. The unprivileged rotator shares Vault's process
namespace to rename the active file and send `SIGHUP`, then compresses closed
files. It receives no Vault token. Limits and retained archive counts live in
[the Helm values](../config/vault-values.yaml). Monitor audit-write failures and
volume pressure separately from ordinary application-log retention.

### Three-peer availability

The default remains one Vault server. Opting into `HIGH_AVAILABILITY_ENABLED=true`
uses [the HA overlay](../config/vault-ha-values.yaml): three Raft voters on three
different control planes, a disruption budget permitting one unavailable peer,
and a separate 5 GiB node-local audit claim for each pod. Data remains on Longhorn.
Three Ready, uncordoned control planes and working storage are prerequisites.
The profile tolerates the managed control-plane taint, so controller-only
control planes are supported. Adding replicas to one host does not provide
host-failure protection.

The chart uses `OnDelete`, so a Helm reconciliation does not restart existing
peers automatically. Roll out image/configuration changes one peer at a time,
checking quorum before and after each replacement:

```sh
./scripts/configure-vault-ha.sh --verify
```

This checks three Ready peers on different nodes, three voters and healthy Raft
Autopilot state. Two surviving peers can continue serving during one host outage.
Each node-local audit claim stays attached to its original host: permanent host
replacement requires an explicit audit-volume recovery plan, alongside recovery
of that member's data. Keep both audit devices and off-host log collection enabled.

#### Migrating an existing singleton

Use [the shared configurator](../scripts/configure-vault-ha.sh), rather than
applying the overlay directly: Kubernetes cannot add a StatefulSet claim template
in place. Schedule one maintenance operation from one control plane, verify a
restoration backup, and complete any [image transition](guides/platform-migration.md)
separately. Set `VAULT_VALUES_FILE` to the rendered `config/vault-values.yaml`.

```sh
VAULT_HA_MIGRATE_EXISTING=true ./scripts/configure-vault-ha.sh \
  --namespace infra --values "$VAULT_VALUES_FILE"
HIGH_AVAILABILITY_ENABLED=true ./scripts/configure-vault.sh infra
```

The explicit flag is also required when the installer or Ansible encounters the
singleton layout. The helper saves a fresh Raft snapshot, checks its checksum,
and archives the current Helm values and StatefulSet under the root-only
`/var/lib/swirl-cloud/backups/` directory. It then orphans the existing controller,
retaining the running `vault-0`, and recreates the controller with per-pod audit
claims. It replaces `vault-0` only after both new peers are unsealed and all three
are healthy voters. Deletions check object identity; no PVC is deleted.

The original `data-vault-0` remains in use. The original `vault-audit` is retained
as an audit archive after `audit-vault-0` takes over. If joining fails, leave the
original pod and retained PVCs in place, diagnose the failed peers, then rerun
the gated helper. If controller creation itself failed, restore the archived
controller before retrying. Do not scale a live three-voter cluster back to one
by changing only the Helm replica count.

### Upgrade and recovery

Test the candidate against the deployed image with disposable data outside the
production cluster. Verify existing-volume upgrade, stored secrets, access
controls, authentication, transit decryption, UI assets, audit rotation and
restart. Restore a snapshot into a separate volume using the previous image
to establish the recovery path. Keep the fixture and logs outside Git.

Before a production Helm update, save a fresh Raft snapshot and verify its
archive checksums. Test restoration into an isolated, network-disabled instance
with the original unseal key and bootstrap token; compare secret hashes,
policies, authentication methods and audit devices against a private inventory.
Keep snapshots, keys, tokens, inventories and fixture logs outside Git. A
successful snapshot command alone does not establish recoverability.

After rollout, verify readiness, unsealed status, stored data, mounts and auth
methods, both audit devices, and every ExternalSecret's Ready condition. Check
the host unseal service and fresh reports for the running image digest. Retain
the verified backup and previous image for recovery; do not start an older
binary directly against upgraded Raft data.

### Host unseal service

The root-owned `swirl-cloud-vault-unseal.service` is scheduled by
[swirl-cloud-vault-unseal.timer](../config/host/swirl-cloud-vault-unseal.timer). Its
[helper](../scripts/vault-unseal.sh) discovers running Vault server pods, checks
seal status and reads
`/var/lib/swirl-cloud/vault-unseal-key` only when unsealing is needed. Keep that
file root-owned with mode `0600`, outside Git and diagnostic output.

An initialized peer is unsealed with the existing key. An empty replacement
joins an unsealed surviving member before receiving that key; the timer never
initializes a cluster. Configuration also refuses initialization when host
recovery material exists but no initialized peer is reachable. Existing runtime
maintenance commands and backups select an unsealed peer, preferring the leader.

In HA mode, distribute the same recovery files and install the timer on every
verified control plane after initial Vault configuration and future enrollment:

```sh
./scripts/sync-vault-recovery.sh --all-control-planes \
  --node-network-cidr "$K3S_NODE_NETWORK_CIDR" \
  --control-plane-ip "$K3S_PRIVATE_ADDRESS" --ssh-user "$K3S_NODE_SSH_USER"
```

This requires private SSH access and passwordless sudo using
[the control-plane administration policy](guides/node-enrollment.md#control-plane-administration).
The helper verifies Kubernetes control-plane roles, private addresses and the
actual SSH endpoints. Only those hosts receive the unseal key and bootstrap
token, as root-owned `0600` files in a `0700` directory. Values travel through
encrypted SSH stdin; differing existing recovery material is never overwritten.
Workers receive neither file. Control-plane root access therefore includes Vault
recovery authority. The original control-plane timer remains the default until
this explicit distribution is requested.

The anonymous `sys/unseal` request carries the key as JSON over
`kubectl exec -i` stdin, keeping it out of process arguments and Kubernetes exec
metadata. Preserve this handling when changing the helper or initial installer
configuration. [The regression test](../tests/test-vault-unseal.py) verifies
stdin handling and error propagation; a real recovery fixture verifies actual
unsealing. [HA tests](../tests/test-vault-ha.py) cover survivor selection,
join/unseal ordering, quorum gates and recovery-file target validation.

Check scheduling and the last result without displaying credentials:

```sh
sudo systemctl show swirl-cloud-vault-unseal.timer \
  -p ActiveState -p SubState -p UnitFileState -p LastTriggerUSec
sudo systemctl show swirl-cloud-vault-unseal.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp
```

A check on an already unsealed server verifies the status path. Exercise actual
unsealing in an isolated recovery test, without sealing the live cluster.
See [security and access](#recovery-credentials) for credential lookup.

## Related Guides

- [Architecture](architecture.md)
- [Public DNS And Ingress](dns.md)
- [Operations](operations.md)
- [Data model](data-model.md)
- [Node enrollment](guides/node-enrollment.md)
