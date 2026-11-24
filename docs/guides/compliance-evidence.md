# Compliance Evidence

Swirl Cloud evaluates a catalog of platform controls continuously and maps each
to NIS2 (Article 21(2) and 23), ISO/IEC 27001:2022 Annex A, DORA, GDPR (Articles
28, 32 and 33) and the CIS Kubernetes and Ubuntu benchmarks. Live telemetry
proves the technical controls; the organization supplies documents for the rest.
Use it for security questionnaires, audit preparation and spotting a control that
stopped working.

The evidence supports an audit; it does not certify compliance. Scope, risk
assessment and organizational measures remain the organization's job.

## The control catalog

[`k8s/files/compliance-controls.yaml`](../../k8s/files/compliance-controls.yaml)
lists each control with its objective, framework mappings, automated checks and
the manual evidence an auditor will ask for. Each check has two Prometheus
queries:

| Query | Meaning |
| --- | --- |
| `present` | Must return data: proves the evidence source exists and reports. |
| `failing` | Returns data only while the control is not met. Existing alerts are reused (`ALERTS{alertname=...}`), so thresholds live in one place. |

A check **passes** when evidence is present and nothing fails, **fails** when
anything fails, and otherwise has **no evidence**. A control takes its worst
check; controls without checks are **manual**. Helm turns the catalog into
recording rules on every sync, evaluated every 15 seconds:

| Series | Value |
| --- | --- |
| `swirl_cloud_compliance_check_status{control,check}` | 2 passing, 1 no evidence, 0 failing |
| `swirl_cloud_compliance_control_status{control}` | worst check of the control |
| `swirl_cloud_compliance_framework_control_status{framework,control}` | control status per mapped framework |
| `swirl_cloud_compliance_framework_passing_ratio{framework}` | share of automated controls passing |
| `swirl_cloud_compliance_control_info{control,title,automated}` | catalog metadata |
| `swirl_cloud_compliance_requirement{control,framework,requirement}` | one series per mapped requirement |

## Dashboard and drift alerts

Grafana's **Compliance Evidence** dashboard (Applications folder) shows the
passing share and requirement coverage per framework, every control and check
needing attention, and the controls proven only by documents.

`ComplianceControlRegressed` fires when a control has been worse than its
six-hour median for 30 minutes, for example a firewall turned off, a backup that
stopped or a vulnerability baseline exceeded. Standing failures stay on the
dashboard without paging again.

## Reports and evidence packs

```bash
./install.sh --compliance                       # status per framework and control
./install.sh --compliance --framework nis2      # one framework (nis2, iso27001, dora, gdpr, cis)
./install.sh --compliance --format markdown     # report to paste or print (or json)
./install.sh --compliance --evidence-pack       # private archive for an auditor
./install.sh --compliance --evidence-pack --sign-key ~/.ssh/id_ed25519
```

The setup hub has the same under **Maintenance → Compliance report and evidence
pack**. `--strict` exits non-zero when a control fails or lacks evidence, for
scheduled checks.

The pack is written to `~/.local/state/swirl-cloud/compliance/` (`--output-dir`)
with mode `0600`:

