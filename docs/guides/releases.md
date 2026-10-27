# Releases

A release is the annotated tag `v<version>` on a commit of this repository. The
chart version in [`k8s/Chart.yaml`](../../k8s/Chart.yaml) is the platform
version. Versions are semantic: major for changes that need manual migration,
minor for new capabilities, patch for fixes and image updates.

## Cut a release

```bash
scripts/release.py prepare 2.1.0        # sets the chart version, prepends CHANGELOG.md
git diff                                # review the version and changelog
git add k8s/Chart.yaml CHANGELOG.md     # CHANGELOG.md is new on the first release
git commit -m "Release 2.1.0"
git tag -a v2.1.0 -m "Swirl Cloud 2.1.0"
scripts/release.py bundle --ref v2.1.0 --sign-key ~/.ssh/release_ed25519
git push origin main v2.1.0
```

The changelog lists commit subjects since the previous `v*` tag, without merges.
Edit it into upgrade notes before committing when a release needs manual steps.

## Release bundle

`scripts/release.py bundle` writes `dist/swirl-cloud-<version>/` (change it with
`--output-dir`) and refuses to overwrite an existing bundle:

| File | Content |
| --- | --- |
| `swirl-cloud-<version>-source.tar.gz` | The repository at the release commit (`git archive`). |
| `sbom.cdx.json` | CycloneDX 1.5 SBOM: every pinned image with its digest, the Helm charts, and the K3s and Helm versions. |
| `images.txt` | One pinned image reference per line, for mirroring into an internal registry. |
| `THIRD_PARTY_NOTICES` | Licence, source and obligations of every SBOM component, from the [licence register](#third-party-licences). |
| `vex.openvex.json` | OpenVEX 0.2.0 statements for the [vulnerability exceptions](../security.md#vulnerability-triage) valid at the release, each naming the shipped image by digest; an exception for an image the release does not ship stops the bundle. |
| `RELEASE-NOTES.md` | Commit, date, changes since the previous tag and a component summary. |
| `SHA256SUMS`, `SHA256SUMS.sig` | Hash of every file and, with `--sign-key`, an SSH signature (namespace `swirl-cloud-release`). |

`scripts/release.py inventory`, `sbom` and `vex` print the same for the working
tree.

## Third-party licences

[`config/third-party-licences.yaml`](../../config/third-party-licences.yaml) holds
one record per SBOM component: its SPDX licence, source repository, obligations
and `customer_use`. `allowed` means the licence permits redistributing the
component and operating it for customers; `decision-needed` waits for the
owner's licensing decision (BUSL-1.1 Vault, Redis 8, SSPL MongoDB, the Elastic
images and `cp-kafka`). `scripts/release.py notices` prints `THIRD_PARTY_NOTICES`
and fails when an SBOM entry has no valid record or a record has no SBOM entry;
the validator and `bundle` run the same check. When adding or replacing an image
or chart, read the licence in its source repository and add the record in the
same commit. A record describes the component's own code: container images also
carry their base distribution's packages under those packages' licences.

Each record also names the `maintainer`, its `jurisdiction` and the pinned
release's `end_of_life`, with the `support_source` it comes from:

| `end_of_life` | Meaning |
| --- | --- |
| A date, such as `"2027-08-31"` | The release line stops receiving security fixes then. |
| `no-date` | Supported; no end announced yet. |
| `latest-only` | The project fixes only its latest release: stay on it. |
| `unverified` | Not checked yet against the maintainer's policy. |

`scripts/release.py support` lists the components by end of life, nearest first,
and exits 1 when one is past it. `bundle` refuses a release that ships a component
past its end of life on the release date. Dates come from the
[endoflife.date](https://endoflife.date) data where it covers the release, which
links each maintainer's policy. Record the new date in the same commit as an
update to another release line. Maintainer and jurisdiction (the maintainer's
country, or the Linux Foundation for CNCF projects) were recorded from public
knowledge on 2026-10-27; confirm them against each maintainer's legal notice
before relying on them in a contract.

## Verify a bundle

```bash
echo "release $(cat release_ed25519.pub)" > allowed_signers
scripts/release.py verify dist/swirl-cloud-2.1.0 --allowed-signers allowed_signers --signer release
```

The bundle can be a directory or a tar archive of one. Verification fails when a
file was changed, added or removed, when the signature does not match, or when
`--allowed-signers` is given and the bundle is unsigned. Publish the release
public key with the product documentation so customers can check bundles before
installing.

## Related Guides

- [Deployment](../deployment.md)
- [Operations: image updates](../operations.md#image-updates)
- [External services](external-services.md)
