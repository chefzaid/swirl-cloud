# Incident Response

This guide covers recording an incident, gathering its evidence and meeting the
NIS2 and GDPR reporting deadlines. Technical first steps for each alert are in
the [alert runbooks](alert-runbooks.md).

## Record the incident

Open a GitLab incident (**Monitor → Incidents → Create incident**, or an issue of
type *Incident*) in the platform project and choose the **Incident** template
([`.gitlab/issue_templates/Incident.md`](../../.gitlab/issue_templates/Incident.md)).
It records when the organization became aware, the severity, affected services
and people, whether personal data is involved, the NIS2 significance test, the
reporting deadlines, the timeline, actions and the post-incident review. GitLab
incidents labelled `incident` form the incident register for audits.

## Gather the evidence

```bash
./install.sh --incident-report --start 2026-10-05T01:00Z --end 2026-10-05T03:00Z \
  --aware 2026-10-05T01:20Z --title "Registry unavailable"
```

The report, ready to paste into the incident, contains:

- due times for the NIS2 early warning (24 hours after awareness), incident
  notification (72 hours), final report (one month after the notification) and
  a GDPR personal data breach notification (72 hours);
- endpoints whose availability dropped in the window, from the availability
  probes, or from the hourly [platform reports](platform-reports.md) when
  Prometheus no longer holds the window;
- a timeline of alerts that fired and [security detection](security-detection.md)
  findings;
- Argo CD syncs and pod restarts in the window;
- Kubernetes writes and Secret reads by people (dry-runs and system components
  excluded), failed and successful logins with source addresses, and Keycloak
  administration changes;
- drafts of the three NIS2 reports with the known facts filled in.

`--format json` prints the raw evidence. The command only reads; it queries
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

An incident is significant under NIS2 Article 23(3) when it has caused, or can
cause, severe operational disruption or financial loss for the organization, or
considerable damage to other people or organizations. Whether the organization
is in NIS2 scope, and which authority receives the reports, depends on its
sector, size and country.

## After the incident

Complete the post-incident review in the incident, open follow-up issues for
each action, and attach the evidence report. Review open incidents and their
follow-ups with the monthly [platform report](platform-reports.md).

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Security detection](security-detection.md)
- [Compliance evidence](compliance-evidence.md)
