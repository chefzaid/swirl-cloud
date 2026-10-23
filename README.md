# Swirl Cloud Platform

A private cloud for your organization on bare-metal servers: K3s with application
delivery, identity, storage, databases, observability, security and Odoo under your
own domain, installed by script and kept in sync with GitOps.

[![K3s](https://img.shields.io/badge/K3s-1.36-yellow.svg)](https://k3s.io/)
[![Argo CD](https://img.shields.io/badge/Argo%20CD-3.5-orange.svg)](https://argo-cd.readthedocs.io/)
[![Traefik](https://img.shields.io/badge/Traefik-3.7-blue.svg)](https://traefik.io/)
[![License](https://img.shields.io/badge/License-GPL%203.0-lightgrey.svg)](LICENSE)

The portal is titled **`<company name> Cloud`** from the name you enter at
installation; no organization, domain or deployment setting is built in.

## Get Started

On the first Ubuntu control-plane host, review the
[installation prerequisites](docs/deployment.md#installation-prerequisites), then run:

```bash
./install.sh
```

The setup hub checks the host, asks only what an express installation needs (or
every input under Advanced), tests your credentials, installs and verifies the
result. It also adds servers and imports repositories. Saved answers drive
unattended reruns with `./install.sh --env-file ~/.config/swirl-cloud/install.env`;
`./install.sh --status` verifies an installed platform. Open
`https://cloud.<your-domain>` for the service catalog. The
[deployment guide](docs/deployment.md) covers unattended setup, Ansible,
reconciliation and application environments.

## How It Fits Together

Cloudflare routes public traffic to Traefik. K3s runs shared platform services in
`infra`, application workloads in `apps`, and centrally managed Odoo in `corp`.
Longhorn supplies storage; Vault and External Secrets supply credentials. One
shared GitLab provides source, CI and the registry; one central Argo CD reconciles
deployments from Git. `int`, `uat` and `prod` run in separate namespaces on the same
cluster, or an environment gets its own application cluster. See the
[architecture overview](docs/architecture.md).

Application repositories own their manifests, pipelines and Argo CD Applications;
[`add-repos.sh`](docs/guides/repository-onboarding.md) imports and onboards them
under the [application contract](docs/guides/application-onboarding.md). Adding
nodes ([node enrollment](docs/guides/node-enrollment.md)) and enabling
[high availability](docs/guides/high-availability.md) are separate operations.

## Documentation

- [Features](./docs/features.md)
- [Architecture Overview and ADR Index](./docs/architecture.md)
- [Data Model Reference](./docs/data-model.md)
- [Development Guide](./docs/development.md)
- [Testing Guide](./docs/testing.md)
- [Code Quality](./docs/code-quality.md)
- [Deployment Guide](./docs/deployment.md)
- [Operations Runbook](./docs/operations.md)
- [Security Reference](./docs/security.md)
- [Public DNS And Ingress](./docs/dns.md)

Specialized procedures:

- [Node enrollment](./docs/guides/node-enrollment.md): adding servers and private networking
- [High availability](./docs/guides/high-availability.md): the multi-server HA profile
- [PostgreSQL HA](./docs/guides/postgres-ha.md): replicated PostgreSQL
- [Kafka HA](./docs/guides/kafka-ha.md): three-node Kafka quorum
- [Node fencing](./docs/guides/node-fencing.md): recovering single-writer services after a host failure
- [Platform migration](./docs/guides/platform-migration.md): move to Traefik and upstream images
- [Repository onboarding](./docs/guides/repository-onboarding.md): import and deploy repositories
- [Application onboarding](./docs/guides/application-onboarding.md): the application contract
- [Alert runbooks](./docs/guides/alert-runbooks.md): response for every platform alert
- [Service levels](./docs/guides/service-levels.md): availability objectives and outage watch
- [Capacity planning](./docs/guides/capacity-planning.md): headroom and what to add
- [Cost showback](./docs/guides/cost-showback.md): cost per environment and workload
- [Platform reports](./docs/guides/platform-reports.md): monthly availability, cost and compliance
- [Compliance evidence](./docs/guides/compliance-evidence.md): NIS2, ISO/IEC 27001, DORA, GDPR and CIS
- [Security detection](./docs/guides/security-detection.md): alerts on suspicious activity
- [Incident response](./docs/guides/incident-response.md): incident records and NIS2/GDPR deadlines
- [External services](./docs/guides/external-services.md): what leaves the platform
- [Platform mail](./docs/guides/platform-mail.md): one SMTP relay for platform email
- [Odoo website](./docs/guides/odoo-website.md): website leads and Live Chat
- [Releases](./docs/guides/releases.md): versioning, SBOM and signed bundles
- [September 2026 service upgrades](./docs/guides/service-upgrades-2026-09.md): upgrade record
- [October 2026 service upgrades](./docs/guides/service-upgrades-2026-10.md): upgrade record

## Roadmap

Implemented and open platform work is tracked in [TODO.md](./TODO.md). Decisions
and their history are recorded as [ADRs](./docs/architecture.md#accepted-adrs).

## Quick Checks

```bash
kubectl get nodes
kubectl get applications -n infra
./scripts/validate-repository.sh
./scripts/validate-repository.sh --live
```

See the [testing guide](./docs/testing.md) for the complete verification matrix.

## License

[GNU General Public License v3.0](LICENSE).
