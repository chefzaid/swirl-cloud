# Security Detection

Prometheus alerts on metrics; some attacks only show in events. Every five
minutes the `security-detection` CronJob in `infra` checks the identity events
and the Kubernetes API audit log in Elasticsearch, and the platform
administrators in Keycloak, then posts its findings to Alertmanager. They reach
the same notification route as other alerts, with `owner: security`,
`source: security-detection` and a [runbook](alert-runbooks.md#security-detection).

## Rules

| Alert | Detects | Window |
| --- | --- | --- |
| `IdentityBruteForceThenSuccess` (critical) | Five or more failed logins for one username followed by a successful login | 15 minutes |
| `KubernetesRBACChangedOutsideGitOps` | Roles, ClusterRoles or bindings created, changed or deleted by anyone except Argo CD, K3s and Kubernetes controllers; dry-runs and rejected requests are ignored | 15 minutes |
| `KubernetesSecretReadByNewIdentity` | An identity reading Secrets that it did not read during the previous seven days; starts after seven days of audit history | 15 minutes |
| `NewPlatformAdministrator` | A user added to `platform-admins` in the platform realm, or given the `admin` role in the master realm, compared with the previous run | reported for a day |

Each run re-posts the current findings with a 30-minute end time, so an alert
stays active while its evidence is in the window and resolves on its own. A
failed run (for example Elasticsearch unavailable) raises
[KubernetesJobFailed](alert-runbooks.md#kubernetesjobfailed), and the
`security-monitoring` control of the [compliance evidence](compliance-evidence.md)
fails when no run succeeded for 30 minutes.

## Data and access

The job reads `identity-audit-*` ([identity events](../security.md#identity-events))
and `kubernetes-audit-*` ([API audit log](../security.md#kubernetes-api-audit-log))
with its own Elasticsearch account, `security_detection`, which may read those
indices and keep the administrator baseline in `security-detection-state`. The
account's password is generated in the cluster by an External Secrets password
generator; the Elasticsearch security bootstrap creates the account from it on
every sync. Keycloak is read with the bootstrap administrator, so its logins
appear in the master realm's events every five minutes.

## Change or add a rule

Rules live in [`k8s/platform/security-detection.yaml`](../../k8s/platform/security-detection.yaml).
Add a function that returns findings (`alertname`, `severity`, identifying
labels, `summary`, `description`), list it in the rule loop, add its runbook
section, and test it with a harmless event, for example:

```bash
kubectl create rolebinding detection-test --clusterrole=view --serviceaccount=default:default -n default
kubectl delete rolebinding detection-test -n default
kubectl -n infra create job --from=cronjob/security-detection detection-test
kubectl -n infra logs job/detection-test
```

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [Security](../security.md)
- [Compliance evidence](compliance-evidence.md)
