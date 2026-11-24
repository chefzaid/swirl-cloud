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
- a timeline of fired alerts, [security detection](security-detection.md)
  findings and Kubernetes Warning events (evictions, out-of-memory kills, failed
  mounts), then Argo CD syncs and pod restarts;
- Kubernetes writes, RBAC changes and Secret reads by people (dry-runs and system
  components excluded), logins with source addresses and Keycloak admin changes;
- drafts of the three NIS2 reports with known facts filled in.

`--customer` writes, instead, the notice to the affected customers in English
and French, with the affected endpoints and times filled in and the rest in
brackets to complete. Without `--end` it says the incident is still in progress.

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

## Reporting as the platform's vendor (Cyber Resilience Act)

When the company supplies Swirl Cloud to customers (a support subscription, an
edition or a managed service, per the CRA decision record in TODO §43), it has
the manufacturer's duty to report, in force since 2026-09-11, through ENISA's
Single Reporting Platform to the CSIRT designated as coordinator in the country
of its main establishment. Two events count: an **actively exploited
vulnerability** in the platform, and a **severe incident** affecting its security
(one that harms the platform's ability to protect data or functions, or that led
to malicious code in it or in its update channel).

| Report | Deadline | Content |
| --- | --- | --- |
| Early warning | 24 hours after becoming aware | What is exploited or happened, whether malicious action is suspected, and the countries where the platform is offered |
| Notification | 72 hours after becoming aware | The product and versions affected, the nature of the exploit or incident, corrective measures taken and those users can take, and how sensitive the information is |
| Final report, exploited vulnerability | 14 days after a fix or mitigation is available | Severity and impact, the malicious actor where known, the fix or update |
| Final report, severe incident | One month after the notification | Severity and impact, type of threat or root cause, mitigation applied and ongoing |

Templates, to copy into the reporting platform:

- **Early warning.** "On [time] we became aware that [vulnerability or incident]
  in Swirl Cloud [versions] is [actively exploited / affecting its security].
  Malicious action is [suspected / not suspected]. Swirl Cloud is offered in
  [countries]."
- **Notification.** "Update of the early warning of [time]. Affected: Swirl Cloud
  [versions] ([components]). Nature: [exploit or incident]. Measures taken:
  [measures]. Measures for users: [upgrade, configuration change, none yet].
  Sensitivity: [assessment]."
- **Final report.** "Severity and impact: [assessment]. [Root cause or threat
  type / malicious actor where known.] Fix: [release and date] or mitigation:
  [measures]. [Cross-border impact.]"

The affected customers are told too, without undue delay, with the measures they
can take: `./install.sh --incident-report --customer` drafts that notice. A
customer's own installation is also an operator under NIS2 for its own reporting;
the [Reporting](#reporting) table applies to it.

## After the incident

Complete the post-incident review, open a follow-up issue per action and attach
the evidence report. Review open incidents and follow-ups with the monthly
[platform report](platform-reports.md).

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Security detection](security-detection.md)
- [Compliance evidence](compliance-evidence.md)
