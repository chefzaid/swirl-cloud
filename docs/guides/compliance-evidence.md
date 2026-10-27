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

It flags administrators, disabled or blocked accounts that keep memberships,
accounts idle for 90 days, missing GitLab two-factor where GitLab does not
require it, and non-system ServiceAccounts with `cluster-admin`. Keycloak and
GitLab are read with their in-pod admin tools, so no Kubernetes Secret is read.

Owners mark each identity **Keep** or **Revoke**, the revocations are applied,
and the signed sheet is the `access-review` control's evidence. Run it at least
quarterly and when people leave. Inactivity uses Keycloak's stored login events
(90 days), so it is reliable only 90 days after event storage started. Vault
policies and Grafana roles follow the Keycloak groups in the sheet.

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
`--provider-exit-plan`; whatever only the provider or customer knows reads
`[to complete]`. `--generic` skips the cluster for contract templates. With
`--output-dir DIR` it also writes `register-of-information.csv`. It reads node
metadata only and exits 1 when the nodes could not be read.

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
