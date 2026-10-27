# Exit Plan

How a customer keeps running the platform without the vendor, or moves its data
and applications elsewhere. Contract annexes and the DORA register of
information ([contract annex](compliance-evidence.md#contract-annex)) reference
this plan; DORA requires exit strategies for ICT services that support critical
or important functions ([Art. 28(8) and 30](https://www.springlex.eu/en/packages/dora/dora-regulation/article-30/)).

**Status:** the paths below use the platform's backups and standard tools. A
single `./install.sh --export`, the self-operation runbook, the handover
procedure and a yearly exit drill are planned and not yet done (TODO §42). No
exit has been rehearsed, so no duration is promised.

## What the customer already holds

- **The platform itself.** Every service runs from this repository and pinned
  upstream images; the [release bundle](releases.md) lists every component, its
  digest and its licence. Nothing in the running platform calls home to the
  vendor ([external services](external-services.md)).
- **Configuration.** Kubernetes manifests and Helm values are plain YAML in Git;
  Argo CD applies them from the GitOps repository chosen at
  installation (`GITOPS_REPOSITORY_URL`). The installation
  identity is the `infra/swirl-cloud-identity` ConfigMap.
- **Credentials and keys.** Vault holds the platform secrets. The
  [recovery kit](../security.md#recovery-kit) holds what restores Vault and K3s;
  the customer exports and keeps it ([operator recovery checklist](../operations.md#operator-recovery-checklist)).
- **Data.** The nightly archive ([coverage](../operations.md#coverage-and-recovery-points))
  holds the K3s state, a Vault snapshot, `pg_dumpall`, `mongodump` and the Odoo
  filestore, on the host and, when configured, in the customer's own S3 bucket.

## Path 1: operate without the vendor

1. Export and verify the recovery kit, and check that `./install.sh --status`
   passes.
2. Rotate every credential the vendor knew: platform administrator accounts in
   Keycloak, `./install.sh` Maintenance → password rotation for local
   administrators, and the GitLab, Cloudflare, Tailscale and backup tokens
   entered at installation.
3. Remove vendor access: `./install.sh --admin-access list` then `revoke` for any
   time-limited grant, vendor SSH keys from every host, and vendor accounts from
   Keycloak, GitLab and the tailnet.
4. Keep updates flowing from this repository's releases ([verify a bundle](releases.md#verify-a-bundle)),
   or fork it.

## Path 2: move elsewhere

| Data | Export today | Format | Gaps |
| --- | --- | --- | --- |
| Application source and history | `git clone --mirror` per project, or GitLab's project export | Git, GitLab export archive | Issues and merge requests need the GitLab export, project by project, until native GitLab backup exists (TODO §12). |
| Container images | `skopeo copy` or `docker pull` from the GitLab Registry | OCI images | No bulk export yet. |
| PostgreSQL databases (applications, Keycloak, SonarQube, Odoo) | `pg_dumpall` in the nightly archive, or `pg_dump` per database | SQL | Up to 24 h old unless dumped again before the move. |
| MongoDB | `mongodump` in the nightly archive | BSON | Same. |
| Odoo | Its database dump with the filestore `tar` from the same archive | SQL and files | Restore into the same Odoo series. |
| Identity | Keycloak realm export (`kc.sh export`) | Keycloak JSON | Passwords export as hashes; a different identity provider needs users to reset them. |
| Secrets | `vault kv get` per path, or a Vault Raft snapshot | JSON, Vault snapshot | A snapshot restores only into Vault. |
| Deployments | The application repositories' manifests and Argo CD Applications | Kubernetes YAML | Platform-specific annotations (ingress, secrets) need mapping on another platform. |
| Dashboards | Provisioned dashboards in Git; user-made ones through Grafana's export | Grafana JSON | |
| Metrics, logs, audit events | Not exported | | Short retention by design; export what audits need before the move. |
| Kafka topics, Redis | Not exported | | Applications replay or rebuild them from their own state. |

After the copy is verified on the new platform, erase the old installation's
volumes, backups and off-node bucket, and record what was erased and when. A
`--wipe` command with an erasure record is planned (TODO §42).

## Related Guides

- [Operations: backups and recovery](../operations.md#backups-and-recovery)
- [Releases](releases.md)
- [Compliance evidence](compliance-evidence.md)
