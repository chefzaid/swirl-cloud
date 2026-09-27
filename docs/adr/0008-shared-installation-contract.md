# ADR 0008: Share One Contract And Renderer Between Installer, Ansible And GitOps

- Status: Accepted
- Date: 2026-08-13
- Amended: 2026-09-10

## Context

The platform can be installed by an interactive shell script, unattended with
`--yes`, through Ansible, and then reconciled continuously by Argo CD. Separate
copies of versions, manifest lists or rendering logic drifted and produced
resources that differed between the installer and GitOps.

## Decision

- Keep ordered manifest inventories, pinned versions, wait lists and host
  inventories in one shell-safe properties file, [`config/platform.env`](../../config/platform.env),
  sourced by Bash and read by Ansible.
- Render native manifests and the Argo CD Helm chart from the same inputs with
  one renderer; installer-only HA resources are generated output, never source.
- Share Helm release operations (Longhorn, Vault, External Secrets, Argo CD) in
  [`reconcile-platform-release.sh`](../../scripts/reconcile-platform-release.sh)
  and ingress in `configure-ingress.sh`.
- Let each entry point choose selection and order, and automatically include
  dependencies (Odoo ⇒ platform services ⇒ data stores ⇒ Vault/ESO; Cloudflare ⇒ ingress).
- Validate installer/GitOps parity on every render profile.

## Rationale

One contract makes interactive, unattended, Ansible and GitOps paths produce the
same cluster. Parity checks catch drift before it reaches a live installation.

## Consequences

Adding a manifest, ExternalSecret or wait target requires updating
`platform.env`. The Argo CD Helm release is not self-managed; its upgrades run
through the installer, Ansible or the shared helper.
