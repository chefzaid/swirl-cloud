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
| `RELEASE-NOTES.md` | Commit, date, changes since the previous tag and a component summary. |
| `SHA256SUMS`, `SHA256SUMS.sig` | Hash of every file and, with `--sign-key`, an SSH signature (namespace `swirl-cloud-release`). |

`scripts/release.py inventory` and `scripts/release.py sbom` print the same
inventory for the working tree.

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
