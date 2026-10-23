# Code Quality

This repository is Bash, Python, YAML, Helm templates and Markdown. One offline
gate enforces quality: static checks, deployment policy on rendered manifests
and supply-chain pinning. [SonarQube](operations.md#source-analysis) serves
application repositories, not this one.

## How It Runs

| Where | What runs |
| --- | --- |
| Local | `./scripts/validate-repository.sh` before every commit |
| Platform CI | `validate:repository` on every branch and merge request in a digest-pinned Alpine image with an APK cache |
| Default branch | `verify:gitops`, `verify:delivery-platform`, `verify:registry-write` after Argo CD reconciles the commit; non-blocking `report:platform-artifacts` Trivy report on vendored charts and the CI image (kept 30 days) |

See [testing](testing.md#test-layers) for the full layer map.

## Static Checks

- `bash -n` and ShellCheck (`shellcheck -x`, configured by [`.shellcheckrc`](../.shellcheckrc)) for every tracked shell script
- Python compilation for every tracked `.py` file and for Python embedded in ConfigMaps
- `bash -n` for shell and `node --check` for JavaScript embedded in ConfigMaps
- YAML and JSON parsing for every tracked file
- Helm lint for every render profile
- Ansible `--syntax-check` for every playbook
- relative Markdown links must point to existing files
- `git diff --check` whitespace and a scan for recognizable Cloudflare, Tailscale and private-key material

## Manifest And Deployment Policy

[`validate-manifests.py`](../scripts/validate-manifests.py) enforces:

- every platform image is pinned by an `@sha256` digest
- every Deployment, StatefulSet, DaemonSet, Job and CronJob declares
  `automountServiceAccountToken` and CPU/memory requests and limits for every container
- every liveness probe has a timeout of 5 seconds or more and restarts only after
  a minute of failures (`periodSeconds` × `failureThreshold` ≥ 60); every readiness
  probe has a timeout of 3 seconds or more. A loaded node answers slowly, and
  restarting a datastore or Keycloak disconnects every application
- no exec probe uses `$(VAR)` unless the variable has a literal `value`; probes
  expand only those, so a Secret's value would be empty
- every Ingress uses the `traefik` class and references an existing Service port
- Ingress hostnames match `DEFAULT_CLOUDFLARE_HOST_LABELS`
- Traefik entry points, alone and with the HA values layered, use only defined
  Middlewares, and `websecure` carries the HSTS and `nosniff` policy that
  `configure-cloudflare.sh` sets at the edge
- every alert names an owner and links to its section of the [alert runbooks](guides/alert-runbooks.md)
- every manifest in the `platform.env` inventories exists without duplicates
- Service selectors, named ports, RBAC and chart file references resolve
- every Elasticsearch `index.lifecycle.name` names a policy that a rendered job or init container stores
- installer and GitOps renders agree for nine profiles
- retired image profiles stop rendering
- onboarding keeps branch names as strings, follows a default-branch switch and rejects unrelated revisions
- GitHub Actions are pinned by commit SHA

## Conventions

- Keep shell `set -euo pipefail`, pass secrets through stdin or private files, and never in arguments or logs.
- Keep Kubernetes security contexts non-root, `RuntimeDefault` seccomp and dropped capabilities unless a documented host requirement exists.
- Keep each concern in its owning manifest or helper; see [change the owning source](development.md#change-the-owning-source).
- Use imperative, descriptive commit subjects; add `[skip ci]` only for documentation or configuration commits that must not start delivery.
- Keep generated output, journals and credentials outside Git.

## Verification And Troubleshooting

- ShellCheck failures print the rule; fix the code rather than adding a disable
  unless the rule is a documented false positive.
- `image must have an immutable digest` names the manifest; resolve the tag to its
  digest and follow [image updates](operations.md#image-updates).
- `Ingress and public DNS inventories differ` means a hostname was added in one
  place only; update both the Ingress and `platform.env`.
- `uses undefined middleware` or `HTTPS origin headers differ from the Cloudflare
  edge policy` names the Traefik values profile; align `config/traefik-values.yaml`
  with the edge ([ingress controller](dns.md#ingress-controller)).
- `a liveness probe needs a timeout` or `must fail for a minute` names the
  container: raise `timeoutSeconds` (10 for a datastore) or `failureThreshold`; give
  a slow start a `startupProbe` instead of a long `initialDelaySeconds`.
- Render failures print the profile label; reproduce it with the
  [local render](development.md#render-locally).

## Related Guides

- [Development](development.md)
- [Testing](testing.md)
- [Security](security.md)
