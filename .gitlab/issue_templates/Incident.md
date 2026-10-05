<!-- Incident record. Keep times in UTC. See docs/guides/incident-response.md. -->

## Summary

- **Aware since:** YYYY-MM-DD HH:MM UTC (the reporting clocks start here)
- **Started (estimated):**
- **Resolved:**
- **Detected by:** alert / user report / supplier / other
- **Severity:** SEV1 (outage or breach) / SEV2 (degraded or contained) / SEV3 (minor)
- **Services affected:**
- **Users or customers affected:**
- **Personal data involved:** yes / no / unknown

## Significance

NIS2 Article 23(3): the incident is significant when it has caused, or can
cause, either of the following. Then report it to the national CSIRT or
competent authority.

- [ ] Severe operational disruption of the services or financial loss for the organization
- [ ] Considerable material or non-material damage to other natural or legal persons

GDPR Article 33: a personal data breach is notified to the supervisory authority
within 72 hours of becoming aware of it, unless it is unlikely to result in a
risk to people's rights and freedoms.

## Reporting deadlines

`./install.sh --incident-report --aware <time>` prints these due times and draft texts.

- [ ] Early warning (24 hours after awareness): due
- [ ] Incident notification (72 hours after awareness): due
- [ ] Final report (one month after the notification): due
- [ ] Personal data breach notification (72 hours after awareness), if personal data is involved: due
- [ ] Affected customers or users informed, where required

## Timeline

| Time (UTC) | Event | Source |
| --- | --- | --- |
|  |  |  |

## Evidence

Paste or attach the output of
`./install.sh --incident-report --start <time> --end <time> --aware <time>`.

## Actions taken

## Post-incident review

- **Root cause:**
- **Contributing factors:**
- **What worked:**
- **Follow-up issues:**

/label ~incident
