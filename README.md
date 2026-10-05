# Swirl Cloud Platform

Build and operate your organization's private cloud on bare-metal infrastructure.
This K3s platform brings application delivery, identity, networking, storage,
databases, observability, security and Odoo together under your own domain.
Automated installation and GitOps keep the platform repeatable as it grows.

[![K3s](https://img.shields.io/badge/K3s-1.36-yellow.svg)](https://k3s.io/)
[![Argo CD](https://img.shields.io/badge/Argo%20CD-3.5-orange.svg)](https://argo-cd.readthedocs.io/)
[![Traefik](https://img.shields.io/badge/Traefik-3.7-blue.svg)](https://traefik.io/)
[![License](https://img.shields.io/badge/License-GPL%203.0-lightgrey.svg)](LICENSE)

Supply your company name during installation to present the portal as
**`<company name> Cloud`**. Company identity, domains and deployment settings are
configuration inputs; no organization is built into the platform.

## Get Started

On the first Ubuntu control-plane host, review the
[installation prerequisites](docs/deployment.md#installation-prerequisites), then run:

```bash
./install.sh
```

The setup hub checks the host, asks only what an express installation needs (or
every input under Advanced), tests your Cloudflare, GitHub and Tailscale tokens,
installs, and verifies the result. It also adds servers and imports
repositories. Saved answers drive unattended reruns with
`./install.sh --env-file ~/.config/swirl-cloud/install.env`, and
`./install.sh --status` verifies an installed platform. Open
`https://cloud.<your-domain>` for the service catalog. The
[deployment guide](docs/deployment.md) also covers unattended setup, Ansible,
reconciliation and application environments.

## How It Fits Together

Cloudflare routes public traffic to Traefik. K3s runs shared platform services in
`infra`, application workloads in `apps`, and centrally managed Odoo in `corp`.
Longhorn supplies persistent storage; Vault and External Secrets supply credentials.
One shared GitLab provides source, CI and the registry; one central Argo CD
reconciles deployments from Git. Run `int`, `uat` and `prod` in separate namespaces
on the same cluster, or give an environment its own application cluster. See the
[architecture overview](docs/architecture.md).

Application repositories own their manifests, pipelines and Argo CD Applications.
Use [`add-repos.sh`](docs/guides/repository-onboarding.md) to import and onboard
them; the [application contract](docs/guides/application-onboarding.md) defines the
integration. Adding nodes and enabling service availability are separate
operations: see [node enrollment](docs/guides/node-enrollment.md) and
[high availability](docs/guides/high-availability.md).

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

- [Node enrollment and private networking](./docs/guides/node-enrollment.md)
- [High availability](./docs/guides/high-availability.md), [PostgreSQL HA](./docs/guides/postgres-ha.md), [Kafka HA](./docs/guides/kafka-ha.md) and [node fencing](./docs/guides/node-fencing.md)
- [Platform migration to Traefik and upstream images](./docs/guides/platform-migration.md)
- [Repository onboarding](./docs/guides/repository-onboarding.md) and the [application onboarding contract](./docs/guides/application-onboarding.md)
- [Compliance evidence](./docs/guides/compliance-evidence.md) for NIS2, ISO/IEC 27001, DORA, GDPR and CIS
- [Cost showback](./docs/guides/cost-showback.md) per environment, namespace and workload

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
