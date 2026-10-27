# Development Guide

How to change platform source. Start from the [README](../README.md); the
[architecture](architecture.md) explains platform boundaries. Runtime resources
belong in their owning application repository; this repository centrally owns
only Odoo.

## Directory map

| Directory | Responsibility |
| --- | --- |
| `.github/workflows/` | GitHub/GitLab source synchronization. |
| `ansible/` | Inventory, the install/reconciliation playbooks and their run-record callback plugin. |
| `config/` | Version and service inventories in `platform.env`, Helm release values and the public origin CA certificate. |
| `config/application-cluster/` | Target firewall policy and temporary route/dependency probes used during registration. |
| `config/host/` | AppArmor, K3s API audit policy, Longhorn multipath settings and systemd backup/unseal units copied onto hosts. |
| `docs/` | Reader guides mirroring the application repositories; decisions in `docs/adr/`, long procedures in `docs/guides/`. |
| `k8s/` | The GitOps Helm chart and shared values. Argo CD uses `path: k8s`. |
| `k8s/base/` | Namespaces, cluster identity, DNS and baseline admission/network/host policy. |
| `k8s/datastores/` | Shared PostgreSQL, MongoDB, Redis and Kafka. |
| `k8s/platform/` | Delivery, identity, observability, ingress and other shared services, including support for application namespaces. |
| `k8s/corp/` | Centrally owned Odoo, gated by `appsEnabled`. External applications do not belong here. |
| `k8s/addons/` | The root Argo CD Application and the Descheduler, with its manual run Job kept apart from ordinary reconciliation. |
| `k8s/files/` | Programs and data embedded in chart ConfigMaps: Prometheus configuration and rules, Grafana dashboards, Dark Reader, Kafka startup, node fencing, the compliance control catalog and Odoo addons. A ConfigMap value of only `# swirl-cloud:file <path>` takes `k8s/files/<path>`. |
| `k8s/templates/` | Helm rendering and opt-in HA resources. |
| `k8s/charts/` | Pinned, vendored dependency charts for offline rendering and bootstrap. |
| `k8s/profiles/` | Two guards that stop installed root Applications still referencing retired image profiles. |
| `scripts/` | Installation, configuration, rendering, maintenance and validation commands. |
| `scripts/lib/` | Shared shell/Python libraries for those commands. |
| `tests/` | The deployment safety suites listed in the [validation policy](testing.md#validation-policy). |

Keep the layout shallow: use an existing directory before adding one, and group
related Kubernetes resources in one service manifest. The chart keeps Helm's
[standard chart structure](https://helm.sh/docs/topics/charts/#the-chart-file-structure)
and follows Kubernetes'
[configuration guidance](https://kubernetes.io/blog/2025/11/25/configuration-good-practices/).

## Toolchain

[Validation](testing.md#validation) lists the required tools; `--live` adds
`kubectl` and a cluster with the platform CRDs. Platform CI installs the same
tools on a digest-pinned Alpine image.

## Change the owning source

- **Defaults and installed Helm releases:** edit `config/platform.env` and the
  values file. `scripts/reconcile-platform-release.sh` owns shared Helm
  operations and `scripts/configure-ingress.sh` owns ingress; both installation
  entry points call them. Keep ordered inventories in `platform.env`.
- **Platform workloads:** edit the `k8s/` manifest or template. Shared
  application services stay present when Odoo is disabled. Installer and GitOps
  rendering must agree on resource identity and configuration.
- **Host setup:** change a helper in `scripts/` and its inputs in `config/host/`.
  Update the remote enrollment transfers when files copied to new nodes change.
- **Repository onboarding:** `add-repos.sh` collects inputs and calls
  `scripts/onboard-repositories.py`; `scripts/lib/` implements the app contract.
  `scripts/lib/application_delivery.py` reconciles project runners and their
  Kubernetes permissions from the environment inventory.
  `configure-repository-sync.sh` sets up ongoing sync for one repository as a
  separate internal step.
- **Image versions:** update the owning manifest and Helm values, then follow
  [image updates](operations.md#image-updates). There is no private image
  registry or profile selection.

Credentials, recovery state, scans, logs and build output stay outside Git. Do
not add copies of generated manifests or upstream source.

## Render Locally

Render the chart and installer manifests with synthetic or installed identity
values:

```bash
umask 077
scripts/render-cluster-config.sh --output "$(mktemp -d)/rendered" \
  --domain example.com --internal-domain internal.example.com \
  --gitops-repository https://gitlab.example.com/example/swirl-cloud.git \
  --cloudflare-access-team example
```

The output directory must be new or empty and outside the checkout; the
renderer refuses anything else so no stale manifests survive. Its `k8s/ha/`
directory is generated output for installer-only HA resources, not source.
Inspect the output, then discard it.

## Adding A Platform Capability

1. Write an [ADR](architecture.md#adr-process) when the change affects
   ownership, a runtime dependency, identity, secret flow, data durability or
   delivery.
2. Add resources to the owning manifest under `k8s/`. Digest-pin every image,
   declare `automountServiceAccountToken` and CPU/memory requests and limits, and
   follow the [probe policy](code-quality.md#manifest-and-deployment-policy).
3. Register new manifests, ExternalSecrets or wait targets in
   [`config/platform.env`](../config/platform.env) so the installer and Ansible
   apply and wait for them in order.
4. Add public hostnames to both the Ingress and `DEFAULT_CLOUDFLARE_HOST_LABELS`;
   the validator rejects a mismatch.
5. Seed credentials in [`configure-vault.sh`](../scripts/configure-vault.sh) and
   project them with an ExternalSecret; never put secrets in Helm values.
6. Update [features](features.md), the owning guide and [TODO.md](../TODO.md).
7. Run [validation](testing.md#quick-commands) and rehearse on a disposable host.

## Documentation Ownership

The docs tree mirrors Swirl Demo App, Indezy and Thoughty
([ADR 0001](adr/0001-documentation-structure.md)). Each guide owns one workflow
or reference: put a procedure or input table there and link to its heading from
elsewhere. Keep prerequisites beside the step that needs them, and link
configuration values to their source instead of copying inventories. Implemented
behavior goes in [features](features.md), planned work in [TODO.md](../TODO.md).
Audits, execution receipts and generated output stay outside Git.

## Related Guides

- [Architecture](architecture.md)
- [Testing](testing.md)
- [Code quality](code-quality.md)
- [Deployment](deployment.md)
