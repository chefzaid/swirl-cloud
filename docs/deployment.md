# Deployment Guide

Install, reconcile and expand the shared platform, register application
environments, and maintain the shared GitLab CI and Argo CD delivery path. The
[architecture overview](architecture.md) explains the design; other procedures live
in the [specialized guides](architecture.md#specialized-guides).

## Infrastructure Layout

| Entry point | Owns |
| --- | --- |
| [`install-control-plane.sh`](../install-control-plane.sh) | Guided or unattended bootstrap of the first control plane and every selected component. |
| [`ansible/install.yml`](../ansible/install.yml) | The same installer through Ansible, with Vault-encrypted inputs. |
| [`ansible/deploy.yml`](../ansible/deploy.yml) | Idempotent reconciliation of an installed control plane. |
| [`add-node.sh`](../add-node.sh) | Control-plane and worker enrollment; see [node enrollment](guides/node-enrollment.md). |
| [`add-repos.sh`](../add-repos.sh) | Repository import, synchronization and first deployment; see [repository onboarding](guides/repository-onboarding.md). |
| [`scripts/install-application-cluster.sh`](../scripts/install-application-cluster.sh) | Optional separate application cluster for one environment. |
| [`k8s/`](../k8s/) | The GitOps Helm chart reconciled by the root Argo CD Application. |
| [`config/`](../config/) | Version inventory and Helm values for separately installed releases. |

## Installation Prerequisites

Run from this checkout on the first control-plane host as a non-root user with sudo
(passwordless for unattended runs). Size the host for the selected services'
[resource and volume requests](../k8s) plus builds, snapshots and backups. A missing
Helm is downloaded at `DEFAULT_HELM_VERSION` from
[`config/platform.env`](../config/platform.env) and installed only if its SHA-256
matches; update the version and both architecture checksums together.

### Support matrix

| Item | Supported | Notes |
| --- | --- | --- |
| Operating system | Ubuntu LTS; tested on Ubuntu 26.04 LTS | The setup hub warns on other distributions. |
| Architecture | x86_64 | GitLab Omnibus is amd64-only; aarch64 passes preflight but is untested. |
| Kubernetes | K3s at the version pinned in `config/platform.env` | Installed by the platform; existing clusters are not adopted. |
| Topology | One server, or three or more control planes with the HA profile | HA profiles are not yet drill-tested on three hosts. |

Sizes for one server running every platform service plus a few applications,
measured in October 2026 (platform services request about 5.4 cores and 16 GiB;
peak CPU use was 3 cores):

| Size | CPU | Memory | Disk |
| --- | --- | --- | --- |
| Minimum (preflight passes) | 8 cores | 24 GiB | 100 GiB free |
| Recommended | 16 cores | 64 GiB | 500 GB NVMe |
| Reference installation | 8 cores (requests at about 90 %) | 31 GiB | 145 GiB (67 % used) |

At the minimum, CPU requests leave little room for applications or rollouts; follow
[capacity planning](guides/capacity-planning.md) before adding workloads. Keep
30 GiB of free disk for backups and upgrades on top of data.

Prepare your public domain, an accessible GitOps repository and the
[administrator credentials](security.md#administrator-sign-in). For several nodes,
complete the [private transport prerequisites](guides/node-enrollment.md#private-node-network)
and [remote enrollment requirements](guides/node-enrollment.md#remote-enrollment).
Interactive setup guides account configuration before changing the firewall;
unattended runs need it done first.

## Choose an entry point

`./install.sh` without arguments opens the setup hub (`--lang fr` or
`SWIRL_CLOUD_LANG=fr` for French). In French, the installations it starts and its
status report are in French too; `SWIRL_CLOUD_LANG=fr ./install.sh --env-file FILE`
and `./install.sh --status --lang fr` do the same outside the hub. Messages without a
translation, JSON output and run records stay in English. `./install.sh --help`
lists every command, including the compliance and incident reports covered by the
specialized guides.

| Task | Command |
| --- | --- |
| Guided installation, servers, repositories, status | [`./install.sh`](#setup-hub-and-the-input-file) |
| Unattended install or rerun from a saved file | [`./install.sh --env-file FILE`](#setup-hub-and-the-input-file) or [Ansible installation](#ansible-installation) |
| Check a file without changing anything | `./install.sh --env-file FILE --validate-only` |
| Verify an installed platform | [`./install.sh --status`](#verify-the-result) |
| See what a rerun will change | [`./install.sh --plan [--env-file FILE]`](#setup-hub-and-the-input-file) |
| Collect troubleshooting data | [`./install.sh --support-bundle`](operations.md#cluster-status) |
| Reconcile an installed platform | [Ansible reconciliation](#platform-reconciliation) |
| Configure local or remote application environments | [Application deployment clusters](#application-deployment-clusters) |

The hub starts [`add-node.sh`](guides/node-enrollment.md) and
[`add-repos.sh`](guides/repository-onboarding.md), which also run on their own.
`install-control-plane.sh` is the engine: without arguments in a terminal it opens
the hub, `--prompts` asks plain terminal questions, and
[`--yes`](#unattended-installation) runs from the environment.

`INSTALL_SCOPE=apps` (default) also installs Odoo in `corp`; `infra` omits it. Both
provide the `apps` namespace foundation for
[application-owned deployments](guides/repository-onboarding.md).

Existing ingress-nginx or private-image installations must first follow the
[platform migration](guides/platform-migration.md). Ordinary reconciliation stops at
a read-only migration check; `platform_migration_approved=true` is only for a
prepared Ansible maintenance run. Retired image-profile inputs are rejected.

## Setup hub and the input file

`./install.sh` refuses to run as root, offers to apt-get install missing `python3`
and `whiptail`, and opens the hub. Answers go to `~/.config/swirl-cloud/install.env`
(`--output FILE` for another path outside the checkout). Nothing changes on the host
until you choose **Install**.

| Menu | What it does |
| --- | --- |
| Overview | What gets installed, and the state of this host and the input file. |
| Check this host | OS, architecture, sudo, CPU, memory, disk, ports, clock, K3s role, egress and domain resolution. |
| Configure: express | Company name and domain, GitOps URL, administrator login, Cloudflare token and Access emails for internet-exposed servers; optionally more servers, S3 backups and GitHub repositories. |
| Configure: advanced | Every input by section, asking only those that apply. |
| Test credentials | Cloudflare (token and zone), GitHub (token and repository admin access), Tailscale, and an SMTP login without sending. The installer checks OVHcloud and S3 keys. |
| Review / Save | Answers (secrets show as `(set)`) and problems; Save writes the file and runs `--validate-only`. |
| Plan | Each stage as installed, missing, drifted (chart or K3s version differs from `config/platform.env`, or identity differs from the file) or failed, the next action and the last result. Install shows it before confirming. |
| Install or update | Saves, rechecks the host, runs the engine with a private log under `~/.local/state/swirl-cloud/logs`, then shows the [verification report](#verify-the-result). |
| Add servers | Enrolls control planes or workers over SSH, or joins this machine to a cluster. |
| Import repositories | Imports and deploys GitHub repositories or existing GitLab projects. |
| Deploy an application | Per-environment revision and health, then a [deployment](#deploy-from-the-hub-or-the-command-line) or promotion. |
| Status / Service links | Verification report, and every service URL by namespace. |
| Maintenance | Recovery backup or Vault unseal now, API audit logging (restarts K3s), [local administrator password rotation](security.md#rotate-local-administrator-passwords), Lynis host audit, API dry-run, failed `swirl-cloud-*` units, earlier logs, support bundle, compliance and access-review reports, server cost, and recovery kit export or verification. |

Password fields can generate a strong password, shown once from a private file and
never passed as a command argument. Any secret can come from a protected file
instead: choose **Read it from a protected file**, or write `NAME_FILE=/path` (for
example `CLOUDFLARE_API_TOKEN_FILE=/secure/cloudflare`). The file holds one line,
belongs to you or root and has mode `600`, so the input file carries no credentials
and Vault is not needed during bootstrap or recovery. Hub logs show secrets as
`[redacted]`.

To keep secrets off the disk, pipe `NAME=value` lines into the engine. Stdin accepts
only secret inputs, and a secret also set in the file (or as `NAME_FILE`) is
rejected:

```bash
pass show swirl-cloud/install-secrets | \
  ./install.sh --env-file ~/.config/swirl-cloud/install.env --secrets-stdin
```

On an installed host the hub loads the installed identity as defaults; a rerun loads
the saved file, and an empty secret keeps the stored value. Unattended reruns pass
the same file to `./install.sh --env-file`.

The file is the only input source for that run (shell variables are ignored), is
parsed as data, never sourced, and must be mode `600`. Every missing, unknown or
invalid input and credential-policy failure is reported before anything changes;
`--validate-only` stops there. The [input schema](../scripts/lib/install_inputs.py)
defines names, types, defaults and conditions
([ADR 0026](adr/0026-installer-input-file.md)); version pins stay in
`config/platform.env`. Keep the file outside Git and remove or protect it after
installation.

## Topology and scheduling

Choose an odd final control-plane count (`1`, `3`, `5`, …) and a total count that
includes workers. Without workers, control planes must stay schedulable. See the
[topology and exposure model](architecture.md#node-topology) and
[scheduling and storage rules](guides/node-enrollment.md#scheduling-and-storage).
Extra hosts alone do not activate the [HA profile](guides/high-availability.md).

## Organization and installation identity

URLs, registry paths, authentication issuers, discovery and branding are rendered
from these inputs; the checkout stays generic. The portal is titled
`<company name> Cloud`; Keycloak's realm display name and Odoo's main company use
the company name alone.

| Environment input | Helm value | Default or requirement |
|---|---|---|
| `ORGANIZATION_NAME` | `organizationName` | Required company display name, spaces and punctuation allowed. |
| `ORGANIZATION_SLUG` | `organizationSlug` | First label of the public domain; a lowercase DNS label. |
| `PLATFORM_DOMAIN` | `publicDomain` | Required public base domain, such as `example.com`. |
| `INTERNAL_DNS_ZONE` | `internalDnsZone` | `internal.<PLATFORM_DOMAIN>`; must differ from the public domain. |
| `GITLAB_GROUP_PATH` | `gitlabGroupPath` | Organization slug; provisioned as a top-level group. |
| `GITLAB_GROUP_NAME` | `gitlabGroupName` | Organization display name. |
| `GITLAB_PROJECT_NAME` | `gitlabProjectName` | `swirl-cloud`; platform GitLab integrations follow it. |
| `KEYCLOAK_REALM` | `keycloakRealm` | Organization slug; `master` is reserved for Keycloak administration. |
| `TLS_SECRET_NAME` | `tlsSecretName` | Public domain with dots as hyphens, plus `-tls`. `CLOUDFLARE_TLS_SECRET_NAME` is an alias. |
| `SONAR_ALM_SETTING` | `sonarAlmSetting` | `<organization-slug>-gitlab`. |
| `CLOUDFLARE_ACCESS_IDP_NAME` | `cloudflareAccessIdpName` | `<organization-name> Keycloak`. |
| `CLOUDFLARE_ACCESS_TEAM_NAME` | `cloudflareAccessTeamName` | Existing Zero Trust team label, or `swirl-cloud-<domain-with-hyphens>` for a new team. |
| `GITOPS_REPOSITORY_URL` | `gitopsRepositoryURL` | Accessible HTTP(S) `.git` URL; interactive setup suggests the checkout's remote. |

The generated Argo CD Application carries these into later Helm reconciliation. The
public `infra/swirl-cloud-identity` ConfigMap records them for reruns, Ansible and
helpers; explicit inputs override it. Credentials follow the
[secret-management flow](security.md#recovery-credentials).

To rebrand, change `organizationName` in the `swirl-cloud` Application's Helm
parameters, reconcile, and pass the same `ORGANIZATION_NAME` to installer or Ansible
reruns. Update `GITLAB_GROUP_NAME` and `CLOUDFLARE_ACCESS_IDP_NAME` separately if
needed. Keep the domain, slug, SSO realm and repository paths unchanged.

An installation older than this identity contract must have its current realm,
GitLab group/project, TLS secret, organization and integration names set as explicit
Helm parameters on the `swirl-cloud` Application **before syncing the generalized
chart**, and passed to the first rerun. Changing a realm, domain or repository path
is a separate migration.

## Unattended installation

`--yes` selects the recommended bundle with `INSTALL_SCOPE=apps`. Set
`INSTALL_PREREQS`, `INSTALL_K3S`, `INSTALL_LONGHORN`, `INSTALL_INGRESS`,
`INSTALL_VAULT_STACK`, `DEPLOY_DATA_STORES`, `DEPLOY_PLATFORM_SERVICES`,
`INSTALL_DESCHEDULER` or `INSTALL_ARGOCD` to `true` or `false` to override a
default; unset components stay installed. Interactive runs skip inputs already set.
A local-exposure example:

```bash
export ORGANIZATION_NAME='Example Company'
export ORGANIZATION_SLUG=example
export PLATFORM_DOMAIN=example.com
export CONTROL_PLANE_NODE_NAME=control-plane-01
export SERVER_EXPOSURE=local
export INSTALL_SCOPE=apps
export CONTROL_PLANE_COUNT=1 CLUSTER_NODE_COUNT=1
export CONTROL_PLANE_SCHEDULABLE=true
export GITOPS_REPOSITORY_URL=https://github.com/example/swirl-cloud.git
export KEYCLOAK_SSO_BOOTSTRAP_USERNAME=platform-admin
read -rsp 'Platform administrator password: ' KEYCLOAK_SSO_BOOTSTRAP_PASSWORD
echo
export KEYCLOAK_SSO_BOOTSTRAP_PASSWORD
./install-control-plane.sh --yes
unset KEYCLOAK_SSO_BOOTSTRAP_PASSWORD
```

Besides the [identity inputs](#organization-and-installation-identity):

| Input | Meaning |
| --- | --- |
| `INSTALL_DEVELOPER_TOOLS` | `true` adds Java 21, Maven and Docker; default `false`. Ansible, storage clients, Node.js, ShellCheck and SQLite are always installed. |
| `SERVER_EXPOSURE` | `local` or `internet`; `internet` enables the public host policy and defaults to Cloudflare configuration. |
| `CONTROL_PLANE_NODE_NAME` | Local Kubernetes node name; defaults to the lowercase host name. |
| `CONTROL_PLANE_COUNT`, `CLUSTER_NODE_COUNT` | Final counts, including registered nodes; the control-plane count must be odd. |
| `CONTROL_PLANE_SCHEDULABLE` | `false` applies `NoSchedule` to control planes once workers are Ready. Defaults to `true` without workers, otherwise `false`. |
| `K3S_NODE_TRANSPORT` | `vrack`, `tailscale` or `lan` for multi-node enrollment. |
| `K3S_CONTROL_PLANE_IPS`, `K3S_WORKER_IPS` | Comma-separated prepared vRack or LAN addresses, excluding the first host. |
| `K3S_CONTROL_PLANE_HOSTS`, `K3S_WORKER_HOSTS` | Comma-separated Tailscale bootstrap SSH targets such as `admin@host`. |
| `K3S_CONTROL_PLANE_SSH_USER`, `K3S_WORKER_SSH_USER` | SSH users for address-based enrollment. |
| `K3S_API_VIP` | vRack or LAN with several control planes: an unused node-network address for the [Kubernetes API endpoint](guides/node-enrollment.md#kubernetes-api-endpoint). Tailscale uses a Tailscale Service. |
| `CLOUDFLARE_NODE_DNS_LABEL` | Public administration label, independent of the node name; default `node-01`. |
| `CLOUDFLARE_PUBLISH_APEX` | Default `false`; opt in only if the platform should manage apex DNS. |

A Tailscale example, with identity, credentials and the
[Tailscale inputs](guides/node-enrollment.md#tailscale) set:

```bash
CONTROL_PLANE_COUNT=3 CLUSTER_NODE_COUNT=5 CONTROL_PLANE_SCHEDULABLE=false \
K3S_NODE_TRANSPORT=tailscale \
K3S_CONTROL_PLANE_HOSTS='admin@cp-02,admin@cp-03' \
K3S_WORKER_HOSTS='admin@worker-01,admin@worker-02' \
  ./install-control-plane.sh --yes
```

When retrying with a partial host list, set both final counts. Registered nodes
count even when NotReady; completion needs every planned node Ready. Reruns keep the
installed K3s version. Expand later with [node enrollment](guides/node-enrollment.md).

### Optional integrations

Both entry points take the same inputs. Keep secrets in hidden prompts, the process
environment or Ansible Vault; never commit credentials or generated configuration.

- **Public DNS, TLS and Access:** see [Cloudflare setup](dns.md#cloudflare).
  `CONFIGURE_CLOUDFLARE=false` uses local TLS and keeps existing TLS secrets.
- **Encrypted offsite backups:** `CONFIGURE_OFFSITE_BACKUPS=true` with
  `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_REGION`,
  `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY` and `BACKUP_REPOSITORY_PASSWORD`
  (16+ characters; keep a copy outside the cluster). See
  [backup and recovery](operations.md#backups-and-recovery).
- **Platform mail:** `CONFIGURE_PLATFORM_MAIL=true` with `PLATFORM_MAIL_HOST`,
  `PLATFORM_MAIL_USERNAME` and `PLATFORM_MAIL_PASSWORD`; the rest has defaults. See
  [platform mail](guides/platform-mail.md).
- **Repository import and deployment:** `CONFIGURE_REPOSITORY_SYNC=true` with the
  [onboarding automation inputs](guides/repository-onboarding.md#automation-and-reruns),
  or `REPOSITORY_SOURCE=gitlab` with `GITLAB_PROJECTS` for
  [projects already in GitLab](guides/repository-onboarding.md#projects-without-github).
  The installer runs `add-repos.sh` (with `--yes` when unattended) once the platform
  and Argo CD are ready. Selecting deployment authorizes the commits and service
  changes described in that guide.

## Ansible

Both playbooks run locally with the supplied `localhost` inventory; extra inventory
hosts do not distribute installation (enrollment uses SSH). Run bootstrap and default
reconciliation on the first control plane; an HA platform can be reconciled from any
surviving control plane. The playbooks share
[platform defaults](../config/platform.env), manifest inventories and helpers such as
[`reconcile-platform-release.sh`](../scripts/reconcile-platform-release.sh) with the
shell installer. Vault readiness follows resource application and precedes secrets
bootstrap.

### Ansible installation

```bash
sudo apt-get update
sudo apt-get install -y ansible git python3
```

Export the [unattended inputs](#unattended-installation), then run:

```bash
ansible-playbook -i ansible/inventory ansible/install.yml
```

To override inputs from an Ansible Vault encrypted extra-vars file, put them in an
`installer_environment` mapping:

```yaml
installer_environment:
  ORGANIZATION_NAME: Example Company
  ORGANIZATION_SLUG: example
  PLATFORM_DOMAIN: example.com
  CONTROL_PLANE_NODE_NAME: control-plane-01
  SERVER_EXPOSURE: local
```

```bash
ansible-playbook -i ansible/inventory ansible/install.yml \
  -e @/secure/cluster-install.yml --ask-vault-pass
```

`-e installer_env_file=/secure/install.env` uses an
[input file](#setup-hub-and-the-input-file) instead of `installer_environment` and
the process environment. The task uses `no_log`; on failure, rerun the shell
installer with the same inputs to see diagnostics.

### Platform reconciliation

Run on an installed control plane with its kubeconfig, kubectl, Helm, jq, OpenSSL,
Git, Python and Ansible. Preflight checks the local K3s service, control-plane role
and kubeconfig before host changes. Workers are rejected; joined private control
planes are accepted when HA is requested or recorded. Supply
`KEYCLOAK_SSO_BOOTSTRAP_USERNAME` and `KEYCLOAK_SSO_BOOTSTRAP_PASSWORD` in the
environment when reconciling platform services:

```bash
ansible-playbook -i ansible/inventory ansible/deploy.yml
```

- Installed [identity settings](#organization-and-installation-identity) are reused
  unless overridden. Set `CONTROL_PLANE_NODE_NAME` if it differs from the hostname.
- Exposure defaults to `internet`; pass `-e server_exposure=local` for a locally
  exposed bootstrap host. Joined servers keep their private exposure.
- Scheduling is kept unless `CONTROL_PLANE_SCHEDULABLE=true|false` changes it.
  [Private SSH access](guides/node-enrollment.md#control-plane-administration) and
  [storage placement](guides/node-enrollment.md#scheduling-and-storage) are reconciled.
- To activate HA, first finish the PostgreSQL, Kafka and Vault migrations in
  [activation order](guides/high-availability.md#activate-in-order), then run with
  `HIGH_AVAILABILITY_ENABLED=true`, `PLATFORM_HA_VALUES_FILE` and
  `-e configure_cloudflare=true`. Later runs keep recorded HA and reject unfinished
  migrations or implicit downgrades.

Switches pull in their dependencies (Odoo → platform services → data stores →
Vault/External Secrets; Cloudflare → ingress). HA forces Longhorn, Cloudflare,
platform services and Argo CD on.

| Switch | Default |
| --- | --- |
| `install_longhorn`, `install_ingress`, `install_vault_stack` | `true` |
| `deploy_data_stores`, `deploy_platform_services` | `true` |
| `install_apps`, `install_odoo` | `true` |
| `install_descheduler`, `install_argocd`, `manage_host_security` | `true` |
| `configure_cloudflare`, `manage_private_transport` | `false` |

`-e install_apps=false` omits Odoo but keeps the `apps` foundation. Disabling a
switch uninstalls nothing; Argo CD keeps reconciling its scope.

### Optional host and account changes

Private transport reconciliation is opt-in and runs before K3s network binding and
UFW. Export the [transport inputs](guides/node-enrollment.md#private-node-network)
and name the existing transport:

```bash
# Already attached and addressed vRack:
ansible-playbook -i ansible/inventory ansible/deploy.yml \
  -e manage_private_transport=true -e k3s_node_transport=vrack

# Tailscale, with its API token supplied in the environment:
ansible-playbook -i ansible/inventory ansible/deploy.yml \
  -e manage_private_transport=true -e k3s_node_transport=tailscale
```

For API-managed vRack attachment, add `-e ovh_vrack_automate_account=true` and the
OVH account inputs. Without transport reconciliation, set `K3S_NODE_NETWORK_CIDR`
when host security must trust node traffic.

Cloudflare needs its [integration inputs](#optional-integrations) and
`-e configure_cloudflare=true`; offsite recovery and repository import take the same
inputs as installation. Repository setup uses `no_log` and stops the play on
failure; run `add-repos.sh` with the same inputs and state directory to diagnose.

To align [local administrator passwords](security.md#rotate-local-administrator-passwords)
(SSO identities and application users do not change):

```bash
read -rsp 'Local administrator password: ' LOCAL_ADMIN_PASSWORD
echo
export LOCAL_ADMIN_PASSWORD ROTATE_LOCAL_ADMIN_PASSWORDS=true
ansible-playbook -i ansible/inventory ansible/deploy.yml
unset LOCAL_ADMIN_PASSWORD ROTATE_LOCAL_ADMIN_PASSWORDS
```

## Application deployment clusters

`int`, `uat` and `prod` run in separate namespaces on the platform cluster, or an
environment gets its own application cluster. GitLab, Argo CD, Vault, the registry,
Keycloak and data services stay shared. Copy
[`deployment-environments.example.yaml`](../config/deployment-environments.example.yaml)
outside the checkout and set your domain, ingress address and pod CIDR.
`mode: local` reuses the platform cluster; `mode: remote` needs a separate cluster
and Tailscale.

| Environment | Default local namespace | Example application hostname |
| --- | --- | --- |
| `int` | `apps-int` | `demo-int.example.com` |
| `uat` | `apps-uat` | `demo-uat.example.com` |
| `prod` | `apps` | `demo.example.com` |

- Local `int` and `uat` each get a namespace, restricted Argo project, Vault store,
  network rules and resource quota (set `resourceQuota` in the entry to fit capacity).
- Local `prod` shares `apps` with single-environment applications: it gets its own
  Argo project and Vault store, but quotas, network and admission rules stay
  platform-owned, and each application ships its own ingress NetworkPolicy.
- Environments on one cluster share its outages, upgrades and resources.

`platform.hostnameStyle: suffix` gives the hostnames above, covered by the
base-domain wildcard; application ingress uses the shared Traefik certificate in
`infra`, whose key branch workloads never receive. `nested` (the default when
omitted) gives `demo.int.example.com` and `demo.uat.example.com`.

### Register an environment

Prepare [shared data authentication](guides/application-onboarding.md#shared-application-data)
first, and keep administrator kubeconfigs, Vault tokens and TLS keys outside Git.
From the platform administration host:

```bash
python3 scripts/configure-deployment-environments.py \
  --config /secure/deployment-environments.yaml --environment int \
  --platform-kubeconfig "$HOME/.kube/config" \
  --vault-token-file /var/lib/swirl-cloud/vault-bootstrap-token
```

Repeat for `uat` and `prod`. Supply `CLOUDFLARE_API_TOKEN` for origin certificates,
or `--tls-cert` and `--tls-key` for one covering the environment domain and its
wildcard; valid existing certificates are reused. A remote target's Traefik repeats
the zone's [HSTS policy](dns.md#ingress-controller) from `CLOUDFLARE_ENABLE_HSTS`
and `CLOUDFLARE_HSTS_MAX_AGE`, or the platform's deployed value when both are unset.
Registration verifies cluster identity, scoped API and Vault access, data services
and TLS before publishing the target in `infra/deployment-environments`.

Setting `DEPLOYMENT_ENVIRONMENTS_FILE` for the installer registers the local entries
after platform setup, before repository onboarding. Register remote entries
separately with their target kubeconfig.

### Optional separate application cluster

Start from
[`deployment-environments.remote.example.yaml`](../config/deployment-environments.remote.example.yaml).
A remote entry needs a unique `clusterName`, an HTTPS `server`, `ingressAddress`,
`podCIDR` and disjoint Tailscale `nodeCIDRs`; `namespace` defaults to `apps`. Point
`platform.gateway` and the shared service endpoints at the protected Tailscale
gateway (validated by the [inventory schema](../scripts/lib/deployment_environments.py)).
In a mixed inventory, local entries need a full `services` override. Kafka broker
advertisements must be reachable from every selected environment.

On each Ubuntu target, install `python3-yaml`, `curl`, `jq`, `openssl`, `nftables`
and Tailscale, then run as a sudo-capable user (never on the platform host):

```bash
./scripts/install-application-cluster.sh \
  --config /secure/deployment-environments.yaml --environment prod
```

It installs pinned K3s and its network guard, without platform services, and
refuses to convert an unrelated cluster. Copy the generated administrator kubeconfig
securely, then register with `--environment prod --target-kubeconfig
/secure/apps-prod.yaml`; registration installs the ingress and secret controllers
and verifies Tailscale routes and the gateway. See
[application node enrollment](guides/node-enrollment.md#application-cluster-nodes)
and [observability](operations.md#namespace-and-discovery) for remote collection.

### Publication and maintenance

Only registered targets are deployable. Run
[repository onboarding](guides/repository-onboarding.md#environment-deployment) to
prepare data, identity, DNS and CI access, then pick the environment in GitLab.
Reruns keep cluster/namespace identities; changing a binding is an explicit
migration. `--rotate-argocd-token` verifies a replacement operator credential before
revoking the old one. Local registration never replaces Argo CD's built-in cluster
credentials.

Each application hostname needs active Cloudflare edge coverage. Suffix names are
covered by `*.example.com`. Nested names need Advanced/Custom coverage prepared in
advance: the [DNS helper](../scripts/configure-application-dns.py) checks coverage
before publishing, and Total TLS needs DNS first. Certificate preparation moves no
production traffic; keep the production database and verify the replacement before
cutover.

### Prepare application CI before targets exist

Version 2 onboarding configures the [scoped project runners](#application-delivery)
itself. For an existing project whose targets are not ready, protect its default
branch, let active jobs finish, and run from the platform administration host:

```bash
python3 scripts/configure-application-delivery.py \
  --application swirl-demo-app --project team/swirl-demo-app --checkout /path/to/swirl-demo-app
```

It creates and revokes a temporary GitLab administrator token (or uses
`GITLAB_ADMIN_TOKEN`). With no registered targets, jobs build but cannot write
Applications. Runner credentials stay in a central Secret; job caches are ephemeral.
Rerun onboarding after registering targets.

## Delivery Flow

The platform supplies GitLab, an instance runner, registries and Argo CD;
application repositories own their CI, runtime manifests and Applications. The
[architecture overview](architecture.md) shows the flow.

### Ownership and bootstrap

The [root Application](../k8s/addons/swirl-cloud-application.yaml) reconciles the
platform chart, including Odoo when enabled; each application has its own
Application and deployment guide. The installer and [Ansible](#ansible) run
[configure-gitlab-ci.sh](../scripts/configure-gitlab-ci.sh) to reconcile the group
and project, Dependency Proxy, retention, instance runner and Vault-backed tokens,
using a temporary administrator token created through `gitlab-rails` and revoked on
exit.

### Pipelines and outputs

#### Application delivery

Swirl Demo App uses one GitLab project and registry for all environments. In
**Build → Pipelines → New pipeline**, set `PIPELINE_MODE=full` and the target:

- `int`: any branch builds and deploys a snapshot (`-SNAPSHOT` versions included);
  all branches share the integration hostname.
- `uat` or `prod`: the default branch publishes a stable release, or set
  `RELEASE_VERSION` to promote a finalized release without rebuilding.

Releases can also go to `int`; snapshots never reach `uat` or `prod`. Older feature
branches must merge or rebase the updated CI helpers first.

- Each Application (`swirl-demo-app-int`, `-uat`, `-prod`) has its own restricted
  AppProject, cluster/namespace pair and immutable runtime configuration commit, so
  deploying `int` never moves the production pointer.
- CI reads only the public `infra/deployment-environments` ConfigMap and rejects
  missing targets or a changed cluster binding. It verifies destination, revision,
  health, image digests and public smoke checks, and never patches workloads.
- Release publication takes one lock; deployments lock per environment and reject a
  stale branch. Snapshot GitOps commits go to `gitops/int/<pipeline-id>` and leave
  the source branches and version counter unchanged.
- Version 2 onboarding gives each project its own integration and protected-release
  runners and disables shared runners for it. Branch jobs update only their
  integration Application; release jobs update their registered environments, and
  admission rules bind Applications to their destinations. Version 1 applications
  keep their runners and delivery contract until migrated.

See the app's deployment guide for job details and the
[scanner contract](operations.md#source-analysis) for analysis-only pipelines.
Remote clusters need their own metrics/log collector.

### Deploy from the hub or the command line

`./install.sh` → **Deploy an application**, or:

```bash
scripts/deploy-application.py list
scripts/deploy-application.py deploy --application swirl-demo-app --environment int [--ref BRANCH]
scripts/deploy-application.py deploy --application swirl-demo-app --environment prod [--release-version 1.2.3]
```

It lists Argo CD Applications labelled `swirl-cloud/environment` whose environment
is registered, and starts the pipeline (`PIPELINE_MODE=full`,
`DEPLOYMENT_ENVIRONMENT`, and `RELEASE_VERSION` when promoting) through a private
port-forward with a one-day administrator token it revokes afterwards (or
`GITLAB_ADMIN_TOKEN`). `uat` and `prod` deploy only from the default branch.
`--yes` skips the start confirmation, and `--timeout` sets the pipeline limit in
minutes (default 60). Manual release gates run only after you confirm or with
`--approve-manual`; otherwise it exits with code 2 and the gate's link. It succeeds
only when the Application is `Synced` and `Healthy` at a new revision, then prints
the application URLs.

#### Infrastructure reconciliation and verification

The [platform pipeline](../.gitlab-ci.yml) and Argo CD act independently:

| Path | Responsibility |
| --- | --- |
| Argo CD | Reconcile `main` into platform resources with automatic sync, prune and self-heal. |
| CI | Run [repository validation](testing.md#validation); on the default branch, verify the same commit is Synced/Healthy and check delivery services and Registry push/read. |

CI observes reconciliation; it does not gate Argo's sync.

### Registry and dependencies

Public names, internal CI/containerd routes and the Dependency Proxy follow the
[service DNS and registry routing contract](dns.md#service-dns-and-registry-routing);
these machine endpoints must work without browser authentication or challenges. The
infrastructure pipeline pins Alpine by digest (`IfNotPresent` reuses the node copy).
Maven/npm use public upstreams and the persistent
[runner cache](../k8s/platform/gitlab-runner.yaml); app builds may use disposable
Kaniko layers. A cold pipeline must still build, test and release.
[Operations](operations.md#gitlab-storage) owns Registry/package retention and
[observability](operations.md#observability) covers delivery metrics and logs.

## Argo CD operations

The installer and `ansible/deploy.yml` install Argo CD with Helm from the rendered
[`config/argocd-values.yaml`](../config/argocd-values.yaml) and the pins in
`config/platform.env`. The root Application does not upgrade its own Argo CD
release; apply Helm changes through the installer, Ansible or a Helm upgrade with
the same rendered inputs.

Bootstrap Jobs such as `elasticsearch-security-bootstrap` and
`keycloak-sso-reconcile` are sync hooks. Automated sync starts only when a regular
resource differs from Git, so a commit that changes only a hook Job does not run it.
Sync the current revision (Argo CD **Sync**, or
`kubectl -n infra patch application swirl-cloud --type merge -p '{"operation":{"sync":{}}}'`).

With `HIGH_AVAILABILITY_ENABLED=true`,
[`config/argocd-ha-values.yaml`](../config/argocd-ha-values.yaml) runs API and
repository servers and ApplicationSet controllers in pairs on separate hosts, and
the Redis cache as three authenticated Sentinel peers behind two HAProxy endpoints,
with disruption budgets. The application controller stays a single process: upstream
[dynamic cluster distribution](https://argo-cd.readthedocs.io/en/stable/operator-manual/dynamic-cluster-distribution/)
(alpha; recheck on upgrades) runs it as a Deployment with 30-second
not-ready/unreachable tolerations, so it is replaced on a surviving host. There is
no standby; reconciliation pauses while it restarts, and running applications are
unaffected.

The controller's `GOMEMLIMIT` is a soft limit below the container memory limit;
change both together in `config/argocd-values.yaml` (upstream
[memory guidance](https://argo-cd.readthedocs.io/en/stable/operator-manual/high_availability/#mitigating-oomkilled-events-from-memory-spikes)).
After a Helm change, wait for the rollout, check Applications return to `Synced` and
`Healthy`, confirm `go_gc_gomemlimit_bytes`, and watch restarts, memory, GC, CPU and
reconciliation time.

## After installation

Two steps need a person:

- Export the encrypted recovery kit, store it and its passphrase off the host, and
  verify it ([operator recovery checklist](operations.md#operator-recovery-checklist)).
  Without it, losing the host loses the Vault keys and the off-node backup password.
- Open `./install.sh` in a terminal once and check that its screens render.

## Verify the result

The installer finishes with [`scripts/verify-platform.py`](../scripts/verify-platform.py)
and exits nonzero when a check fails. Run the same read-only report any time:

```bash
./install.sh --status                 # --strict fails on warnings, --json for tooling
./install.sh --status --api-dry-run   # also validate rendered profiles against the API server
```

`--skip-endpoints` skips the HTTPS probes; `--backup-max-age-hours` sets the backup
limit (default 26).

| Check | Fails when | Warns when |
| --- | --- | --- |
| Nodes | any node is not `Ready` | |
| Workloads | a pod older than five minutes is not ready (failed Job pods are left to their Jobs) | a newer pod is still starting |
| ExternalSecrets | any is not `Ready` | |
| Argo CD | an Application is not `Synced` and `Healthy` | a synced Application is `Progressing` |
| Vault | a Vault pod is sealed or none is running | Vault is not installed |
| Longhorn | a volume is `faulted` | a volume is `degraded`, or volumes are unreadable |
| TLS secrets | a certificate in `infra`, `apps` or `corp` has expired | it expires within 30 days |
| Platform mail | | [platform mail](guides/platform-mail.md) is off or not seeded, so alerts reach only GitLab |
| Public endpoints | an Ingress host answers 5xx or fails TLS | DNS does not resolve yet or the request times out |
| Disk headroom | the K3s filesystem is 85 % used (kubelet image GC) | less than 30 GiB is free for maintenance |
| Host firewall | UFW is inactive or IPv4/IPv6 INPUT is not default-deny | UFW cannot be read without passwordless sudo |
| API audit log | | auditing is configured but not yet active, or not configured |
| Kubelet memory reservation | | the reservation is missing or waits for a K3s restart |
| Nightly backup | the last success is older than 26 hours, or the last run failed | no success metric yet, or the timer is not installed |
| Last installation | | the latest installer or playbook run failed, was interrupted or recorded no finish (shown with its last step) |

Each warning or failure ends with a `Next:` line naming the first command to run.
Every installer run that reaches the pre-flight checks writes a record to
`/var/lib/swirl-cloud/install-runs/` (the newest 50 are kept): mode, Git revision,
chart and K3s versions, each step with its time, and the result with its exit code
and duration. Records hold no inputs. `ansible/install.yml` and `ansible/deploy.yml`
runs write the same record (mode `ansible:<playbook>`, one step per task) through
the [callback plugin](../ansible/callback_plugins/swirl_cloud_record.py) beside the
playbooks, using passwordless sudo when the directory is root's; `--check` and
`--syntax-check` runs are not recorded. Hosts installed before records existed show
no **Last installation** line.
Redirects and `401`/`403` count as reachable: administration hosts sit behind
Cloudflare Access and SSO. Then open `https://cloud.<your-domain>`.

For source changes and Ansible syntax checks:

```bash
ansible-playbook -i ansible/inventory --syntax-check ansible/install.yml
ansible-playbook -i ansible/inventory --syntax-check ansible/deploy.yml
./scripts/validate-repository.sh
```

`validate-repository.sh --live` adds a Kubernetes server-side dry-run without
mutation. `install.yml --check` skips installation; `deploy.yml` rejects check
mode. Neither replaces a fresh installation on disposable hosts; see
[validation coverage](testing.md#validation).

## Related Guides

[Architecture](architecture.md), [Public DNS And Ingress](dns.md),
[Operations](operations.md), [Security](security.md), [Testing](testing.md),
[node enrollment](guides/node-enrollment.md),
[high availability](guides/high-availability.md) and
[repository onboarding](guides/repository-onboarding.md).
