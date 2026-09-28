# ADR 0001: Mirror The Application Documentation Structure

- Status: Accepted
- Date: 2026-09-27

## Context

The platform documentation had grown to seventeen flat task guides under `docs/`
(installation, delivery, networking, observability, maintenance, vault, structure,
node enrollment, four HA and migration runbooks, and two onboarding references).
Each guide owned one workflow, but readers had no stable path by role, and
architectural rationale lived only in commit messages.

Swirl Demo App, Indezy and Thoughty already share a reader-oriented `docs/` structure with
an ADR log. Operators moving between the platform and those applications had to
learn two documentation shapes.

## Decision

Mirror the application repositories' hierarchy:

- `README.md` stays the short entry point with a documentation index and roadmap link
- `TODO.md` tracks implemented (`[x]`) and open (`[ ]`) platform work
- `docs/features.md`, `docs/architecture.md` (overview and ADR index),
  `docs/data-model.md`, `docs/development.md`, `docs/testing.md`,
  `docs/code-quality.md`, `docs/deployment.md`, `docs/operations.md`,
  `docs/security.md` and `docs/dns.md`
- `docs/adr/NNNN-short-title.md` for decisions, including superseded history

Long procedures with no application counterpart live in `docs/guides/`:
node enrollment, high availability, PostgreSQL HA, Kafka HA, node fencing,
platform migration, repository onboarding and the application onboarding contract.

Existing headings were kept verbatim when guides were merged, so anchors used by
application repositories (for example `operations.md#namespace-and-discovery`)
continue to resolve after their file paths are updated.

## Rationale

Different readers get the same path as in the application repositories:

- evaluators start with features
- architects start with architecture, ADRs and the data model
- contributors start with development, testing and code quality
- installers start with deployment
- operators start with operations and the guides
- reviewers start with security and DNS

Keeping `docs/guides/` separate avoids inflating the core guides with multi-page
migration procedures that only apply during maintenance windows.

## Consequences

Cross-links from scripts, manifests and application repositories must point to
the new paths. The repository validator checks that linked files exist.

Every technical change should update the owning guide, `features.md` or `TODO.md`,
and add an ADR when the change meets the [ADR process](../architecture.md#adr-process).

Decisions made before this ADR were reconstructed from the Git history
(2026-03-07 onward) and dated to the commit that introduced them.
