# Self-Operation

What an organization does to run its installation without the vendor: routine
checks, updates, backups and restores, rotations, where every credential lives,
and how to disconnect the vendor. Each task links to the procedure it follows.

**Status:** this runbook gathers the existing procedures. It counts as proven
only once an operator from outside the vendor has installed, restored and
upgraded an installation with it on a disposable host, with times and gaps
recorded (TODO §42).

## Routine

| When | Task | Procedure |
| --- | --- | --- |
| Daily | `./install.sh --status` shows no `FAIL`; act on each `Next:` line | [Verify the result](../deployment.md#verify-the-result) |
| Daily | Check firing alerts in Grafana or Alertmanager (notifications may be paused) | [Alert runbooks](alert-runbooks.md) |
| Daily | The nightly backup passed (`--status`, **Nightly backup**) | [Backups and recovery](../operations.md#backups-and-recovery) |
| Weekly | Review Critical and High findings past their deadline | `./install.sh --vulnerability-report`, [vulnerability triage](../security.md#vulnerability-triage) |
| Weekly | Review proposed version updates | [Image updates](../operations.md#image-updates) |
| Monthly | Read the Platform Report | [Monthly report](platform-reports.md#monthly-report) |
| Monthly | Build the evidence pack and review controls that fail or lack evidence | [Reports and evidence packs](compliance-evidence.md#reports-and-evidence-packs) |
| Quarterly | Access review: confirm or revoke every person's roles | [Access reviews](compliance-evidence.md#access-reviews) |
| Quarterly | Export and verify the recovery kit again | [Operator recovery checklist](../operations.md#operator-recovery-checklist) |
| Yearly | Restore drill on a disposable host, with measured times | [Restore after losing the host](../operations.md#restore-after-losing-the-host) |

## Updates

- **Platform releases:** verify the bundle, then rerun the installer or Ansible
  reconciliation on the new revision ([releases](releases.md),
  [platform reconciliation](../deployment.md#platform-reconciliation)). Reruns
  converge.
- **Images and charts:** one update at a time, with a backup first
  ([image updates](../operations.md#image-updates)).
- **Hosts:** security updates install automatically; an operator reboots in a
  maintenance window when they need it, and
  [`NodeRebootRequired`](alert-runbooks.md#noderebootrequired) fires after a week.
- **K3s:** no documented upgrade procedure yet; `./install.sh --upgrade` is
  planned (TODO §32).

## Credentials and recovery items

| Item | Where it lives | Rotation |
| --- | --- | --- |
| Platform service passwords and API keys | Vault, delivered by External Secrets ([data model](../data-model.md)) | [Credential age](../security.md#credential-age) lists their age |
| Local infrastructure administrator passwords | Vault | [Rotate local administrator passwords](../security.md#rotate-local-administrator-passwords) |
| Platform administrators | Keycloak (`platform-admins`), with optional second factor | [Administrator sign-in](../security.md#administrator-sign-in), [second factor](../security.md#administrator-second-factor) |
| Vault unseal key, bootstrap token, K3s token and encryption state, restic password | The control plane and the [recovery kit](../security.md#recovery-kit) | [Recovery credentials](../security.md#recovery-credentials) |
| Cloudflare, Tailscale and GitHub tokens | The installation input file, kept outside the repository | Replace at the provider, then rerun the installer with the new value |
| SMTP relay login | Vault `infra/smtp` | [Change or turn off](platform-mail.md#change-or-turn-off) platform mail |
| TLS certificates | Kubernetes TLS Secrets; `--status` warns 30 days before expiry | [Cloudflare](../dns.md#cloudflare) |

## Disconnect the vendor

Follow [exit plan, path 1](exit-plan.md#path-1-operate-without-the-vendor):
verify the recovery kit, rotate every credential the vendor knew, then remove
its time-limited grants (`./install.sh --admin-access list`, `revoke`), SSH keys
and accounts in Keycloak, GitLab and the tailnet.

## Related Guides

- [Operations](../operations.md)
- [Exit plan](exit-plan.md)
- [Incident response](incident-response.md)
