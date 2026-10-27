# Security Reference

Vault owns infrastructure credentials; External Secrets projects them into
namespace-scoped Kubernetes Secrets. Keep credentials, local environment files,
setup tokens and recovery material outside Git. Rotate bootstrap credentials and
revoke setup tokens after use. Container updates are in
[image maintenance](operations.md#image-updates).

## Security Posture Summary

| Layer | Implemented controls |
| --- | --- |
| Edge | Cloudflare proxied DNS, Full (strict) Origin CA TLS, DNSSEC, WAF and login rate limits, Access in front of administration hosts. HSTS (subdomains, no preload) and `nosniff`, repeated by Traefik at the origin; apps own framing and Content Security Policy. See [DNS and ingress](dns.md#cloudflare). |
| Host | UFW default deny, origin HTTP/HTTPS limited to Cloudflare, SSH hardening, Fail2ban, CrowdSec, Lynis and AppArmor for containerd. |
| Cluster | Secrets encryption at rest, Pod Security Admission, admission policies denying hostPath and gitRepo volumes, hardened system controllers, default-deny ingress in application namespaces. |
| Identity | Keycloak realm with brute-force protection and password policy; native OIDC or OAuth2 Proxy for every administrative UI. |
| Secrets | Vault KV-v2 with Kubernetes and OIDC auth, two audit devices, External Secrets projection, host-only recovery material. |
| Supply chain | Every platform image pinned by digest, pinned GitHub Actions, in-cluster Trivy Operator reports. |

Known gaps are open items in [TODO.md](../TODO.md).

## Trust Boundaries

- Public traffic reaches Traefik only through Cloudflare. Direct mode trusts
  Cloudflare ranges; HA Tunnel mode trusts only the loopback connector.
- Administrative hosts require Cloudflare Access and a Keycloak session.
- Node traffic stays on vRack, Tailscale or a trusted LAN; workers accept SSH
  only from control planes.
- Application namespaces get only their own Vault paths, data credentials and
  runners, never platform administrator secrets.
- Control-plane root access includes Vault recovery authority.

## Exposure boundaries

| Node | Inbound access |
| --- | --- |
| First control plane | Local or internet exposure; direct public HTTP/HTTPS only from Cloudflare networks. HA ingress uses an outbound Tunnel instead of public ServiceLB |
| Additional control plane | Private SSH from verified control planes, Kubernetes API TCP 6443 and etcd TCP 2379–2380 from node peers, VRRP from the node network on vRack/LAN; HA adds an outbound Tunnel with local ingress and no public ServiceLB |
| Worker | Private SSH from verified control planes and required K3s/Longhorn peer traffic; no server API, etcd or public ingress |

Every control plane serves the private
[Kubernetes API endpoint](guides/node-enrollment.md#kubernetes-api-endpoint): a
keepalived VIP on vRack/LAN (UFW accepts VRRP only from the node network on the
cluster interface), or on Tailscale a Service that only tailnet admins, control
planes and workers reach on TCP 6443. Its readiness probe uses the K3s scheduler
client certificate.

Added nodes deny inbound host and forwarded traffic on non-cluster interfaces
(IPv4 and IPv6); outbound traffic stays open. Private SSH must work from the
declared control-plane address before the provider-facing path is closed. See
[node enrollment](guides/node-enrollment.md) for role-specific setup.

Odoo is not behind Cloudflare Access: its login page, Live Chat endpoints and the
website chat frame (`/platform_crm_intake/chat/<id>`) are public by design. Chat
starts share the login rate limit, the website's lead intake key can only create
leads, and emailed replies are accepted only from the Cloudflare Email Worker.
See [Odoo website integration](guides/odoo-website.md#chat-by-email).

## Host policy

The installer and Ansible use
[configure-node-security.sh](../scripts/configure-node-security.sh); private
ports are in [node enrollment](guides/node-enrollment.md#private-node-network).

| Node | Installed host controls |
| --- | --- |
| Internet-facing control plane | UFW, Lynis, Fail2ban, CrowdSec and SSH hardening. |
| Local/private control plane | UFW and Lynis. |
| Worker | UFW; no Fail2ban, CrowdSec or persistent Lynis installation. |

Internet-facing control planes keep password SSH, disable root SSH and limit
authentication attempts and connection bursts. Fail2ban escalates repeat bans;
CrowdSec also catches slow brute force and user enumeration. Follow
[the private SSH preflight](guides/node-enrollment.md#private-node-network)
before applying a node's firewall.

`swirl-cloud-intrusion-metrics.timer` exports Fail2ban and CrowdSec counts (bans,
failures, decisions; no addresses) and `swirl_cloud_host_firewall_active` (UFW
active, IPv4 and IPv6 `INPUT` default-deny) every five minutes for the
[host security alerts](guides/alert-runbooks.md#host-security-and-maintenance);
`./install.sh --status` also checks the firewall. If UFW cannot start even after
`ufw-init force-reload`, the script drops all inbound traffic except SSH,
loopback, established connections and cluster interfaces, then fails.

Every node also gets a host baseline that never touches the firewall or
Kubernetes networking:

- Login banner in `/etc/issue` and `/etc/issue.net`; `libpam-tmpdir`,
  `apt-show-versions`, `apt-listchanges` (text frontend) and `debsums` (weekly
  checksum check, logged under the `debsums` tag).
- `pam_pwquality` for new passwords (12+ characters, dictionary and user-name
  checks). Config of removed kernel packages is purged.
- `/etc/sysctl.d/90-swirl-cloud-hardening.conf` restricts kernel pointers,
  `dmesg`, unprivileged BPF, sticky-directory file creation and ICMP redirects;
  forwarding, rp_filter, module loading and SysRq stay as Kubernetes and console
  recovery need.
- DCCP, RDS, SCTP and TIPC modules are blocked (a Service that needs SCTP must
  remove that entry first); USB storage stays available for recovery media.
- `/etc/crontab`, `/etc/cron.*` and `/etc/ssh/sshd_config` are root-only, kept
  across upgrades by `dpkg-statoverride`. SSH session options
  (`ClientAliveInterval 300`, `ClientAliveCountMax 2`, `TCPKeepAlive no`,
  `MaxSessions 4`, `LogLevel VERBOSE`) pass `sshd -t` before a reload.
- `auditd` watches accounts, sudoers, SSH, cron, systemd units, K3s and Swirl
  Cloud configuration, UFW, the time zone and module loads (`ausearch -k
  identity`, `-k k3s-config`, ...); logs rotate at 8 × 32 MB and a full disk
  suspends auditing. `acct` keeps 14 days of process accounting (`lastcomm`).
- `swirl-cloud-rkhunter.timer` scans every Sunday, report-only (`journalctl -u
  swirl-cloud-rkhunter.service`, `swirl_cloud_rkhunter_warnings`); whitelist
  reasons are in `/etc/rkhunter.conf.local`.

A full `--apply` includes the baseline. To reapply only the baseline without
resetting UFW:

```bash
./scripts/configure-node-security.sh --host-baseline
```

## Host audits

Control planes run `swirl-cloud-lynis.timer` monthly (the distro's daily
`lynis.timer` is masked). Reports are root-only under `/var/log/lynis-reports`;
the latest is `/var/log/lynis-report.dat`. The schedule lives in
[configure-lynis-schedule.sh](../scripts/configure-lynis-schedule.sh).

```sh
systemctl list-timers swirl-cloud-lynis.timer
swirl-cloud-audit-nodes
```

Run `swirl-cloud-audit-nodes` with the enrollment user and SSH identity. It
copies Lynis to each worker, runs it with passwordless `sudo`, collects reports
into `~/.local/state/swirl-cloud/lynis-reports` and removes the copy
(`--targets user@host,...` for explicit hosts). Control-plane findings appear in
Kibana's **Lynis Security Audits** dashboard.

Accepted findings live in `/etc/lynis/custom.prf` with an owner, review date and
rationale each. Entries skip one check or value (for example
`KRNL-6000:kernel.sysrq`), so new gaps in the same test still show. Change them
in [configure-node-security.sh](../scripts/configure-node-security.sh), never on
a host.

## Kubernetes API audit log

[`configure-k3s-audit.sh`](../scripts/configure-k3s-audit.sh) installs the
[audit policy](../config/host/k3s-audit-policy.yaml) and the drop-in
`/etc/rancher/k3s/config.yaml.d/40-swirl-cloud-api-audit.yaml`. Each K3s server
writes `/var/log/kubernetes/audit/audit.log` (root only; 100 MB × 10 files, 30
days). It records who changed which object and every read of Secrets, ConfigMaps
and service-account tokens, at `Metadata` level only, so secret values are never
logged. Probes, leases, events, node reads and routine service-account reads are
dropped.

The installer and control-plane enrollment set it up before K3s starts. On an
existing server it applies at the next K3s restart; to apply now, use
**Maintenance → Apply Kubernetes API audit logging now** or run
`scripts/configure-k3s-audit.sh --restart` (the API is down for about a minute;
workloads keep running). `./install.sh --status` reports whether it is active.

Fluent Bit ships the log to `kubernetes-audit-*` (`verb`, `user.username`,
`objectRef.*`, `responseStatus.code`, `sourceIPs`, `node`), deleted after 30 days
by `swirl-cloud-audit-30d`. In Kibana, filter on `objectRef.resource : secrets`;
on a host, `sudo jq 'select(.objectRef.resource=="secrets")' <log>`.

## Security detection

The [security detection](guides/security-detection.md) job turns identity
events, the API audit log and Keycloak state into alerts every five minutes.

## Compliance evidence

The [compliance evidence guide](guides/compliance-evidence.md) maps these
controls to NIS2, ISO/IEC 27001, DORA, GDPR and CIS, evaluates them continuously
and exports signed evidence packs.

## Just-in-time administration

Grant `platform-admins` for a limited time, with a reason and an approver who is
not the user, instead of permanent membership:

```bash
./install.sh --admin-access grant --user alice --hours 4 \
  --reason "INC-42: restore the billing database" --approved-by bob
./install.sh --admin-access list
./install.sh --admin-access revoke --user alice
```

Grants last 1 to 24 hours and are recorded in the Elasticsearch index
`access-grants`. Security detection removes the membership within five minutes
of expiry, and `NewPlatformAdministrator` reports each grant. Members added
directly in Keycloak are never removed automatically: keep them to the
break-glass accounts.

## Identity events

`keycloak-sso-reconcile` enables event storage in every realm: login events
(logins, failures, logouts, credential changes, lockouts, identity-provider
links) and admin events (who changed which user, client, role or realm setting),
kept 90 days under **Realm settings → Events**. Admin event details stay off, so
stored events never hold submitted representations.

Keycloak's `jboss-logging` listener also logs every event. The logging agent
moves those lines (only from the `keycloak` container in `infra`) into monthly
`identity-audit-*` indices, with fields under `keycloak.*` (`type`,
`operationType`, `realmName`, `clientId`, `userId`, `username`, `ipAddress`,
`error`, `resourceType`, `resourcePath`) and `event_category` (`user`/`admin`).
`swirl-cloud-identity-audit` deletes each index 396 days after it starts, so
events are kept at least a year. Filter in Kibana on, for example,
`keycloak.type : LOGIN_ERROR`. Admin events carry the acting administrator's
realm; the target is in `keycloak.resourcePath`.

## Application policies

The application policy pack
([ADR 0027](adr/0027-native-admission-policy-packs.md),
[`k8s/base/application-policies.yaml`](../k8s/base/application-policies.yaml))
checks Deployments, StatefulSets, DaemonSets and CronJobs in application
namespaces:

| Rule | Why |
| --- | --- |
| Images pinned by digest (`image@sha256:...`) | The running code is exactly what was scanned and released. |
| Images from `registry.<domain>` | Images pass through the platform Registry and its scanning. |
| CPU and memory requests and a memory limit | Scheduling, capacity planning and cost showback depend on them. |
| An `app` pod label | Cost showback, discovery and dashboards group by it. |
| `swirl-cloud.io/owner` and `swirl-cloud.io/data-classification` (`public`, `internal`, `confidential`, `restricted`) workload labels | Accountability and the records of processing. |

It runs in **Warn and Audit** mode: changes are admitted with a warning and an
audit-log entry. `./install.sh --policy-report` dry-runs existing workloads and
lists violations. Once an environment has none, move its binding to `Deny`,
starting with `prod`.

## Encrypted volumes

Claims with StorageClass `longhorn-encrypted` are encrypted with dm-crypt (LUKS2,
`aes-xts-plain64`, Argon2i), so replicas, snapshots and backups hold only
ciphertext:

```yaml
spec:
  storageClassName: longhorn-encrypted
```

The key lives in Vault at `infra/longhorn` (`volume_encryption_key`) and reaches
Longhorn through the `longhorn-volume-encryption` Secret in `infra`, which
removing its ExternalSecret does not delete. **Never change or delete the key**:
every encrypted volume and backup needs it; it is in the Vault recovery archives
and the recovery kit. `scripts/configure-longhorn-host.sh` loads `dm_crypt`.
Existing volumes stay unencrypted; restore their data into a new encrypted claim.

## Host disk encryption

In a rented data centre, disks leave the operator's hands: replaced drives, a
returned server, a stolen chassis. What is encrypted at rest today:

| Data | Encrypted at rest |
| --- | --- |
| Kubernetes Secrets in the K3s datastore | Yes, K3s secrets encryption |
| Volumes with StorageClass `longhorn-encrypted` ([above](#encrypted-volumes)) | Yes, LUKS2 per volume |
| Off-node archives and volume backups | Yes, restic encrypts before upload |
| Vault's storage | Yes, Vault's own barrier encryption; its unseal key sits on the same host unless the [recovery kit](#recovery-kit) holds it |
| Everything else on the host: the K3s datastore itself, other Longhorn volumes (databases, GitLab), local archives in `/var/backups/swirl-cloud`, images and logs | No |

The option for the rest is LUKS2 on the host's data partitions
(`/var/lib/rancher`, `/var/lib/longhorn`, `/var/backups`), set up when a node is
installed or rebuilt, since encrypting a partition in place is not supported.
Unattended security reboots (within a week) need an unlock that does not wait
for a person:

| Unlock | Protects against | Needs |
| --- | --- | --- |
| TPM2 (`clevis-tpm2`) | A disk removed from the server | A TPM in the server; a stolen whole server still unlocks |
| Tang server (`clevis` with `tang`) | A disk or server taken away from the network | A Tang server outside the host's failure domain |
| Passphrase over SSH at boot (`dropbear-initramfs`) | Any loss while powered off | A person at every reboot; conflicts with automatic reboots |

Ubuntu packages `cryptsetup` and `clevis-luks` bind a LUKS2 partition to the
unlock (`clevis luks bind -d /dev/<partition> tpm2 '{"pcr_ids":"7"}'`, or `tang`
with the server URL), and `clevis-initramfs` unlocks it at boot. Keep a LUKS
recovery passphrase with the recovery kit, off the host. Neither unlock has been
tested on a platform node yet (TODO §37), and a rebuild follows the
[restore after losing the host](operations.md#restore-after-losing-the-host)
path. Applications holding personal data should request `longhorn-encrypted`
claims either way.

## Administrator second factor

A second factor is optional. Users add an authenticator app in the Keycloak
account console (**Account security → Signing in**). `./install.sh --admin-mfa
status` shows which `platform-admins` members have one; `--admin-mfa require`
asks the others to set one up at next sign-in (*Configure OTP* required action)
without changing the sign-in flow. Rerun it after adding administrators.

## Credential age

`./install.sh --credential-age` lists every secret in Vault's KV mounts with its
version and last write, oldest first, and exits non-zero when one is older than
`--max-age-days` (default 365). It reads metadata only. Rotate local
administrator passwords with
[the rotation helper](#rotate-local-administrator-passwords); application and
registry credentials rotate through their onboarding flows.

## Vulnerability triage

`TrivyNewCriticalVulnerability` fires when a running image gains critical
findings over its recent maximum, or a newly deployed image has any. Deadlines
run from first sighting in a running image: 15 days for Critical, 30 for High.
`./install.sh --vulnerability-report` lists items past deadline; the Grafana
**Platform Report** tracks monthly counts
([platform reports](guides/platform-reports.md)).

Record each accepted finding in
[`k8s/files/vulnerability-exceptions.yaml`](../k8s/files/vulnerability-exceptions.yaml):
one image repository and one CVE or GHSA ID, an owner, the triage date, an expiry
at most 92 days later and the reason. Until the end of its expiry day (UTC) the
finding is left out of `TrivyNewCriticalVulnerability`, the Platform Report's
counts and `--vulnerability-report` (shown with `--all`); it stays tracked from
first sighting, so its deadline resumes on expiry, and the report lists expired
exceptions. A finding triaged without an ID stays in the tables below only and
still counts: an image-wide exception would also hide new findings.

List critical findings by image, deduplicated across workloads (Trivy Operator
keeps one report per running digest):

```bash
kubectl get vulnerabilityreports -A -o json | jq -r '
  [.items[] | {image: (.report.artifact.repository + ":" + (.report.artifact.tag // "")),
    digest: .report.artifact.digest, crit: .report.summary.criticalCount}]
  | unique_by(.digest) | sort_by(-.crit) | .[] | select(.crit > 0) | "\(.crit) \(.image)"'
```

Before an upgrade, scan the candidate from a short-lived pod with small requests:
`trivy image --server http://trivy-service.infra:4954 --severity CRITICAL <image>`.

Triage of 2026-10-04 (critical only). Exceptions expire on 2026-11-04. Reachability
is assessed from the advisory and how the platform runs the component, not
exploit-tested.

| Image | Critical findings | Status |
| --- | --- | --- |
| Keycloak 26.7.4 | Netty SNI routing, FreeMarker path traversal, Bouncy Castle | Resolved: upgraded to 26.7.5 (0 critical, 7 high). |
| Homepage v2.4.0 (v2.7.0 since 2026-10-26, not rescanned) | Next.js `next/og` RCE, node-tar | Latest release; no fix yet. The server bundle has no `ImageResponse`/`next/og` code, so the RCE path is unreachable; behind Cloudflare Access and Keycloak. Exception. |
| Argo CD v3.5.3 | Go `crypto/tls` (CVE-2025-68121) | Latest 3.5 patch; no fix yet. Exception. |
| Elasticsearch, Kibana, Logstash 9.5.4 | Netty SNI routing; `piscina`; Bouncy Castle | Latest 9.5 patch. Netty SNI does not apply to the plain-HTTP in-cluster listeners. Exception. |
| Kafka UI v1.5.0 | Netty SNI routing | Latest release; plain HTTP behind Traefik. Exception. |
| SonarQube 26.9.0 Community | Netty SNI routing, Bouncy Castle | Latest Community release; plain HTTP behind Traefik. Exception. |
| GitLab CE 19.4.1 (19.4.3 since 2026-10-26, not rescanned) | `handlebars` 1.0.0 | Bundled asset, not a server-side template engine. Exception; recheck on 19.4.3. |
| GitLab Runner helper 19.4.1 (Alpine) | `miniperl` regular-expression handling | Fixed in Alpine `5.42.2-r1`; 19.4.1 is the latest helper. Job helpers do not run untrusted Perl. Exception. |
| Longhorn `csi-provisioner` v5.3.0 | Go `crypto/tls`, gRPC authz | Resolved: Longhorn 1.13.0 (2026-10-05) runs `csi-provisioner` v6.3.0. |
| MongoDB 7.0.43 (7.0.45 since 2026-10-26, not rescanned) | Go `crypto/tls` in bundled tools | Exception; 7.0.45 ships database tools 100.19.1, recheck it. |
| PostgreSQL 18.6, DBGate 7.3.1 (7.3.2 since 2026-10-26) (Debian bookworm) | Perl, zlib minizip, SQLite, libxml2 | No Debian fix. A newer Debian base changes glibc collation and needs a reindex plan. Exception. |

Debian bookworm status of the PostgreSQL and DBGate findings
([security tracker](https://security-tracker.debian.org/tracker/), 2026-10-04):

| CVE | Package | Debian status | Runtime use in these images |
| --- | --- | --- | --- |
| CVE-2023-45853 | zlib (minizip) | Ignored: minizip is not built into `zlib1g` | Not present |
| CVE-2026-42496 | Perl Archive::Tar | Postponed, minor | No Perl processes untrusted archives |
| CVE-2026-8376 | Perl regex compiler | No DSA, minor; point release | No Perl handles untrusted regular expressions |
| CVE-2026-13221 | Perl regex | Vulnerable per tracker; introduced in Perl 5.37.10, newer than bookworm's 5.36 | As above |
| CVE-2025-7458 | SQLite | No DSA, minor | PostgreSQL and DBGate do not use SQLite for data |
| CVE-2026-6653 | libxml2 | Postponed, minor (fixed only in 2.11) | PostgreSQL XML functions; reachable only by authenticated database clients (denial of service) |

On 2026-10-26 PostgreSQL moved to the tag's 2026-10-21 rebuild
(`sha256:bbf83594798e…`). Against the triaged build it changes only Debian
security updates: OpenSSL 3.0.22, Perl `5.36.0-7+deb12u4`, PCRE2 and `tzdata`.
The PostgreSQL packages and glibc are unchanged, so no reindex is needed. It has
not been rescanned: recheck these findings on the new digest before 2026-11-04.

### Exposed-secret findings

Triage of 2026-10-05. None is a usable credential; each is accepted until
2026-11-05.

| Image | Finding | Evidence |
| --- | --- | --- |
| GitLab CE 19.4.1 | SSH host keys in `/etc/ssh` | Build-time keys of the image (`root@buildkitsandbox`, ED25519 `SHA256:sIkAlv…`). At start GitLab replaces them with the installation's own keys from `/etc/gitlab`, which sshd uses (`HostKey /etc/gitlab/…`, ED25519 `SHA256:dpjMii…`, generated 2026-03-08), so the shipped keys are never served. |
| GitLab CE 19.4.1 | GitLab tokens in `config/gitleaks*.toml` and `diffblue_cover.rb` | Secret-detection rule examples and a masked form placeholder marked `gitleaks:allow`. |
| GitLab CE 19.4.1 | JWTs and private keys in `aws-sdk-core`, `chef`, `chef-zero` and `fog-aws` gems | Test fixtures and documentation examples in bundled gem sources. |
| PostgreSQL 18.6 (Debian) | `/etc/ssl/private/ssl-cert-snakeoil.key` | Debian's self-signed placeholder certificate; the platform's PostgreSQL does not enable TLS with it. |

## Administrator sign-in

[Keycloak reconciliation](../k8s/platform/keycloak-sso.yaml) keeps the
administrator login chosen at installation as managed identities in `master` and
`KEYCLOAK_REALM`. A username gets the email `<username>@<your-domain>`. The
`master` identity administers every realm at
`https://keycloak.<your-domain>/auth/admin/master/console/`.

`platform-admins` grants the access below, through native OIDC where supported
and the Keycloak proxy otherwise; administrative hosts also sit behind Cloudflare
Access ([hostnames](dns.md#cloudflare)). Give automation only the service
credentials it needs.

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

GitLab links SSO to its existing root account, keeping project ownership and
locally edited names. Kibana keeps individual profiles and disables managed
identities that leave `platform-admins`.

## Recovery credentials

Read credentials only in a private terminal; they serve recovery and automation,
not normal access. The managed SSO login is in `infra/keycloak-sso-credentials`
(`SSO_BOOTSTRAP_USERNAME`, `SSO_BOOTSTRAP_PASSWORD`):

```sh
kubectl get secret -n infra keycloak-sso-credentials \
  -o jsonpath='{.data.SSO_BOOTSTRAP_USERNAME}' | base64 -d
```

The same command works for these:

| Service | Namespace / Secret | Password key |
| --- | --- | --- |
| Grafana | `infra/grafana-admin-secret` | `GF_SECURITY_ADMIN_PASSWORD` |
| Keycloak bootstrap | `infra/keycloak-admin-secret` | `KC_BOOTSTRAP_ADMIN_PASSWORD` |
| Elasticsearch `admin` / `elastic` | `infra/elasticsearch-security-bootstrap` | `ADMIN_PASSWORD` / `ELASTIC_PASSWORD` |
| MongoDB | `infra/mongodb-secret` | `MONGO_INITDB_ROOT_PASSWORD` |
| PostgreSQL | `infra/postgres-secret` | `POSTGRES_PASSWORD` |
| Portainer | `infra/portainer-auth-secret` | `ADMIN_PASSWORD` |
| Odoo | `corp/odoo-secret` | `ODOO_ADMIN_PASSWORD` |

Username fields and Vault mappings are in the
[ExternalSecrets](../k8s/platform/vault.yaml) and the
[Odoo manifest](../k8s/corp/odoo.yaml). Vault's `secret` KV engine also holds:

| Service | Path | Field |
| --- | --- | --- |
| GitLab | `infra/gitlab` | `root_password` |
| SonarQube | `infra/sonarqube` | `admin_password`, `admin_token` |
| Longhorn origin login | `infra/platform-ui` | `password` |
| Website's Odoo lead intake | `apps/<website>/odoo` | `apiKey` (only creates leads; [guide](guides/odoo-website.md)) |
| Platform mail relay | `infra/smtp` | `password` (the relay login; [guide](guides/platform-mail.md#where-the-password-lives)) |

During an SSO outage, run `kubectl port-forward -n infra service/vault 8200:8200`
and open `http://127.0.0.1:8200/ui` with the root-only token in
`/var/lib/swirl-cloud/vault-bootstrap-token`; keep it out of arguments and logs.
Argo CD's local `admin` is disabled; DBGate admits only `platform-admins` through
its own Keycloak client.

## Recovery kit

`scripts/export-recovery-kit.sh` (or **Maintenance → Export the encrypted
recovery kit**) writes one file, encrypted with your passphrase (OpenSSL
AES-256-CBC, PBKDF2, 600 000 iterations), holding the Vault unseal key and root
token, the K3s server token and secrets-encryption state, and the restic password
and S3 credentials when off-node backups are set. The passphrase goes to OpenSSL
on stdin. The script test-decrypts the kit and records only SHA-256 fingerprints
in `/var/lib/swirl-cloud/recovery-kit.json`.

Move the kit and passphrase off the host (password manager, encrypted USB) and
check it with `scripts/export-recovery-kit.sh --verify FILE`, which fails when any
key changed since export. Export a new kit after rotating Vault, K3s or backup
credentials. With `--separate-archives`, nightly archives omit the Vault keys
while they match the kit; a rotated key is archived again, with a warning, until
a new kit exists. See the
[operator recovery checklist](operations.md#operator-recovery-checklist) and
[restore procedure](operations.md#restore-after-losing-the-host).

## Rotate local administrator passwords

The installer's rotation step, off by default, sets one password for the local
infrastructure administrators. Unattended `--env-file` installs enable it with
`ROTATE_LOCAL_ADMIN_PASSWORDS=true` and `LOCAL_ADMIN_PASSWORD` in their secret
environment. To run it later:

```bash
read -rsp 'Local administrator password: ' LOCAL_ADMIN_PASSWORD
printf '\n'
printf '%s\n' "$LOCAL_ADMIN_PASSWORD" | \
  scripts/rotate-local-admin-passwords.sh --password-stdin
unset LOCAL_ADMIN_PASSWORD
```

[The helper](../scripts/rotate-local-admin-passwords.sh) updates the services,
stores the password in Vault, refreshes External Secrets, restarts consumers and
checks availability. Application users, SSO identities, API tokens and ingress
Basic Auth have separate lifecycles.

## Vault Operations

Vault uses integrated Raft storage. The installer and Ansible render
[the shared Helm values](../config/vault-values.yaml), including the pinned
[image](operations.md#image-updates); changed values need a Helm reconciliation.
External Secrets reads Vault through Kubernetes auth
([vault.yaml](../k8s/platform/vault.yaml)).

### Storage and audit

The server has a read-only root filesystem, a small memory-backed `/tmp`, and
separate mounts for home, Raft data and audit logs. **Keep the image's file-owner
UID/GID when updating**; a change can stop Vault opening existing Raft and audit
files.

Keep both audit devices. Stdout goes to Fluent Bit; the file device writes to the
node-local `vault-audit` PVC, so startup does not depend on Longhorn. A tokenless
rotator sidecar renames the file, sends `SIGHUP` and compresses old files; limits
are in [the Helm values](../config/vault-values.yaml).

### Three-peer availability

The default is one server. `HIGH_AVAILABILITY_ENABLED=true` applies
[the HA overlay](../config/vault-ha-values.yaml): three Raft voters on three
control planes, a disruption budget of one, and a 5 GiB node-local audit claim
per pod; data stays on Longhorn. It needs three Ready, uncordoned control planes
(controller-only ones work). Replicas on one host give no host-failure
protection.

The chart uses `OnDelete`: Helm does not restart peers. Replace one peer at a
time and check quorum before and after each:

```sh
./scripts/configure-vault-ha.sh --verify
```

It checks three Ready peers on different nodes, three voters and healthy
Autopilot. Two peers keep serving through one host outage. Audit claims stay on
their host, so replacing a host needs an audit-volume recovery plan too.

#### Migrating an existing singleton

Use [the shared configurator](../scripts/configure-vault-ha.sh), not the overlay:
Kubernetes cannot add a claim template to a StatefulSet in place. Run it as one
maintenance operation from one control plane after verifying a restore backup,
and do any [image transition](guides/platform-migration.md) separately.
`VAULT_VALUES_FILE` is the rendered `config/vault-values.yaml`.

```sh
VAULT_HA_MIGRATE_EXISTING=true ./scripts/configure-vault-ha.sh \
  --namespace infra --values "$VAULT_VALUES_FILE"
HIGH_AVAILABILITY_ENABLED=true ./scripts/configure-vault.sh infra
```

The installer and Ansible also stop at the singleton layout until
`VAULT_HA_MIGRATE_EXISTING=true` is set. The helper saves and checksums a Raft
snapshot, archives the Helm values and StatefulSet in the root-only
`/var/lib/swirl-cloud/backups/`, orphans the controller (keeping `vault-0`
running), recreates it with per-pod audit claims, and replaces `vault-0` only
after both new peers are unsealed healthy voters. No PVC is deleted;
`data-vault-0` stays in use and the old `vault-audit` is kept as an archive.

If joining fails, leave the original pod and PVCs, fix the peers and rerun. If
controller creation failed, restore the archived controller first. **Never scale
a live three-voter cluster to one by changing only the Helm replica count.**

### Upgrade and recovery

1. Test the candidate on disposable data outside production: volume upgrade,
   secrets, access controls, auth, transit decryption, UI, audit rotation and
   restart. Prove a snapshot restore with the previous image.
2. Before the production update, save a fresh Raft snapshot, verify checksums
   and restore it into an isolated, network-disabled instance with the original
   unseal key and token. Compare secret hashes, policies, auth methods and audit
   devices with a private inventory; a snapshot command alone proves nothing.
3. After rollout, verify readiness, unseal state, data, mounts, auth methods,
   both audit devices, every ExternalSecret's Ready condition, the host unseal
   service and fresh Trivy reports.

Keep snapshots, keys, tokens, inventories and logs outside Git. Keep the verified
backup and previous image; never start an older binary on upgraded Raft data.

### Host unseal service

[swirl-cloud-vault-unseal.timer](../config/host/swirl-cloud-vault-unseal.timer)
runs the root-owned `swirl-cloud-vault-unseal.service`. Its
[helper](../scripts/vault-unseal.sh) checks each Vault pod's seal status and reads
`/var/lib/swirl-cloud/vault-unseal-key` (root, `0600`, never in Git or
diagnostics) only when needed. It unseals initialized peers, lets an empty
replacement join an unsealed survivor first, and never initializes a cluster.
Configuration refuses to initialize when recovery material exists but no
initialized peer answers.

In HA mode, after the first Vault configuration and after each enrollment,
distribute the recovery files and timer to every verified control plane:

```sh
./scripts/sync-vault-recovery.sh --all-control-planes \
  --node-network-cidr "$K3S_NODE_NETWORK_CIDR" \
  --control-plane-ip "$K3S_PRIVATE_ADDRESS" --ssh-user "$K3S_NODE_SSH_USER"
```

It needs private SSH and passwordless sudo
([control-plane administration](guides/node-enrollment.md#control-plane-administration)),
verifies roles, addresses and SSH endpoints, and sends the unseal key and token
over SSH stdin as root `0600` files in a `0700` directory. It never overwrites
differing material; workers get nothing. Until it runs, only the first control
plane has the timer.

The helper sends the key as JSON on `kubectl exec -i` stdin, never in arguments;
keep it that way ([unseal test](../tests/test-vault-unseal.py),
[HA tests](../tests/test-vault-ha.py)). Check the timer without showing
credentials:

```sh
sudo systemctl show swirl-cloud-vault-unseal.timer \
  -p ActiveState -p SubState -p UnitFileState -p LastTriggerUSec
sudo systemctl show swirl-cloud-vault-unseal.service \
  -p Result -p ExecMainStatus -p ExecMainExitTimestamp
```

Test real unsealing only in an isolated fixture, never by sealing the live
cluster. Credential lookup is in [recovery credentials](#recovery-credentials).

## Related Guides

- [Architecture](architecture.md)
- [Public DNS And Ingress](dns.md)
- [Operations](operations.md)
- [Data model](data-model.md)
- [Node enrollment](guides/node-enrollment.md)
- [Compliance evidence](guides/compliance-evidence.md)
