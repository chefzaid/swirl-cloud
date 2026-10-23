# Application Onboarding Contract

Applications opt into `add-repos.sh` by committing `infra/onboarding.json`, which
declares supported platform operations; repository scripts and playbooks are never
run. This page is for application authors; operators use
[repository onboarding](repository-onboarding.md). Version 2 (Swirl Demo App)
delivers to environment namespaces; version 1 remains for existing apps in `apps`.
See also the [namespace and discovery contract](../operations.md#namespace-and-discovery).

## Shared-platform contract (version 2)

The application has one default branch, one `.gitlab-ci.yml`, one registry and the
Argo CD template `infra/argocd/application.yaml`. CI renders
`infra/argocd/<env>.yaml` and `infra/environments/<env>/` from it for the selected
registered cluster. Version 2 uses the common [fields](#fields) plus exactly:

```json
{
  "version": 2,
  "deployment": {
    "inventory": "infra/deployment-environments.json",
    "settingsDirectory": "infra/environments",
    "defaultEnvironment": "int",
    "dataServices": ["postgres", "redis", "kafka"]
  }
}
```

It rejects `bootstrap`, `vault` and a `DEPLOYMENT_ENVIRONMENT` pipeline variable.
The template selects `infra/k8s` or `infra/overlays/ha` (initial replica profile).
Other apps stay on version 1 until migrated; onboarding changes no source code.

Onboarding commits a public copy of the central `infra/deployment-environments`
ConfigMap inventory. Per-target `settings.json` holds the shared `appSubdomain` plus
`trustedProxyCIDRs`, `highAvailability` and optional `databaseName`; credentials and
kubeconfigs never enter Git. Platform domain, realm and existing target bindings
must match (cluster migration is deliberate). Reruns reject a changed saved
`appSubdomain`, since pinned environments still need their DNS and login callbacks.

- Each Application uses `applications-<env>`, the registered cluster name and
  namespace, and a full Git commit for its runtime source.
- CI offers an `int`/`uat`/`prod` selector (default `int`), updates only that
  Application and verifies destination, images and exact revision after sync.
- `int` may deploy branch snapshots; `uat`/`prod` need finalized releases, enforced
  by runners and admission rules ([delivery](../deployment.md#application-delivery)).
- The final pointer commit carries the [trailers](#required-delivery-behavior) and
  follows the pinned runtime commit; onboarding verifies both and Deployment
  readiness.

One registry credential serves all environments. Data credentials live in central
Vault at `apps/<app>/<env>/{database,redis,kafka}`; the `kubernetes-<env>` auth mount
cannot read other environments or admin paths. Browser clients use `<app>-<env>-web`
in the shared realm, with exact callbacks and matching JWT audiences. Operator flow:
[environment deployment](repository-onboarding.md#environment-deployment).

## Shared application data

| Service | Sharing model |
|---|---|
| PostgreSQL | One service; per-environment databases and logins (`swirl_demo_app_int`, `_uat`, `_prod`). |
| Redis | One cache; environment-prefixed keys and ACL users. |
| Kafka | One cluster; environment-prefixed topics/groups and SCRAM credentials. |

No data service runs on an application target. Redis ACLs isolate values, not
metadata: `KEYS`/`SCAN` (for eviction) list other environments' key names and `INFO`
shows server stats, so key names must not hold secrets or personal data.

Isolation requires authenticated Redis and Kafka. With anonymous Redis clients:

1. Stage a compatibility credential (current access unchanged):

   ```bash
   python3 scripts/configure-application-data.py \
     --config /secure/deployment-environments.yaml --prepare-legacy-auth
   ```

2. Move legacy consumers to Secret `infra/application-redis-legacy-auth`. OAuth2
   Proxy and DBGate read it after rollout; app owners wire it into runtime config.
   Verify clients reconnect. New environments always get restricted users.
3. Prepare the ACL profile (credentials only; no port or listener change). It writes
   the public [profile](../../config/application-data-values.yaml) outside the
   checkout and never overwrites a file. The admin password stays at Vault
   `infra/application-redis`.

   ```bash
   KUBECONFIG=/secure/platform.yaml python3 scripts/configure-application-data.py \
     --config /secure/deployment-environments.yaml \
     --prepare-auth --legacy-clients-migrated \
     --output-values /secure/application-data-values.yaml
   ```

4. Run `PLATFORM_HA_VALUES_FILE=/secure/application-data-values.yaml
   ./install-control-plane.sh`. This does not enable HA; later runs keep data auth
   and reject an implicit downgrade.

The gateway stays closed until `configure-application-data.py --config ... --check`
verifies authenticated Redis and the Kafka remote listeners on every broker.

Onboarding creates fresh databases and never drops, empties or re-owns the existing
production `swirl_demo_app_db`. To keep it: migrate ownership to the app role
(reviewed), store its credentials at `apps/swirl-demo-app/prod/database`, set
`"databaseName": "swirl_demo_app_db"` in the production `settings.json`, and rerun.
The `--adopt-existing-database` check refuses missing ownership or credentials.

## Single-target compatibility (version 1)

One local Application with a repository-local Kustomize directory or Helm chart
(defaults only); the AppProject must permit its repository and destination.
External charts, multi-source Applications and source overrides are unsupported.

## Onboarding declaration

Example: Application `catalog`, Kustomize at `infra/k8s`, CI jobs `verify` and
`release`:

```json
{
  "version": 1,
  "application": "infra/argocd/application.yaml",
  "inputs": [
    {"name": "APP_SUBDOMAIN", "label": "Public subdomain (@ for apex)", "type": "subdomain", "default": "catalog"}
  ],
  "files": [".gitlab-ci.yml", "infra/argocd/application.yaml", "infra/k8s/ingress.yaml", "infra/k8s/kustomization.yaml"],
  "replacements": [
    {"from": "catalog.example.com", "to": "{{APP_HOST}}"},
    {"from": "example-com-tls", "to": "{{TLS_SECRET_NAME}}"},
    {"from": "registry.example.com/team/catalog", "to": "{{REGISTRY_HOST}}/{{GITLAB_PROJECT_PATH}}"}
  ],
  "registry": {"path": "apps/catalog/registry"},
  "vault": [
    {"path": "apps/catalog/runtime", "fields": {"SIGNING_KEY": {"generate": 32, "encoding": "hex"}}}
  ],
  "dns": {"hosts": ["{{APP_HOST}}"]},
  "pipeline": {"variables": {"APP_ONBOARDING": "true"}, "jobs": ["verify", "release"]},
  "readiness": {"deployments": ["catalog"]}
}
```

Image settings must cover manifests and release helpers; hostname changes may touch
redirects, allowed origins and build-time URLs. Test a first render and a
changed-context rerun against the real pipeline.

## Fields

| Field | Contract |
|---|---|
| `version` | `1` or `2`; unknown fields are rejected. |
| `application` | Version 1 deploys this default-branch Application into local `apps`; version 2 uses it as the template. |
| `inputs` | Unique `name` with optional `label`, `type`, `default`, `required`, `secret`. `APP_SUBDOMAIN` derives `APP_HOST`. |
| `files` | Unique, explicit local public files. No symlinks, outside paths, credential files, contract or saved settings. |
| `replacements` | Literal `from` and a `to` template; simultaneous longest-match, reusing prior bindings on reruns. |
| `registry` | Required Vault `path` for verified `read_registry`/`read_repository` credentials, also projected into Argo CD via External Secrets. |
| `vault` | Optional `path` + `fields`; each field has exactly one of `generate`, `value`, `input`. |
| `keycloak` | Optional `{"realm":"{{KEYCLOAK_REALM}}","file":"infra/keycloak/production-client.json"}` for a production public OIDC/PKCE client; list the file in `files` to render it. |
| `bootstrap` | Optional YAML with only `Namespace` (`apps` only), `Role`, `RoleBinding`, `ExternalSecret` in `apps`/`infra`, explicit namespaces. No workloads. |
| `dns` | Unique exact `hosts` in the public zone covering every rendered Ingress host; no wildcards. |
| `pipeline` | `variables` include `APP_ONBOARDING: "true"`; `jobs` lists required automatic jobs. Reserved: `ONBOARDING_EXPECTED_SHA`, `ONBOARDING_RUN_ID`. |
| `readiness` | `deployments` checked in `apps` after the Application is `Synced`/`Healthy`. |

Input types: `string`, `subdomain` (one label or `@`) and `cidrs` (comma-separated
networks). Only `APP_SUBDOMAIN` maps `@` to the apex, so an alias such as
`{{ALIAS}}.{{PUBLIC_DOMAIN}}` needs a real label. Inputs are required unless
`required:false`; defaults may use context values and reruns offer prior choices.

A hidden credential that keeps its stored value uses `secret:true`,
`required:false`, `default:""` and appears only in a Vault field, e.g.
`"API_KEY":{"input":"PROVIDER_API_KEY"}`. Empty input keeps the stored value; a
missing required value stops setup. Secrets never go in files, replacements or
pipeline variables.

Registry/Vault paths stay under `apps/<Application-name>/`. `generate` takes 16-256
random bytes, `hex` or `base64`; `value` is a literal default. Writes are
version-checked and only add missing fields: a different supplied credential fails
rather than rotating, changed declarations never rotate populated values, and
deleted versions need explicit recovery. Invalid registry credentials can be
replaced while prior tokens stay valid.

The Keycloak file must define a public HTTPS client using Authorization Code with
PKCE `S256`, limited to declared hosts; password, implicit and service-account
grants are disabled. The helper verifies realm, ownership and scopes, then
reconciles scopes and group/audience mappers. It never imports demo realms, users or
secrets.

## Public rendering context

| Values | Meaning |
|---|---|
| `PUBLIC_DOMAIN`, `INTERNAL_DNS_ZONE`, `TLS_SECRET_NAME` | Public zone, private service zone, shared TLS Secret. |
| `APP_SUBDOMAIN`, `APP_HOST` | Declared label and computed public hostname. |
| `GITLAB_PROJECT_PATH`, `GITLAB_PROJECT_ID`, `DEFAULT_BRANCH` | Imported destination identity and branch. |
| `GITLAB_PUBLIC_URL`, `GITLAB_INTERNAL_URL`, `GITLAB_REPOSITORY_URL` | Public base, internal API base, internal clone URL (setup port-forward not saved). |
| `REGISTRY_HOST`, `REGISTRY_PUSH_HOST` | Public pull host and internal CI push endpoint. |
| `GITHUB_OWNER`, `GITHUB_REPOSITORY` | Imported GitHub source. |
| `KEYCLOAK_REALM` | Existing realm, from the SSO issuer unless supplied. |
| `POD_CIDR` | Pod network, default `10.42.0.0/16`; set `K3S_CLUSTER_CIDR` if different. |
| `PLATFORM_SECURITY_PROJECT_PATH` | Helper-image project: platform Application repository plus `/security`. |
| `SONAR_PROJECT_KEY` | GitLab project path with `/` as `:`. |

Explicit `PLATFORM_DOMAIN`, `INTERNAL_DNS_ZONE` and `KEYCLOAK_REALM` win; then the
installed `swirl-cloud` Application's Helm parameters, `valuesObject` and inline
values; then CoreDNS and OAuth2 Proxy's issuer. The private zone is never guessed
from the public domain; missing settings stop deployment. If
`PLATFORM_SECURITY_PROJECT_PATH` is used and undiscoverable, interactive setup
prompts and unattended setup requires it.

Inputs add context names but never override platform values.
`infra/onboarding-values.json` stores version `1`, public `context` and `bindings`
(original literal to last rendered value), never secrets; the journal stays outside
Git. Declare `spec.source.targetRevision: HEAD` and no revision replacements.
Rendering writes the real default branch, GitLab URL and `infra` namespace, keeping
the source path/profile; it accepts `HEAD` or the current or saved default branch.
Release jobs must preserve these choices.

## Required delivery behavior

The default-branch pipeline accepts source `api` and runs its declared jobs
automatically with `APP_ONBOARDING=true`. Manual releases are unaffected;
source-analysis follows the [scanner contract](../operations.md#scanner-contract).

Before publishing, require `CI_COMMIT_SHA` to equal a valid `ONBOARDING_EXPECTED_SHA`,
the default branch and source `api`. Reject an advanced branch before changing
desired state. Configuration commits use `[skip ci]`, so the onboarding pipeline
owns first publication. The final GitOps commit (branch tip) must carry:

```text
Onboarding-Pipeline: <CI_PIPELINE_ID>
Onboarding-Source: <CI_COMMIT_SHA>
```

The helper verifies these and source ancestry. Version 1 deploys the tip; version 2
reads its selected Application and verifies the preceding pinned runtime commit.
`ONBOARDING_RUN_ID` identifies the request for recovery after a lost response; never
override or save it.

The release pipeline publishes immutable images, commits their selection and applies
the committed Application (restoring paused auto-sync), then waits for its revision
and health checks. The helper requires every declared job to succeed, Argo CD's exact
revision and Application/Deployment health. Optional reports may stay manual if
unlisted. See [reruns and recovery](repository-onboarding.md#automation-and-reruns).
