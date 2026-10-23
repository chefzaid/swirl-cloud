# Testing Guide

Verification is layered: offline source and render checks, deployment and
recovery safety suites, live API dry-runs, default-branch service checks and
rehearsals. Only rehearsals prove that an installation, migration or failover
works.

## Test Layers

| Layer | Command or location | Proves |
| --- | --- | --- |
| Source syntax and policy | `./scripts/validate-repository.sh` | The [static checks](code-quality.md#static-checks): syntax, ShellCheck, documentation links, whitespace and a credential scan. |
| Rendering | `scripts/validate-manifests.py` (called by the validator) | The [manifest and deployment policy](code-quality.md#manifest-and-deployment-policy) over nine installer/GitOps render profiles, including installer/GitOps parity. |
| Safety suites | `tests/` | Deployment and recovery failure modes in the [validation policy](#validation-policy). |
| Live dry-run | `./scripts/validate-repository.sh --live` | API compatibility of every rendered profile with the active cluster, without changes. Objects in a namespace the profile creates or in `cnpg-system`, and CloudNativePG objects before HA activation, are counted and reported as not validated; any other missing namespace or API fails. |
| Platform CI | [`.gitlab-ci.yml`](../.gitlab-ci.yml) | Default-branch reconciliation of the exact commit, delivery services and Registry push/read. |
| Rehearsal | Disposable or representative hosts | Installation, enrollment, migration, restore and failover. |

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

Run `./scripts/validate-repository.sh` from the repository root before changing
installation or deployment behavior. It is offline and needs Bash, Git,
ShellCheck, Python 3 with PyYAML, Helm, Ansible, Node.js, jq, SQLite's `sqlite3`
CLI, `flock`, OpenSSL and the time zone database (`tzdata`). CI installs them. A
missing tool fails validation instead of skipping checks.

The nine render profiles cover single-node and HA, `infra` and Odoo scope,
PostgreSQL and Kafka migration phases with fencing, and authenticated shared
application data. The command then runs the [safety suites](#validation-policy).

`--live` dry-runs those profiles against the active Kubernetes context. It needs
`kubectl`, API access and the platform CRDs. Synthetic `example.com` settings
test API compatibility, not the live installation's credentials, routing or
health. Generated files are private and removed on exit.

Syntax and rendering cannot prove a successful install, image runtime
compatibility or failover. Rehearse installer and enrollment changes on
disposable hosts and follow each service's upgrade and recovery guide. See
[delivery](deployment.md#infrastructure-reconciliation-and-verification) for the
default-branch checks. A synthetic restore does not prove compatibility with
existing data; use protected copies of real backups for
[migration rehearsal](guides/platform-migration.md#prepare-and-rehearse).

## Validation policy

The validator runs only these safety suites:

| Retained suite | Failure it guards against |
| --- | --- |
| `test-k3s-backups.sh` | Unrestorable or incomplete archives (stale SQLite backups after etcd migration, missing recovery tokens or Vault keys, unsafe GitLab configuration copies), off-node uploads without repository credentials, restic retention that keeps every archive, success reported while the lock is held, and a weekly verification that passes a failed `restic check`, a bad checksum, a corrupt, foreign or empty repository, or runs during a backup. |
| `test-k3s-api-endpoint.sh` | An API VIP already held or outside the node network, a VIP on a server whose API is not ready, multicast VRRP fallback, and a certificate restart that leaves no fresh etcd majority. |
| `test-k3s-kubelet-memory.sh` | A K3s reservation that does not scale with node memory, a drop-in that replaces other kubelet arguments, and an eviction threshold that drops K3s's disk thresholds. |
| `test-postgres-ha.py` | Data or role loss, unverified cutover and restarting an obsolete writer; `--docker` adds an isolated SQL restore drill. |
| `test-kafka-ha.py` | Migration before preparation, incomplete replication and unsafe quorum changes. |
| `test-vault-unseal.py` | Recovery keys in process arguments, false unseal success, and a recovery kit that is unreadable, incomplete or stale. |
| `test-vault-ha.py` | Unsafe peer replacement, loss of recovery access and keys sent to the wrong host. |
| `test-node-fencing.py` | Powering off a healthy or wrong host, or recovering storage before fencing is confirmed. |
| `test-application-clusters.py` | Wrong cluster/namespace identity, unsafe shared-cluster ingress and unverified remote gateway access. |
| `test-application-data.py` | Cross-environment data access, anonymous remote cache access and unsafe database adoption; optional disposable datastore checks. |
| `test-environment-onboarding.py` | Wrong-target publication, runner privilege crossover, credential leakage, a certificate missing an environment hostname, a branch deployed to `uat`/`prod`, and concurrent runs on one environment. |
| `test-odoo-website.py` | The integration key or inbound email secret leaking into arguments, errors or routing rules, reuse of another integration's key, and unvalidated values reaching Odoo's chat frame and settings. |
| `test-outage-watch.py` | The relay password leaving the Worker secret, watching Access-gated or environment hosts, recreating the Durable Object class on each deploy, and SMTP failures over implicit TLS or STARTTLS, duplicate emails or dropped changes. |
| `test-platform-mail.py` | The relay password in arguments, output or errors, settings saved after a refused login, needless restarts, header/YAML/JSON injection through addresses, and an Alertmanager configuration that breaks with mail on or off. |
| `test-install-inputs.py` | Shell execution through the input file, inputs leaking from the calling shell, readable credential files, secrets in setup-hub arguments, the input file, run logs or support bundles, and installs that start with unreported input problems. |

Do not bring back broad application, UI, source-text or per-image test
frameworks. A new suite needs a concrete deployment or recovery failure that
syntax, manifest policy and rendering cannot catch. Image promotion still needs
isolated runtime and recovery checks
([image updates](operations.md#image-updates)); cluster availability still needs
the host-failure drills below.

## Observability Discovery Changes

### Validating changes

Run [repository validation](#validation) for manifests and embedded script
syntax. Then use a disposable app to check namespace and ownership boundaries,
dashboard links, an idempotent second pass, the selected Sonar project, its
scan-only pipeline and analysis timestamp. App repositories test successful
submission, scanner failure, a missing token and CI with both values of
`SONAR_SCAN_ONLY`; scan-only runs must never publish or deploy.

## Rehearsals And Drills

Rendering and unit-style tests cannot establish:

- fresh installation and node enrollment on disposable hosts;
- PostgreSQL, Kafka and Vault HA migrations with sample data, in the
  [HA activation order](guides/high-availability.md#activate-in-order);
- one-host failure drills, including the original control plane and the current
  database and consensus leaders;
- physical Redfish fencing for [persistent singletons](guides/node-fencing.md);
- restoring backups into an isolated environment and comparing data;
- Longhorn recovery, production authentication, Cloudflare Tunnel failover and
  Registry push/pull through the public edge;
- [platform migration](guides/platform-migration.md#prepare-and-rehearse) with
  protected copies of real backups.

## Known Verification Gaps

- With an off-node restic repository, `swirl-cloud-k3s-backup-verify.timer`
  runs `restic check` and reads back the newest archive weekly, but no automated
  drill restores an archive, and
  [restore after losing the host](operations.md#restore-after-losing-the-host)
  has not been rehearsed.
- The setup hub has been exercised only with scripted dialog answers, not in a
  real terminal.
- HA profiles, Tunnel ingress and fencing have not run on three independent hosts.
- Image upgrades rely on the manual checks in
  [image updates](operations.md#image-updates).

## Related Guides

- [Development](development.md)
- [Code quality](code-quality.md)
- [Deployment](deployment.md)
- [Operations](operations.md)
