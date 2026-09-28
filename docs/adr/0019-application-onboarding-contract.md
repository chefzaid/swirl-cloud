# ADR 0019: Onboard Applications Through A Declarative Repository Contract

- Status: Accepted
- Date: 2026-09-10
- Amended: 2026-09-24

## Context

Each application needed repository import from GitHub, two-way synchronization,
registry credentials, Vault paths, Keycloak clients, DNS and a first release.
Doing this by hand was error-prone, and adding an application inventory to this
repository would couple the platform to every app.

## Decision

- Applications opt in by committing `infra/onboarding.json`. Version 1 declares one
  Application in `apps`; version 2 (2026-09-24) targets registered environments.
- [`add-repos.sh`](../../add-repos.sh) imports repositories privately, installs the
  synchronization workflow and webhook, validates the contract, renders public
  settings into `infra/onboarding-values.json` with `[skip ci]`, provisions
  declared registry, Vault, Keycloak, bootstrap and DNS resources, and starts one
  API pipeline with `ONBOARDING_EXPECTED_SHA` and `ONBOARDING_RUN_ID`.
- The helper accepts success only after required jobs pass, the final GitOps
  commit carries onboarding trailers, Argo CD reports that exact revision
  `Synced`/`Healthy`, and declared Deployments roll out.
- Progress is kept in a locked private journal so reruns resume or recover a lost
  pipeline response.

## Rationale

A declarative contract keeps application knowledge in the application repository
while letting the platform verify and provision shared prerequisites. Exact
revision checks prevent reporting success for a stale or unrelated deployment.

## Consequences

Onboarding can leave commits, credentials, DNS or paused Applications behind after
a failure; there is no automatic rollback. Only Swirl Demo App uses version 2; other
applications remain on version 1 in `apps`. Issues, merge requests, releases and
LFS objects are not synchronized.
