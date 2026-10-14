# ADR 0010: Keep Recovery Archives On The Host With Optional Encrypted Offsite Copies

- Status: Accepted
- Date: 2026-08-13
- Amended: 2026-09-07, 2026-09-20, 2026-10-14

## Context

A single-host platform must be able to restore Kubernetes state, Vault and shared
databases after corruption or operator error. Restoring K3s requires the server
token and encryption configuration, not just a datastore snapshot.

## Decision

Install `swirl-cloud-k3s-backup.timer` (daily 02:15 UTC ± 15 min) running
[`backup-k3s.sh`](../../scripts/backup-k3s.sh), which writes a root-only archive
to `/var/backups/swirl-cloud/k3s` containing:

- a consistent SQLite backup or embedded-etcd snapshot
- server token, secrets-encryption configuration and K3s configuration
- a Vault Raft snapshot and host recovery material
- `pg_dumpall` and `mongodump` of the shared databases
- GitLab's `/etc/gitlab` (secrets, SSH host keys, `gitlab.rb`) with its version and image
- PVC and Longhorn volume inventories and a datastore-specific `RESTORE.txt`

Keep seven local archives. When configured, restic encrypts and uploads them to
S3-compatible storage (7 daily, 5 weekly, 12 monthly) and Longhorn backs up
enrolled volumes to the same bucket.

## Rationale

One archive per day gives a self-contained restore point that matches the K3s
restore procedure. Offsite upload stays optional so installation does not require
an object-storage account.

## Consequences

Without offsite configuration, archives do not survive host or disk loss — the
current live state. GitLab data needs its own native backup, including the
Registry metadata database; only its configuration and secrets are in this
archive. Backup success is an exit status, not a verified restore. These gaps
are open items in [TODO.md](../../TODO.md).
