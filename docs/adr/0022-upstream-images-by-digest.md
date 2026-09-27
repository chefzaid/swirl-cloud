# ADR 0022: Pin Public Upstream Images By Digest

- Status: Accepted
- Date: 2026-09-14
- Supersedes: [ADR 0016](0016-private-security-images.md)

## Context

Privately rebuilt images ([ADR 0016](0016-private-security-images.md)) made the
platform depend on its own Registry to boot, required pull credentials everywhere
and diverged from vendor support.

## Decision

- Use public vendor images only, pinned by `@sha256` digest in the owning manifest
  or Helm values; the validator rejects any platform image without a digest.
- Remove the private image profiles, override admission policies and platform pull
  credentials. Keep the retired profile files only as guards that stop rendering.
- Update images through the [image update procedure](../operations.md#image-updates):
  compatibility review, scanner comparison across all severities, disposable
  restore from the previous image, validation and post-rollout verification.
- Keep application images and their credentials in application repositories.

## Rationale

Digest pins give reproducibility and supply-chain integrity without a private
build pipeline. Vendor images keep upstream support and remove a boot-time
dependency on the platform Registry.

## Consequences

Unfixed upstream vulnerabilities remain visible in Trivy until vendors publish
fixes; the live cluster reports 119 critical and 924 high findings. Pinned digests
need deliberate updates. Recovery during a Registry or internet outage requires
pre-imported images.
