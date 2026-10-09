# Development Guide

Use this guide when changing platform source. The [README](../README.md) is the
entry point, [architecture](architecture.md) explains platform boundaries, and
each other guide owns one workflow. Keep runtime resources in their owning
application repository; this repository centrally owns Odoo.

## Directory map

| Directory | Responsibility |
| --- | --- |
| `.github/workflows/` | GitHub/GitLab source synchronization. |
| `ansible/` | Inventory and the installation/reconciliation playbooks. |
| `config/` | Shared version and service inventories in `platform.env`; Helm release values and the public origin CA certificate. |
| `config/application-cluster/` | Target firewall policy and temporary route/dependency probes used during registration. |
| `config/host/` | AppArmor, Longhorn multipath settings, and systemd backup/unseal units copied onto hosts. |
| `docs/` | Reader guides mirroring the application repositories; `docs/adr/` holds decisions and `docs/guides/` long procedures. |
| `k8s/` | The GitOps Helm chart, shared values. Argo CD continues to use `path: k8s`. |
| `k8s/base/` | Namespaces, cluster identity, DNS and baseline admission/network/host policy. |
| `k8s/datastores/` | Shared PostgreSQL, MongoDB, Redis and Kafka resources. |
| `k8s/platform/` | Delivery, identity, observability, ingress and other shared services, including support for application namespaces. |
| `k8s/corp/` | Centrally owned Odoo, gated by `appsEnabled`. External applications do not belong here. |
| `k8s/addons/` | The root Argo CD Application and optional/manual Descheduler resources. Keep manual Job creation separate from ordinary reconciliation. |
| `k8s/files/` | Runtime programs embedded in chart ConfigMaps: Kafka startup and node fencing. |
| `k8s/templates/` | Helm rendering and opt-in HA resources. |
| `k8s/charts/` | Pinned, vendored dependency charts used for offline rendering and bootstrap. |
| `k8s/profiles/` | Two small migration guards for installed root Applications that still reference retired image profiles. |
| `scripts/` | Installation, configuration, rendering, maintenance and validation commands. |
| `scripts/lib/` | Shared shell/Python libraries used by those commands. |
| `tests/` | The small set of deployment safety exceptions listed in the [validation policy](testing.md#validation-policy). |

Keep this layout shallow. Use the existing responsibility before adding a new
directory; group related Kubernetes resources in a service manifest. The chart
retains Helm's [standard chart structure](https://helm.sh/docs/topics/charts/#the-chart-file-structure).
The ownership boundaries and declarative source follow Kubernetes'
[configuration guidance](https://kubernetes.io/blog/2025/11/25/configuration-good-practices/).

## Toolchain

Offline validation requires Bash, Git, ShellCheck, Python 3 with PyYAML, Helm,
Ansible, Node.js, jq, SQLite's `sqlite3` CLI, `flock` and OpenSSL. Live validation adds
`kubectl` and access to a cluster with the platform CRDs. The platform CI job
installs the same tools on a digest-pinned Alpine image. Missing tools fail
validation instead of silently skipping checks.

## Change the owning source

- **Defaults and installed Helm releases:** edit `config/platform.env` and the
  relevant values file. `scripts/reconcile-platform-release.sh` owns shared Helm
  operations; `scripts/configure-ingress.sh` owns ingress. Both installation
  entry points call these helpers. Keep ordered inventories in `platform.env`.
- **Platform workloads:** edit the appropriate `k8s/` manifest or template.
  Shared application services remain present when Odoo is disabled. Installer
  and GitOps rendering must agree on resource identity and configuration.
- **Host setup:** change a helper in `scripts/` and its inputs in `config/host/`.
  Update remote enrollment transfers when changing files copied to new nodes.
- **Repository onboarding:** `add-repos.sh` collects operator inputs and calls
  `scripts/onboard-repositories.py`; `scripts/lib/` implements the app contract.
  `scripts/lib/application_delivery.py` reconciles project runners and their
  Kubernetes permissions from the registered environment inventory.
  `configure-repository-sync.sh` configures ongoing synchronization for one
  repository and remains a separate internal step.
- **Image versions:** update the owning manifest and Helm values;
  follow [image updates](operations.md#image-updates) when replacing a
  service with a compatible upstream release. No private platform image
  registry or profile selection is needed.

Render into a new or empty private temporary directory outside the checkout;
the renderer rejects reused output to prevent stale manifests. Its `k8s/ha/`
directory is generated output for installer-only HA resource delivery, not a
source directory. Credentials, recovery state, scans, logs and build output stay
outside Git. Do not add another copy of generated manifests or upstream source.

## Render Locally

Render the chart and installer manifests into a new private directory outside
the checkout, using synthetic or installed identity values:

```bash
umask 077
scripts/render-cluster-config.sh --output "$(mktemp -d)/rendered" \
  --domain example.com --internal-domain internal.example.com \
  --gitops-repository https://gitlab.example.com/example/swirl-cloud.git \
  --cloudflare-access-team example
```

Inspect the output, then discard it. Never commit rendered manifests,
credentials, journals or recovery files.

## Adding A Platform Capability

1. Record the decision in a new [ADR](architecture.md#adr-process) when it changes
   ownership, a runtime dependency, identity, secret flow, data durability or
   delivery.
2. Add resources to the owning manifest under `k8s/`, digest-pin every image,
   declare `automountServiceAccountToken` and CPU/memory requests and limits.
3. Register new manifests, ExternalSecrets or wait targets in
   [`config/platform.env`](../config/platform.env) so the installer and Ansible
   apply and wait for them in order.
4. Add public hostnames to both the Ingress and `DEFAULT_CLOUDFLARE_HOST_LABELS`;
   the validator rejects a mismatch.
5. Seed credentials in [`configure-vault.sh`](../scripts/configure-vault.sh) and
   project them with an ExternalSecret; never place secrets in Helm values.
6. Update [features](features.md), the owning guide and [TODO.md](../TODO.md).
7. Run [validation](testing.md#quick-commands) and rehearse on a disposable host.

## Documentation Ownership

The docs tree mirrors Swirl Demo App, Indezy and Thoughty; [ADR 0001](adr/0001-documentation-structure.md)
records the structure. The README is the entry point; each guide owns one
workflow or reference. Put a procedure or input table in that guide and link to
its heading from other pages. Keep prerequisites beside the step that needs
them. Link configuration values to their source rather than copying inventories
into several guides. Implemented behavior belongs in [features](features.md);
planned work belongs in [TODO.md](../TODO.md). Historical audits, execution
receipts and generated output stay outside Git.

## Related Guides

- [Architecture](architecture.md)
- [Testing](testing.md)
- [Code quality](code-quality.md)
- [Deployment](deployment.md)
