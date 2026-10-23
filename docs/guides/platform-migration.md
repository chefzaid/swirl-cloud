# Migrating The Platform To Traefik And Upstream Images

Chart 2 replaces retired ingress-nginx with native Traefik Ingress, and privately
rebuilt platform images with pinned public vendor images. Existing installations
migrate once, in a maintenance window spanning `swirl-cloud`, Swirl Demo App,
Indezy, Thoughty and Swirl Website. New installations skip this guide; topology
and database HA changes are separate.

The installer and Ansible run the read-only `scripts/check-platform-migration.sh`,
which stops while `infra` runs `ingress-nginx-controller` or `/security/` images;
the chart refuses old image profiles. Neither replaces pausing automated sync.

## Prepare and rehearse

1. Save to a private recovery directory: source revisions, root and app
   Application specs, Helm values and versions, image digests, volume ownership and
   ingress/DNS mode. Pause automated sync for the root and all four apps before
   publishing new revisions; keep it paused through validation (the root
   Application manages its own sync settings).
2. Verify off-host K3s, Vault, GitLab and database backups; keep the exact old
   images. Restore copies into disposable infrastructure and check PostgreSQL
   collations, extensions and globals, MongoDB auth and data, Vault unseal and Raft,
   GitLab repositories and secrets, and Keycloak/SonarQube database access.
   [Image maintenance](../operations.md#runtime-and-recovery-constraints) covers
   runtime identity changes. Stop writers before the final backup and ownership
   changes; never run old and new writers against one volume.
3. Render with the existing identity and HA values
   ([installation inputs](../deployment.md#organization-and-installation-identity)),
   keeping identity, Odoo scope and HA migration settings. Review the root
   Application and every app's manifests. Rehearse revisions, routing and data
   restore on an isolated K3s cluster.

## Prepare routing without moving the public endpoint

Install Traefik and its CRDs before any new Middleware or app routes:

```bash
INGRESS_MIGRATION_CANDIDATE=true INGRESS_VALUES_FILE=rendered/config/traefik-values.yaml \
  ./scripts/configure-ingress.sh
```

With the existing identity, this installs a ClusterIP candidate, even on HA: no
Tunnel sidecar, no DNS, not the default IngressClass. Probe it by port-forward with
renamed temporary copies of the new routes and policies. It refuses to replace an
existing Traefik release; remove only a verified disposable candidate to repeat.

Leave the old Ingress objects and ingress-allow NetworkPolicies unchanged while
probing. The controllers use different pod selectors, so new app policies alone can
cut off the old controller; NGINX admission can reject duplicate host/path routes;
and a ClusterIP candidate has no address for Argo CD's Ingress health check. Keep
sync and deployment jobs paused until cutover, change routes and policies together,
and keep the old manifests for rollback.

Verify HTTPS with the real origin certificate and SNI, HTTP redirects, OAuth login
and return URL, identity headers, app request limits, WebSockets, Sonar scanner API
access, GitLab Registry push/pull and Thoughty's canary rules. Middleware is
same-namespace; each app owns its authentication and request limits.

## Cut over in a maintenance window

1. With sync paused, remove the retired system image-override admission policies
   and bindings from the saved inventory, or they rewrite new Pods to private
   images. Keep the workload-hardening policies, and the old image credentials until
   all consumers migrate.
2. Reconcile stateful services one at a time with the reviewed public image and
   runtime settings, including Vault and Argo CD Helm values. Verify readiness, data
   and authentication after each. App PostgreSQL helper images change in their own
   repositories.
3. Apply the new Ingress, Middleware and NetworkPolicy resources together, then
   remove the ingress-nginx Helm release (freeing direct-mode host ports or stopping
   old HA connectors). Run `./scripts/configure-ingress.sh` without the candidate
   flag to promote Traefik to the existing direct or HA mode. For HA, this starts
   co-located connectors on the existing tunnel; run the Cloudflare configurator's
   origin/connector checks before DNS publication, reconcile its platform-host
   client-IP transform and verify the address GitLab and Keycloak see. GitLab reads
   Traefik's `ingress-proxy-trust` ConfigMap at startup: restart it after any
   edge-range or ingress-mode change. Keep public hostnames unchanged.
4. Replace the root Application's **entire Helm configuration** with the rendered
   one, keeping the reviewed identity and HA inputs. Remove
   `profiles/security-images-*.values`, `securityImagesEnabled`, private Trivy
   registry/repository parameters and ad hoc image overrides from parameters,
   inline values and `valuesObject` (also unset `SECURITY_IMAGES_ENABLED` /
   `security_images_enabled`). Changing only the image profile is not a migration.
   Keep `spec.syncPolicy.automated.enabled=false` until verified. Set
   `PLATFORM_MIGRATION_APPROVED=true` (Ansible: `platform_migration_approved=true`)
   only for this run; it neither bypasses the ingress release guard nor accepts
   retired inputs.
5. Check all routes, jobs, PVCs, image digests, authentication and fresh
   vulnerability reports, then re-enable automated sync. After the recovery window,
   remove overlap resources, obsolete private registry ExternalSecrets and cache
   workloads, and their credentials. Keep recovery archives off-host.

## Roll back

Pause reconciliation and writers. Restore the recorded source revisions,
Application Helm settings, ingress release and network policies as one set. If a
service changed its on-disk format or schema, also restore its data and matching
secrets; an older image cannot undo a database migration. Restore the previous
direct endpoint or HA connector once its routes are ready, and verify data and
external behavior before reopening traffic.

Background: [ingress-nginx retirement](https://kubernetes.io/blog/2026/04/22/kubernetes-v1-36-release/#ingress-nginx-retirement),
Traefik's [native Ingress provider](https://doc.traefik.io/traefik/reference/install-configuration/providers/kubernetes/kubernetes-ingress/)
and [ForwardAuth](https://doc.traefik.io/traefik/reference/routing-configuration/http/middlewares/forwardauth/).
Traefik matches path segments strictly and forwards WebSockets automatically. Its
global 900-second connection/request deadlines and app response-header limits do
not match NGINX idle read/send timers; test long uploads and streaming, including
against the edge provider's limits.