| File | Content |
| --- | --- |
| `report.md`, `report.json` | Status per framework, requirement and control, with failing series and manual evidence checklists. |
| `controls.yaml` | The catalog used for the report. |
| `history.json` | Control status over the last 24 hours (`--history-hours`), limited by Prometheus retention. |
| `context/verification.json` | The platform verification report (`./install.sh --status`). |
| `context/trivy-compliance.json` | CIS Kubernetes, NSA and Pod Security Standards results with the failing check IDs. |
| `context/asset-inventory.json` | The [asset inventory](#asset-inventory) at the time of the pack. |
| `context/isms-documents.json` | With `--isms-documents DIR`: each [ISMS document](#isms-document-kit), whether its signed copy is there, who approved it and when, its unfilled fields and its SHA-256. |
| `context/access-review.json` | With `--access-review`: the [access review](#access-reviews) at the time of the pack. |
| `SHA256SUMS`, `SHA256SUMS.sig` | Hash of every file and, with `--sign-key`, an SSH signature over the manifest. |

No Kubernetes Secret is read. Prometheus keeps only recent history, so generate a
pack at least monthly and archive it with the period's manual evidence.

## Access reviews

```bash
./install.sh --access-review                                # summary and findings
./install.sh --access-review --output-dir ~/access-reviews  # review sheets (.md, .csv, .json)
```

The review lists every identity and what it can do:

- Keycloak users in every realm: groups, effective roles, last recorded login.
- GitLab users: administrator flag, two-factor status, last activity, group and
  project memberships.
- Kubernetes users, groups and ServiceAccounts bound to `cluster-admin`, `admin`
  or `edit`.
- Argo CD role mappings and local accounts, and the local break-glass
  administrators.
- Vault's sign-in roles: each OIDC role with the groups it admits and each
  Kubernetes role with the service accounts it admits, with the policies they
  grant (read with the bootstrap token through passwordless sudo, on a control
  plane).

It flags administrators, disabled or blocked accounts that keep memberships,
accounts idle for 90 days, missing GitLab two-factor where GitLab does not
require it, non-system ServiceAccounts with `cluster-admin`, and a Vault OIDC role
that grants policies to any account of the realm. Keycloak and
GitLab are read with their in-pod admin tools, so no Kubernetes Secret is read.

Owners mark each identity **Keep** or **Revoke**, the revocations are applied,
and the signed sheet is the `access-review` control's evidence. Run it at least
quarterly and when people leave. Inactivity uses Keycloak's stored login events
(90 days), so it is reliable only 90 days after event storage started. Vault
policies and Grafana roles follow the Keycloak groups in the sheet.

With `--output-dir`, the review also writes one sheet per access group of the
platform realm (`access-review-<time>-<group>.md`), for the person who knows who
should hold it: the group's owner in
[`config/access-owners.yaml`](../../config/access-owners.yaml), or the platform
administrators when none is named. Each sheet lists the group's members, whether
each holds a [time-limited grant](../security.md#access-with-an-end-date) (with its
approver and end) or a permanent membership, their last activity, and Keep or
Revoke columns with the owner's signature line. Send each owner their sheet and
keep the signed sheets with the overall one. `--tickets` opens one Zammad ticket
per group for its owner instead, with the sheet as the first message: the owner
must be named by email in `access-owners.yaml` and already have a Zammad
account (they get one at their first sign-in to the help desk); groups without
one are listed and get no ticket.

## Control calendar

Audits fail on a missed periodic review more often than on a missing tool. The
duties an installation owes on a schedule (access reviews, failure and restore
drills, the recovery kit, credential rotation, risk and policy reviews, an
incident exercise) are listed with their period, how they are done and the
requirement they evidence in
[`config/control-calendar.yaml`](../../config/control-calendar.yaml).

```bash
./install.sh --duty list                       # every duty, last done, overdue or not
./install.sh --duty done restore-drill --note "restored on a disposable host"
```

`--access-review --output-dir DIR` records the access review when its sheets are
complete, and `--game-day --record` records the drill when it passes; the other
duties are recorded with `--duty done`. Completions are kept in the ConfigMap
`infra/swirl-cloud-duties` (date, who, note), and `./install.sh --status` warns
under **Periodic duties** for each duty never done or past its period.

## Change records

`./install.sh --change-log [--since 2026-11-01] [--format json]` lists every change
to the platform and production since a date (30 days by default), from what
already records them: commits to the deployed configuration (`k8s/`, `config/`),
Argo CD's sync history of the root Application and of each production
Application, installer and Ansible runs, and just-in-time administrator grants.
Each entry names who, the approver where one is recorded, the revision, the
result and the way back (a revert, an Argo CD rollback, a revocation). Entries
without an approver are counted and marked for review, as ISO/IEC 27001 A.8.32
asks of emergency changes. A source that cannot be read is listed under **Not
collected**.

## Asset inventory

`./install.sh --asset-inventory` lists the assets NIS2 Art. 21(2)(i) asks to
manage: servers (hardware, OS, kernel, K3s version, location labels, owner),
workloads with their images, owner, data classification and criticality, data
stores (persistent volumes) and the outside services in
[external services](external-services.md). Staff devices are not inventoried
until an endpoint agent exists (TODO §37). `--output-dir DIR` writes
`servers.csv`, `workloads.csv`, `data-stores.csv`, `external-services.csv` and
`inventory.json` (mode `0600`); `--format json` prints the JSON. Every evidence
pack includes it.

Owner, data classification and criticality come from the `swirl-cloud.io/owner`,
`swirl-cloud.io/data-classification` and `swirl-cloud.io/criticality` labels.
Without a label, platform namespaces belong to `platform` and criticality follows
the environment (`high` for platform, `corp` and production, `medium` for `uat`,
`low` for `int`), marked *derived*. The run reads only node, workload and volume
metadata, never Secrets, and exits 1 when a source could not be read.

## Security questionnaires

`./install.sh --questionnaire caiq-v4` maps every catalog control to CSA Cloud
Controls Matrix v4 control IDs ([`config/questionnaires/caiq-v4.yaml`](../../config/questionnaires/caiq-v4.yaml));
CAIQ v4 numbers its questions under those IDs. For each CCM control it lists the
platform controls that answer it, their measure and their evidence: the automated
checks, or *manual* with the documents to attach. `--live` adds each control's
current status from Prometheus; `--format csv` or `json` changes the output, and
`--output-dir DIR` writes all three (mode `0600`). The mapping was drafted without
CSA's machine-readable bundle and says `verified: false` until it is checked
against the CAIQ version being answered; until then every output carries an
*unverified* warning. A catalog control missing from the mapping, or a malformed
CCM ID, stops the run. The sovereignty report joins the pack once it exists (TODO §33).

## Contract annex

`./install.sh --contract-annex` writes the annex customers attach to a contract:
the technical and organisational measures from the catalog (with the GDPR Art. 32
points each covers), the sub-processors from [external services](external-services.md)
(services selected at installation and data shared while running; download sources
only see requests), the data and processing locations from the nodes'
`topology.kubernetes.io/region` and `zone` labels, and DORA register-of-information
rows: the provider, then each sub-processor as its subcontractor. Pass
`--provider-name`, `--provider-lei`, `--provider-country` and
`--provider-exit-plan` (default: the [exit plan](exit-plan.md)); whatever only the provider or customer knows reads
`[to complete]`. `--generic` skips the cluster for contract templates. With
`--output-dir DIR` it also writes `register-of-information.csv`. It reads node
metadata only and exits 1 when the nodes could not be read.

## ISMS document kit

`./install.sh --isms-kit --output-dir DIR` writes four templates for the manual
evidence the catalog asks for (mode `0600`):

| Document | Prefilled from |
| --- | --- |
| `information-security-policy.md` | Every catalog control as a measure, with the requirements it maps to |
| `risk-register.md` | One risk per control, then each data set the backups do not recover yet ([coverage](../operations.md#coverage-and-recovery-points)) |
| `continuity-and-recovery-plan.md` | The backup coverage table and the restore, recovery-kit and [exit](exit-plan.md) procedures |
| `supplier-register.md` | Every service in [external services](external-services.md) |

Likelihood, impact, owners, objectives, contacts and agreements read
`[to complete]`. Each document ends with *Approved by*, *Approved on* and *Next
review* lines. Keep the signed copies in one directory and pass it to
`./install.sh --compliance --evidence-pack --isms-documents DIR`: the pack then
records which documents are missing, unsigned or approved, by whom and when.

## Records of processing

`./install.sh --processing-records > records.md` drafts the GDPR Article 30
register from the running platform. For each Argo CD application it lists
environments, the `swirl-cloud.io/owner` and `swirl-cloud.io/data-classification`
labels, public endpoints, data services and external processors found in its
credential names (never values) and persistent volumes. It adds the platform's
own activities (identity, GitLab, logs and audit, Odoo), where processing runs
and the retention applied. Purposes, data subjects, legal bases, recipients and
erasure deadlines are left in brackets for the controller. Regenerate it when
applications change and keep the completed version with the period's evidence.

## Data subject requests

When a person asks what the company holds about them (GDPR Art. 15, and Art. 20
for a portable copy), run on the control plane:

```bash
./install.sh --data-subject export --subject ada@example.com --reference DSR-12 --output-dir ~/requests
./install.sh --data-subject list
```

It writes a private directory with one JSON file per source and a `SUMMARY.md`:
the person's Keycloak account in the platform realm (profile, groups, linked
identity providers, consents, sessions, and their credentials' types and dates),
their sign-in and administration events, their time-limited access grants, their
GitLab account (profile, memberships, the titles of their SSH keys, and how many
issues, merge requests and comments they wrote), their
Zammad account with the tickets and messages they wrote, their Odoo contacts and
user, and whether they have a Vaultwarden account. Fields that look like secrets
are removed. A source that cannot be read is listed as not collected and the
command exits 1, so the answer is never silently incomplete. Vaultwarden vaults
are encrypted with the person's master password: they export theirs themselves.
Applications answer for their own data through their owners. The request is
recorded (date, reference, sources, due date one month later) in the ConfigMap
`infra/swirl-cloud-data-subject-requests`, with a digest instead of the address.
Send the directory to the person through a channel the request came from, then
delete it.

### Verify a pack

An auditor needs the archive, the signer's public key and Python 3.12 or later:

```bash
echo "evidence $(cat signer.pub)" > allowed_signers
scripts/compliance-report.py --verify swirl-cloud-evidence-<time>.tar.gz \
  --allowed-signers allowed_signers --signer evidence
```

Verification fails when a file was changed, added or removed, or the signature
does not match the manifest.

## Change the catalog

1. Add or edit a control in the catalog. Prefer an existing alert as the
   `failing` query; otherwise write a query that returns series only while the
   control is not met.
2. Run `./scripts/validate-repository.sh`; it renders the generated rules with
   the chart.
3. After the sync, run `./install.sh --compliance` and check the control on the
   dashboard.

Record an accepted exception (for example a benchmark check that does not apply)
by narrowing the `failing` query and naming the owner, rationale and review date
in the control's manual evidence.

## Related Guides

- [Security reference](../security.md)
- [Operations](../operations.md)
- [Features](../features.md)
