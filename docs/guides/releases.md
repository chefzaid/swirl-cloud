# Releases

A Swirl Cloud release is a tagged commit of this repository: the chart version
in [`k8s/Chart.yaml`](../../k8s/Chart.yaml) is the platform version, and the
annotated tag `v<version>` marks it. Versions are semantic: a major version for
changes that need manual migration, a minor version for new capabilities and a
patch version for fixes and image updates.

## Cut a release

```bash
scripts/release.py prepare 2.1.0        # sets the chart version, prepends CHANGELOG.md
git diff                                # review the version and changelog
git commit -am "Release 2.1.0"
git tag -a v2.1.0 -m "Swirl Cloud 2.1.0"
scripts/release.py bundle --ref v2.1.0 --sign-key ~/.ssh/release_ed25519
git push origin main v2.1.0
```

The changelog lists the commit subjects since the previous `v*` tag; edit it
into upgrade notes before committing when a release needs manual steps.

## Release bundle

`scripts/release.py bundle` writes `dist/swirl-cloud-<version>/`:

| File | Content |
| --- | --- |
| `swirl-cloud-<version>-source.tar.gz` | The repository at the release commit (`git archive`). |
| `sbom.cdx.json` | CycloneDX 1.5 software bill of materials: every pinned container image with its digest, the Helm charts and the K3s and Helm versions. |
| `images.txt` | One pinned image reference per line, for mirroring into an internal registry. |
| `RELEASE-NOTES.md` | Commit, date, changes since the previous tag and a component summary. |
| `SHA256SUMS`, `SHA256SUMS.sig` | Hash of every file and, with `--sign-key`, an SSH signature (namespace `swirl-cloud-release`). |

`scripts/release.py inventory` and `scripts/release.py sbom` print the same
inventory for the working tree at any time.

## Verify a bundle

```bash
echo "release $(cat release_ed25519.pub)" > allowed_signers
scripts/release.py verify swirl-cloud-2.1.0 --allowed-signers allowed_signers --signer release
```

Verification fails when a file was changed, added or removed, or when the
signature does not match. Publish the release public key with the product
documentation so customers can check bundles before installing them.

## Related Guides

- [Deployment](../deployment.md)
- [Operations: image updates](../operations.md#image-updates)
- [External services](external-services.md)
