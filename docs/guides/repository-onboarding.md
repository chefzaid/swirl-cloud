# Add, Synchronize And Deploy Repositories

Run `./add-repos.sh` from this checkout on a configured control-plane host. It
imports GitHub repositories into GitLab, configures two-way synchronization and
deploys selected applications. Rerun it to add repositories, change public settings
or resume interrupted setup. The installer and
[Ansible](../deployment.md#optional-host-and-account-changes) call the same helper
when repository onboarding is selected. Application declarations stay in their
repositories; `swirl-cloud` has no application inventory.

## Guided flow

1. Enter a GitHub username, hidden personal access token and comma-separated
   repositories such as `catalog,org/api` (bare names belong to that user).
2. Confirm the public GitLab HTTPS URL and destination group, defaulted from the
   installed platform or GitLab Ingress. Nested groups work; names are preserved.
3. The helper creates missing groups/projects as private, configures sync and
   verifies the first copy. It manages the GitHub workflow, encrypted Actions
   secrets/variables and the GitLab push/tag webhook; a conflicting workflow is
   reported, not overwritten.
4. Select deployments: all successful imports (default), a comma-separated subset or
   `none`. Each must have the [onboarding contract](application-onboarding.md).
5. Supply declared app inputs (previous choices are defaults). The helper commits
   public configuration with `[skip ci]` and sets up declared registry, Vault,
   identity, bootstrap and DNS resources.
6. An API pipeline gets the source SHA and a unique run ID. The app publishes images
   and applies its Argo CD Application. The helper waits for required jobs, the
   exact revision `Synced`/`Healthy`, and declared Deployment rollouts.

Selecting a deployment authorizes these commits and service changes. `[READY]`
means delivery and readiness succeeded; missing contracts, prerequisites or required
jobs report `[CANNOT DEPLOY]` and exit nonzero, keeping imports for retry. New
projects are imported with CI disabled until selected for deployment; existing
projects keep their CI setting.

For version 1, a public configuration change pauses an auto-synced Application
(workloads keep running) before the settings commit; the app's release job, which
also owns first deployment, reapplies it and restores sync.

## Projects without GitHub

Projects already in the platform GitLab group deploy without GitHub, import or
mirroring (`REPOSITORY_SOURCE=gitlab` is equivalent to `--gitlab`):

```bash
./add-repos.sh --gitlab
GITLAB_PROJECTS=billing,portal DEPLOY_REPOSITORIES=all ./add-repos.sh --yes --gitlab
```

`GITLAB_PROJECTS` names projects directly in `GITLAB_GROUP_PATH` (discovered from the
installed platform). Each needs a pushed default branch with `infra/onboarding.json`;
missing projects are skipped. The rest of the flow is unchanged. `{{GITHUB_OWNER}}`
and `{{GITHUB_REPOSITORY}}` become the group's last path segment and project name.

## Environment deployment

Swirl Demo App uses contract version 2. First
[register the environments](../deployment.md#application-deployment-clusters) and
enable [shared data access](application-onboarding.md#shared-application-data), then
run `add-repos.sh` once on the central platform. The Cloudflare token needs Zone
Read, DNS Edit and SSL and Certificates Edit on the managed parent zone.

For every registered target, onboarding provisions registry access, separate data
credentials and browser clients, reconciles DNS/TLS, and writes the public inventory
and target settings into the repository. It also creates project-scoped
integration/release runners and their Application permissions; the default branch
must be protected and the shared instance runner is no longer used. See
[delivery permissions](../deployment.md#application-delivery).

`APP_SUBDOMAIN` is one label for all environments. With
`platform.hostnameStyle: suffix`, `demo` gives `demo-int.example.com`,
`demo-uat.example.com` and `demo.example.com`; nested hostnames and their
certificates are in the [installation guide](../deployment.md#application-deployment-clusters).
The initial deployment goes to `int`; set `ONBOARDING_DEPLOYMENT_ENVIRONMENT=uat` or
`prod` to change it. Later deployments and promotions use
[GitLab CI](../deployment.md#application-delivery).

Certificate/DNS prerequisites are checked for every registered target, even if only
one is deployed, so register environments as they become ready; a new target needs
another onboarding run. Existing Applications keep their pinned revisions (no sync
pause).

## Prerequisites and credentials

- Host: Bash, Git, curl, jq, GNU date, Python, PyYAML and libsodium (the installer
  provides the Ubuntu/Debian packages). Local Helm charts need Helm.
- Deployment: control-plane kubeconfig, `kubectl`, Argo CD in `infra`, the instance
  runner and requested shared services. Vault unsealed with KV-v2 `secret/`; working
  External Secrets and public ingress/TLS.
- Before publishing, onboarding checks the app namespace foundation, AppProject
  repository/destination permissions and certificate coverage for declared hosts.
  Repository credentials must complete a fresh ExternalSecret refresh before
  delivery.

Use a GitHub [fine-grained PAT](https://github.com/settings/personal-access-tokens/new)
with **Administration, Actions, Contents, Secrets, Variables and Workflows:
read/write** on the selected repositories. Organization rules must allow the token
and Actions; branch protection must allow workflow installation, configuration
commits, sync and release pushes (the helper never relaxes it). Empty or archived
sources are unsupported.

| Credential | Scope and handling |
|---|---|
| GitHub PAT | Encrypted Actions secret `REPOSITORY_SYNC_ADMIN_TOKEN` and GitLab webhook Authorization header; rerun with a replacement before expiry. |
| GitLab sync project token | `write_repository` and `self_rotate`; the monthly GitHub workflow rotates it near expiry. |
| GitLab setup administrator token | Issued locally through `gitlab-rails` and revoked on exit. An explicit `GITLAB_ADMIN_TOKEN` with `api` scope is accepted and kept. |
| App registry deploy token | `read_registry` and `read_repository` at the app-declared Vault path; valid credentials are reused. |
| Argo CD repository access | ExternalSecret `infra/repository-<hash>` projects the registry/repository credential from Vault; nothing goes into Git. |
| Cloudflare API token | **Zone Read** and **DNS Edit** for the zone (plus SSL and Certificates Edit for version 2); environment or hidden prompt. Uses the existing HA Tunnel. |
| Vault setup access | `VAULT_BOOTSTRAP_TOKEN_FILE`, default `/var/lib/swirl-cloud/vault-bootstrap-token`, via a local port-forward. Declared paths need read/create/update and KV metadata-read. |
| Optional Keycloak setup access | Platform administrator Secret via a local port-forward; no client credentials are committed. |

Secrets stay in memory or private temporary files, never in Git or the journal.
Keep unattended input files outside repositories with mode `0600`; they are not
deleted automatically.

Setup reaches GitLab through a temporary loopback `kubectl port-forward` (API, clone,
registry auth), so it does not depend on host DNS or browser login. An explicit
`GITLAB_URL` overrides this; if the GitLab Service is unavailable, imports fall back
to public GitLab. Deployment still needs cluster access.

Synchronization uses `GITLAB_PUBLIC_URL`: GitHub Actions must reach public GitLab
API/Git routes and GitLab must reach `api.github.com`, with no browser-only
Cloudflare Access in the way. Argo CD and app CI use the private service zone;
loopback addresses are never saved. Defaults and overrides:
[installed context discovery](application-onboarding.md#public-rendering-context).

## Application configuration

The [app-author reference](application-onboarding.md) defines files, inputs,
services and CI behavior. Public choices live in `infra/onboarding-values.json`, so
changed domains or groups survive releases; existing Vault values are never rotated.
Onboarding does not add nodes or enable [HA](high-availability.md). The shared
`apps` foundation exists even when Odoo is disabled. For hostname changes and
direct/Tunnel routing see [application DNS ownership](../dns.md#application-dns-ownership).

## Automation and reruns

`--yes` never prompts. Supply `DEPLOY_REPOSITORIES=all`, `none` or a comma-separated
subset, plus app inputs in a private JSON file keyed by GitHub slug:

```json
{
  "alice/catalog": {"APP_SUBDOMAIN": "catalog"},
  "org/api": {"APP_SUBDOMAIN": "api"}
}
```

```bash
chmod 600 /secure/repository-inputs.json
read -rsp 'GitHub personal access token: ' GITHUB_ADMIN_TOKEN; echo
read -rsp 'Cloudflare DNS token: ' CLOUDFLARE_API_TOKEN; echo
export GITHUB_ADMIN_TOKEN CLOUDFLARE_API_TOKEN
GITHUB_USERNAME=alice \
GITHUB_REPOSITORIES='catalog,org/api' \
GITLAB_PUBLIC_URL=https://gitlab.example.com \
GITLAB_GROUP_PATH=team \
DEPLOY_REPOSITORIES=all \
  ./add-repos.sh --yes --inputs /secure/repository-inputs.json \
    --state-dir /secure/repository-state
unset GITHUB_ADMIN_TOKEN CLOUDFLARE_API_TOKEN
```

- `--inputs`/`--state-dir` also work interactively (`REPOSITORY_INPUTS_FILE`,
  `REPOSITORY_STATE_DIR`). Unknown input names fail validation. An existing state
  directory must be mode `0700`.
- For the installer or Ansible, export these with `CONFIGURE_REPOSITORY_SYNC=true`.
- `ONBOARDING_TIMEOUT`: required-job wait in seconds, default `3600`, range
  `30`-`43200`. Application sync waits up to 15 minutes within it, then Deployment
  rollouts are checked.

Progress defaults to `~/.local/state/swirl-cloud/repositories`. Each locked journal
records project/cluster identity, configuration and pipeline progress, never
credentials. Rerun from the same host and state directory to resume a pipeline or
retry failed jobs. An active earlier pipeline must finish before settings change.
Missing or manual required jobs never count as success; fix the reported pipeline
first.

Pipeline intent is journaled before the API request. If the response is lost, a
rerun looks for the matching source SHA and `ONBOARDING_RUN_ID` instead of creating
another pipeline; an unresolved request stops recovery. Remove only
`pipeline_intent` from the journal, and only after confirming in GitLab that the
request created no pipeline.

Failures can leave configuration commits, credentials, DNS or a paused Application
in place. Completed setup is kept; nothing is deleted or rolled back automatically.
If configuration was published, the corrected release restores the Application's
policy; if not, a rerun restores it after verifying the source is unchanged. Other
selected repositories continue. A new host recovers public choices from Git but
needs the private journal to resume a recorded pipeline.

## Synchronization and validation

Reruns preserve visibility; private GitHub sources need private GitLab projects.
Sync fast-forwards the lagging side and merges compatible divergence without force
pushes. Resolve merge and tag conflicts before retrying. A deletion on one side only
is restored.

GitLab is the delivery and metadata authority (CI, Registry, protected environments,
merge requests, issues, releases, packages, LFS) and Argo CD reads from it. GitHub
is a Git mirror: sync exchanges only branches and tags. Forge metadata is protected
only by GitLab's own recovery ([backups and recovery](../operations.md#backups-and-recovery);
native GitLab backup is still open in TODO §12).

Importing this repository copies source only; the platform GitOps URL is a separate
installer choice.

Run [repository validation](../testing.md#validation), then test onboarding changes
with a disposable repository and the [app contract](application-onboarding.md):
private-source visibility, sync, a successful pinned pipeline, app readiness and an
idempotent rerun.
