# Security Detection

Some attacks show only in events, not metrics. Every five minutes the
`security-detection` CronJob in `infra` checks the identity events, the
Kubernetes API audit log, the hosts' SSH and sudo journal lines and the Keycloak
administrators, and posts findings to
Alertmanager with `owner: security`, `source: security-detection` and a
[runbook](alert-runbooks.md#security-detection).

## Rules

| Alert | Detects | Window |
| --- | --- | --- |
| `IdentityBruteForceThenSuccess` (critical) | Five or more failed logins for one username followed by a successful login | 15 minutes |
| `KubernetesRBACChangedOutsideGitOps` | Roles, ClusterRoles or bindings created, changed or deleted by anyone except Argo CD, K3s and Kubernetes controllers; dry-runs and rejected requests are ignored | 15 minutes |
| `KubernetesSecretReadByNewIdentity` | An identity reading Secrets that it did not read during the previous seven days; starts after seven days of audit history | 15 minutes |
| `NewPlatformAdministrator` | A user added to `platform-admins` in the platform realm, or given the `admin` role in the master realm, compared with the previous run | reported for a day |
| `HostSSHBruteForceThenSuccess` (critical) | A successful SSH login from an address that failed five or more times on the same host; Fail2ban and CrowdSec bans are not in the journal, so the failures stand in for them | 15 minutes |
| `HostSudoByNewUser` | A user running a command with sudo who is not in the learned history; the first run learns the last seven days | reported for a day |

Each run re-posts current findings with a 30-minute end time, so an alert stays
active while its evidence is in the window, then resolves. A failed run (for
example Elasticsearch down) raises
[KubernetesJobFailed](alert-runbooks.md#kubernetesjobfailed); the
`security-monitoring` [compliance](compliance-evidence.md) control fails after
30 minutes without a successful run. The job also ends expired
[just-in-time administrator grants](../security.md#just-in-time-administration).

## Data and access

The job reads `identity-audit-*` ([identity events](../security.md#identity-events)),
`kubernetes-audit-*` ([API audit log](../security.md#kubernetes-api-audit-log))
and `kubernetes-logs-host-*` (the host journal) as the Elasticsearch user
`security_detection`. That user may also write `security-detection-state`
(administrator and sudo baselines), `security-findings-*` and
`access-grants`. Its password comes from an in-cluster External Secrets
generator; the Elasticsearch security bootstrap creates the account on every
sync. Keycloak is read with the bootstrap administrator, so its logins appear in
the master realm's events every five minutes.

## Change or add a rule

Rules live in [`k8s/platform/security-detection.yaml`](../../k8s/platform/security-detection.yaml).
Add a function that returns findings (`alertname`, `severity`, identifying
labels, `summary`, `description`), add it to the rule loop, write its runbook
section and test with a harmless event:

```bash
kubectl create rolebinding detection-test --clusterrole=view --serviceaccount=default:default -n default
kubectl delete rolebinding detection-test -n default
kubectl -n infra create job --from=cronjob/security-detection detection-test
kubectl -n infra logs job/detection-test
```

## Related Guides

- [SOC forwarding](soc-forwarding.md)
- [Alert runbooks](alert-runbooks.md)
- [Security](../security.md)
- [Compliance evidence](compliance-evidence.md)
