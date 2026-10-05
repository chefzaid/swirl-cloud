# Compliance Evidence

Swirl Cloud evaluates a catalog of platform controls continuously and maps each
one to the requirements it supports in NIS2 (Article 21(2) and 23), ISO/IEC
27001:2022 Annex A, DORA, GDPR (Articles 28, 32 and 33) and the CIS Kubernetes
and Ubuntu benchmarks. The platform proves the technical controls from live
telemetry; the organization supplies the documents for the rest. Use it to
answer customer security questionnaires, prepare an audit and notice when a
control stops working.

The evidence supports an audit; it does not certify compliance. Scope,
risk assessment and the organizational measures remain the organization's
responsibility.

## The control catalog

[`k8s/files/compliance-controls.yaml`](../../k8s/files/compliance-controls.yaml)
lists each control with its objective, framework mappings, automated checks and
the manual evidence an auditor will ask for. Each automated check has two
Prometheus queries:

| Query | Meaning |
| --- | --- |
| `present` | Must return data: proves the evidence source exists and reports. |
| `failing` | Returns data only while the control is not met. Existing alerts are reused (`ALERTS{alertname=...}`), so thresholds live in one place. |

A check **passes** when evidence is present and nothing is failing, **fails**
when anything is failing and reports **no evidence** otherwise. A control takes
the worst status of its checks; controls without checks are **manual**.

Helm turns the catalog into Prometheus recording rules on every sync, so
Prometheus evaluates the controls every 15 seconds:

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
passing share per framework, every control and check needing attention, the
requirement coverage per framework and the controls proven only by documents.

`ComplianceControlRegressed` fires when a control has been worse than its best
state of the last six hours for 30 minutes, for example a firewall turned off,
a backup that stopped or a vulnerability baseline exceeded. Standing failures
stay visible on the dashboard without paging repeatedly.

## Reports and evidence packs

```bash
./install.sh --compliance                       # status per framework and control
./install.sh --compliance --framework nis2      # one framework
./install.sh --compliance --format markdown     # report to paste or print
./install.sh --compliance --evidence-pack       # private archive for an auditor
./install.sh --compliance --evidence-pack --sign-key ~/.ssh/id_ed25519
```

The setup hub offers the same under **Maintenance → Compliance report and
evidence pack**. `--strict` exits non-zero when a control fails or lacks
evidence, for use in scheduled checks.

The evidence pack is written to `~/.local/state/swirl-cloud/compliance/` with
mode `0600` and contains:

| File | Content |
| --- | --- |
| `report.md`, `report.json` | Status per framework, requirement and control, with failing series and manual evidence checklists. |
| `controls.yaml` | The catalog used for the report. |
| `history.json` | Control status over the last 24 hours (`--history-hours`), limited by Prometheus retention. |
| `context/verification.json` | The platform verification report (`./install.sh --status`). |
| `context/trivy-compliance.json` | CIS Kubernetes, NSA and Pod Security Standards results with the failing check IDs. |
| `SHA256SUMS`, `SHA256SUMS.sig` | Hash of every file and, with `--sign-key`, an SSH signature over the manifest. |

No Kubernetes Secret is read. Generate a pack at least monthly and store it
with the period's manual evidence; Prometheus keeps only recent history, so the
archived packs are the long-term record.

### Verify a pack

An auditor needs the archive, the signer's public key and Python 3.12 or later:

```bash
echo "evidence $(cat signer.pub)" > allowed_signers
scripts/compliance-report.py --verify swirl-cloud-evidence-<time>.tar.gz \
  --allowed-signers allowed_signers --signer evidence
```

Verification fails when a file was changed, added or removed, or when the
signature does not match the manifest.

## Change the catalog

1. Add or edit a control in the catalog. Prefer an existing alert as the
   `failing` query; otherwise write a query that returns series only while the
   control is not met.
2. Run `./scripts/validate-repository.sh`; it renders the generated rules with
   the rest of the chart.
3. After the sync, run `./install.sh --compliance` and check the new control on
   the dashboard.

Record an accepted exception (for example a benchmark check that does not apply)
by narrowing the `failing` query and naming the owner, rationale and review date
in the control's manual evidence.

## Related Guides

- [Security reference](../security.md)
- [Operations](../operations.md)
- [Features](../features.md)
