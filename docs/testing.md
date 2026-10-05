# Testing Guide

Platform verification is layered: offline source and render checks, deployment
and recovery safety suites, live API dry-runs, default-branch service checks, and
rehearsals on disposable or representative hosts. Only the last layer proves that
an installation, migration or failover actually works.

## Test Layers

| Layer | Command or location | Proves |
| --- | --- | --- |
| Source syntax and policy | `./scripts/validate-repository.sh` | Shell, Python, YAML, JSON and Ansible syntax; ShellCheck; documentation links; manifest inventories; digest-pinned images; workload token/resource declarations; an owner and a runbook section for every alert. |
| Rendering | `scripts/validate-manifests.py` (called by the validator) | Nine installer/GitOps render profiles, resource references, Service selectors and named ports, Ingress backends, RBAC and installer/GitOps parity. |
| Safety suites | `tests/` | Deployment and recovery failure modes listed in the [validation policy](#validation-policy). |
| Live dry-run | `./scripts/validate-repository.sh --live` | API compatibility of every rendered profile with the active cluster, without mutation. Objects that need a namespace the profile creates, `cnpg-system` or CloudNativePG APIs before HA activation are counted and reported as not validated; any other missing namespace or API fails. |
| Platform CI | [`.gitlab-ci.yml`](../.gitlab-ci.yml) | Default-branch reconciliation of the exact commit, delivery services and Registry push/read. |
| Rehearsal | Disposable or representative hosts | Installation, enrollment, migration, restore and failover behavior. |

## Quick Commands

```bash
./scripts/validate-repository.sh
./scripts/validate-repository.sh --live
ansible-playbook -i ansible/inventory --syntax-check ansible/install.yml
ansible-playbook -i ansible/inventory --syntax-check ansible/deploy.yml
python3 tests/test-postgres-ha.py --docker   # optional isolated SQL restore drill
python3 scripts/verify-high-availability.py  # read-only HA readiness on a live cluster
```

## Validation

Run from the repository root before changing installation or deployment behavior:

```bash
./scripts/validate-repository.sh
./scripts/validate-repository.sh --live
```

The first command is offline and requires Bash, Git, ShellCheck, Python 3 with
PyYAML, Helm, Ansible, Node.js, jq, SQLite's `sqlite3` CLI, and `flock`. CI installs
these tools. Missing dependencies fail validation rather than silently skipping
deployment checks. It checks syntax, documentation links, manifest inventories,
workload policy and platform-owned image digests. Nine installer/GitOps render
combinations cover HA, Odoo scope, PostgreSQL staging/cutover,
Kafka migration phases, fencing and authenticated shared application data. Each render checks resource references,
Service selectors and named ports, Ingress backends, RBAC and installer/GitOps
parity, then the command runs the [deployment and recovery safety suites](#validation-policy).

The second command adds server-side dry-runs of the rendered profiles, including
the opted-in database migration and fencing resources, against the active
Kubernetes context.
It requires kubectl, access to the API and the platform CRDs; it does not install
resources. Synthetic `example.com` settings validate API compatibility, not the
live installation's credentials, routing or health. Generated files are private
and removed on exit.

Syntax and rendering cannot establish successful installation, image runtime
compatibility or failover. Rehearse installer/enrollment changes on disposable
hosts and follow each service's upgrade/recovery guide. See [delivery](deployment.md#infrastructure-reconciliation-and-verification)
for the additional default-branch reconciliation and service checks.

Rehearse host provisioning, Longhorn recovery, physical fencing, Tunnel failover
and production authentication in a representative environment before relying on
them. A synthetic upstream restore does not establish compatibility with existing
data; use protected copies of the actual backups for [migration rehearsal](guides/platform-migration.md#prepare-and-rehearse).

## Validation policy

Run `./scripts/validate-repository.sh` before submitting changes. It checks actual
source inputs and rendered deployment output, then runs only these safety suites:

| Retained suite | Failure it guards against |
| --- | --- |
| `test-k3s-backups.sh` | Unrestorable archives, stale SQLite backups after etcd migration, missing recovery tokens, and Vault keys dropped from archives without a matching recovery kit. |
| `test-postgres-ha.py` | Data or role loss, unverified cutover, and restarting an obsolete writer. Includes an optional isolated SQL restore drill. |
| `test-kafka-ha.py` | Migration before preparation, incomplete replication and unsafe quorum changes. |
| `test-vault-unseal.py` | Recovery keys leaking into process arguments, false unseal success, and a recovery kit that cannot be decrypted, misses keys or is accepted after the keys changed. |
| `test-vault-ha.py` | Unsafe peer replacement, loss of recovery access and distributing keys to the wrong host. |
| `test-node-fencing.py` | Powering off a healthy/wrong host or recovering storage before confirmed fencing. |
| `test-application-clusters.py` | Wrong cluster/namespace identity, unsafe shared-cluster ingress, and unverified remote gateway access. |
| `test-application-data.py` | Cross-environment data access, anonymous remote cache access and unsafe database adoption; optional disposable datastore checks. |
| `test-environment-onboarding.py` | Wrong-target publication, runner privilege crossover, credential leakage, accepting a certificate that does not cover an environment hostname, and deploying a branch to `uat`/`prod` or a second concurrent run to one environment. |
| `test-install-inputs.py` | Shell execution through the installation input file, inputs leaking in from the calling shell, readable credential files, secrets (including generated passwords and file references) in setup-hub process arguments, the input file, run logs or support bundles, and installs starting with unreported input problems. |

Do not restore broad application, UI, source-text assertion or per-image test
frameworks here. New tests need a concrete deployment or recovery failure that
syntax, manifest policy and rendering cannot detect. Image promotion still
requires isolated runtime and recovery checks; cluster availability still
requires the documented host-failure drills. See [validation](#validation) for commands and coverage, and
[maintenance](operations.md#image-updates) for image promotion.

## Observability Discovery Changes

### Validating changes

Run [repository validation](#validation) for manifests and embedded
script syntax. After discovery changes, use a disposable app to check namespace
and ownership boundaries, dashboard links, an idempotent second pass, the selected
Sonar project, its scan-only pipeline and analysis timestamp. App repositories
validate successful submission, scanner failure, missing-token behavior and CI
with both values of `SONAR_SCAN_ONLY`; scan-only runs must never publish or deploy.

## Rehearsals And Drills

These behaviors cannot be established by rendering or unit-style tests:

- fresh installation and node enrollment on disposable hosts;
- PostgreSQL, Kafka and Vault HA migrations with sample data, following the
  [HA activation order](guides/high-availability.md#activate-in-order);
- controlled one-host failure drills, including the original control plane and
  current database and consensus leaders;
- physical Redfish fencing for [persistent singletons](guides/node-fencing.md);
- restoring backups into an isolated environment and comparing data;
- Cloudflare Tunnel failover and Registry push/pull through the public edge;
- [platform migration](guides/platform-migration.md#prepare-and-rehearse) rehearsal
  with protected copies of real backups.

## Known Verification Gaps

- No automated restore drill verifies offsite archives or `restic check`, and the
  [restore after losing the host](operations.md#restore-after-losing-the-host)
  procedure has not been rehearsed.
- The setup hub has been exercised only with scripted dialog answers, not in a
  real terminal.
- HA profiles, Tunnel ingress and fencing have not been exercised on three
  independent hosts.
- Image upgrades rely on manual runtime and recovery checks described in
  [image updates](operations.md#image-updates).

## Related Guides

- [Development](development.md)
- [Code quality](code-quality.md)
- [Deployment](deployment.md)
- [Operations](operations.md)
