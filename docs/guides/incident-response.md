# Incident Response

Record an incident, gather its evidence and meet the NIS2 and GDPR reporting
deadlines. First technical steps per alert are in the
[alert runbooks](alert-runbooks.md).

## Record the incident

In the platform GitLab project, open an incident (**Monitor → Incidents → Create
incident**, or an issue of type *Incident*) with the **Incident** template
([`.gitlab/issue_templates/Incident.md`](../../.gitlab/issue_templates/Incident.md)).
It covers awareness time, severity, affected services and people, personal data,
the NIS2 significance test, deadlines, timeline, actions and post-incident
review. Incidents labelled `incident` are the audit register.

## Gather the evidence

```bash
./install.sh --incident-report --start 2026-10-05T01:00Z --end 2026-10-05T03:00Z \
  --aware 2026-10-05T01:20Z --title "Registry unavailable"
```

`--end` defaults to now and `--aware` to `--start`; times without an offset are
UTC. The Markdown report, ready to paste into the incident, contains:

- NIS2 and GDPR due times, counted from `--aware` (see [Reporting](#reporting));
- endpoints whose availability dropped, from the probes or, when Prometheus no
  longer holds the window, the hourly [platform reports](platform-reports.md);
- a timeline of fired alerts and [security detection](security-detection.md)
  findings, Argo CD syncs and pod restarts;
- Kubernetes writes, RBAC changes and Secret reads by people (dry-runs and system
  components excluded), logins with source addresses and Keycloak admin changes;
- drafts of the three NIS2 reports with known facts filled in.

`--format json` prints the raw evidence. The command only reads and queries
Elasticsearch inside its pod, so no credential leaves the cluster. Detection
findings are kept 13 months in `security-findings-*`.

## Reporting

| Report | Deadline | Recipient |
| --- | --- | --- |
| NIS2 early warning | 24 hours after becoming aware of a significant incident | National CSIRT or competent authority |
| NIS2 incident notification | 72 hours after becoming aware | Same |
| NIS2 intermediate report | On request | Same |
| NIS2 final report | One month after the incident notification | Same |
| GDPR personal data breach notification | 72 hours after becoming aware, unless unlikely to result in a risk | Supervisory authority |

Under NIS2 Article 23(3) an incident is significant when it has caused, or can
cause, severe operational disruption or financial loss for the organization, or
considerable damage to others. NIS2 scope and the receiving authority depend on
the organization's sector, size and country.

## After the incident

Complete the post-incident review, open a follow-up issue per action and attach
the evidence report. Review open incidents and follow-ups with the monthly
[platform report](platform-reports.md).

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Security detection](security-detection.md)
- [Compliance evidence](compliance-evidence.md)
