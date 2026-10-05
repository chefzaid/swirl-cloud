# ADR 0027: Native Admission Policy Packs, Audit First

- Status: Accepted
- Date: 2026-10-05

## Context

Governance rules for application workloads (pinned images, the internal
Registry, declared resources, ownership and data-classification labels) were
documented but not checked. Compliance evidence needs them checked
continuously, and customers need warnings before any rule starts rejecting
deployments. The platform already uses Kubernetes `ValidatingAdmissionPolicy`
and `MutatingAdmissionPolicy` with CEL for hostPath, gitRepo and system
workload hardening.

Kyverno would add policy reports, background scans and image-signature
verification, but also a controller, webhooks whose outage blocks admission,
its own CRDs and another component to upgrade and secure.

## Decision

- Write policy packs as native `ValidatingAdmissionPolicy` objects with CEL.
  No admission webhook is added.
- Bind new packs with `validationActions: [Warn, Audit]` first: requests are
  admitted, users see warnings, and failures are recorded as annotations in the
  Kubernetes API audit log. Move a binding to `[Deny]` per environment only
  after the policy report shows no violations there, starting with `prod`.
- Evaluate existing workloads with `scripts/policy-report.py`, which submits
  them as server-side dry runs and collects the policy warnings, so the rules
  exist once.
- Apply application packs to namespaces labelled
  `swirl-cloud.io/application-workloads: "true"`; platform namespaces keep
  their existing baselines.
- Revisit Kyverno, or an image-signature admission check, when signed
  application images are verified at admission (TODO §36).

## Consequences

Policies run inside the API server with no extra failure mode. Warnings reach
`kubectl` and Argo CD users immediately. There is no background scan
controller, so existing workloads are reported by the dry-run script rather
than by a stored policy report.
