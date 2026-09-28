# Public DNS And Ingress

The platform owns the managed Cloudflare zone, platform hostnames, origin TLS,
the Traefik ingress controller and cluster-only service names. Applications own
their own hostnames, Ingress resources and redirects. Private node transport is
covered in [node enrollment](guides/node-enrollment.md#private-node-network) and
host exposure in [security](security.md#exposure-boundaries).

## Service DNS and registry routing

CoreDNS resolves `<service>.internal.<your-domain>` to the matching Service in
`infra`, preserving additional DNS labels for headless services. Curated aliases route
services such as Longhorn into their own namespaces. Application-owned
services use canonical Kubernetes DNS names. The private zone is cluster-only;
Cloudflare does not publish it, and ordinary host resolvers cannot use it.

K3s/containerd routes `registry.<your-domain>` to the fixed internal Registry
service endpoint. Cluster automation uses internal GitLab/API routes, while
the Dependency Proxy uses canonical `gitlab.<your-domain>` HTTPS. Kubernetes
API clients retain `kubernetes.default.svc`, which matches the server certificate.
Public Docker clients use `registry.<your-domain>` over HTTPS.

The shared HA cache accepts application traffic only through HAProxy on port
6379 from `infra`, `apps` and `corp`. Its Redis and Sentinel ports accept traffic
only from that release's server and HAProxy pods in `infra`. Applications cannot
reach Sentinel management directly; these NetworkPolicies preserve the existing
application cache credential contract.

## Cloudflare

Platform host inventories live in [config/platform.env](../config/platform.env).
The configurator reconciles their proxied DNS, Origin CA TLS, DNSSEC, WAF/cache
rules and Keycloak-backed Access for administration. Application names are not
part of that inventory. Apex DNS publication is disabled by default; set
`CLOUDFLARE_PUBLISH_APEX=true` only when the platform should manage that record.
Applications own their public DNS records, Ingress resources and redirects,
including apex/`www` behavior when applicable.

Homepage is published at `cloud.<your-domain>` and protected by Cloudflare
Access. Its Ingress, allowed hosts and links share that hostname. The retired
`dashboard` and `intranet` labels remain in `DEFAULT_CLOUDFLARE_RETIRED_HOST_LABELS` so Cloudflare
reconciliation removes their old address records and Access applications.

In the default layout, node administration has a separate unproxied hostname
controlled by `CLOUDFLARE_NODE_DNS_LABEL`. HA public-host reconciliation does
not publish node administration records; maintain any required record separately.

```bash
./scripts/configure-cloudflare.sh --zone example.com
```

The script prompts for a scoped Cloudflare **User API Token** and prints the
required permissions. For automation, supply `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCESS_ALLOWED_EMAILS` and `CLOUDFLARE_ACCESS_TEAM_NAME`. Complete
registrar nameserver delegation and DNSSEC prerequisites before unattended
setup; interactive setup pauses with the required registrar values.

Registry clients cannot answer browser bot challenges. Reconciliation disables
basic Bot Fight Mode, which has no hostname exceptions, and skips Super Bot
Fight Mode only for the Registry hostname. Other WAF, rate-limit and TLS
controls remain enabled. The token therefore needs **Bot Management Read** and
**Edit**, in addition to the other permissions printed by the configurator.

### Application DNS ownership

Each application declares its own DNS records and owns their lifecycle.
[`add-repos.sh`](guides/repository-onboarding.md) can provision or reconcile those exact
hosts from `infra/onboarding.json`, without adding names to this repository.
Direct ingress uses proxied A records targeting its unique public IPv4.
Changing a hostname does not delete the old record; retire old hosts explicitly
after verifying the replacement route.

After HA activation, use proxied CNAME records targeting
`<publishedTunnelID>.cfargotunnel.com`. The platform publishes its nonsecret
endpoint state in `infra/swirl-cloud-public-ingress`:

```bash
kubectl -n infra get configmap swirl-cloud-public-ingress -o json
```

An application must wait until `data.mode=tunnel`, `data.publishedTunnelID` is
nonempty and equals `data.tunnelID`, and `data.domain` matches its zone before
switching DNS. A prepared tunnel is not an activated endpoint. Repository
onboarding checks these conditions before publication; rerun it for the app's
DNS cutover after platform HA activation. Conflicting address records or another
application's Ingress ownership stop reconciliation. Unrelated MX/TXT records
are preserved. App-owned checks still verify the public route and failover.

The tunnel accepts the configured zone apex and wildcard subdomains and forwards
them to local Traefik; Kubernetes Ingress resources select the application. This
uses Cloudflare's [hostname wildcard matching](https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/#wildcards).
It creates no wildcard DNS record and grants no application deployment ownership
to the platform. A hostname without a matching Ingress receives Traefik's default
404 response. Other public zones require a separate ingress and DNS arrangement.

### Ingress controller

The platform installs Traefik through its own pinned Helm release, with K3s
bundled Traefik disabled. Native Kubernetes Ingress preserves application-owned
hosts, DNS/Homepage discovery and TLS secrets. Same-namespace Middleware adds
authentication and request limits; Thoughty uses native weighted routes for its
canary. This keeps the existing ownership model without another routing API
migration. See [controller migration](guides/platform-migration.md) for existing clusters.

Only HTTP/HTTPS entry points receive public application routers. The shared
header Middleware removes RFC `Forwarded`, `X-Real-IP` and forwarded routing
overrides. Preserve Traefik's default appended `X-Forwarded-For` chain: app rate
limiters validate the trusted proxy boundary and take the visitor immediately
before the edge, or the single sanitized address for a direct client.

For the platform host inventory, the Cloudflare configurator removes incoming
`X-Forwarded-For` through a request-header transform. Cloudflare then supplies
the visitor address itself, as documented in its [header transform behavior](https://developers.cloudflare.com/rules/transform/request-header-modification/#important-remarks).
This prevents stock services from trusting a visitor-supplied leading address;
app rate limiters also validate the chain locally. Verify the rule at the live
edge during cutover, especially if Workers or additional proxies handle a host.

The Traefik release publishes its trusted edge ranges in
`infra/ingress-proxy-trust`. GitLab uses those ranges with its private proxy trust
in both Rails and its bundled NGINX. NGINX resolves the visitor recursively from
`X-Forwarded-For` before forwarding that address to Workhorse, preserving the
visitor identity for audit logs and rate limits.
After changing ingress mode or edge ranges, restart GitLab in a maintenance
window to reload that environment input. A GitLab installation without ingress
uses private proxy defaults. The token needs Zone **Transform Rules Edit** in
addition to the listed DNS/security permissions.

### HA public ingress

The [HA ingress profile](../config/traefik-ha-values.yaml) runs Traefik as a
DaemonSet on control planes, with one co-located `cloudflared` container per
pod. Every connector uses the same tunnel identity and connects outbound to
Cloudflare. Traefik uses normal pod networking and an internal Service; additional
hosts need no inbound public web listener or shared public IP.

The connector sends HTTPS to its own Traefik listener over loopback, verifies the
Cloudflare Origin CA certificate against the public domain, and preserves the
request Host. Traefik trusts the forwarded client-address chain only from that loopback peer.
A shared Middleware clears forwarded routing headers and derives host, URI and
method from the request; the public scheme is HTTPS.
Application proxy and NetworkPolicy contracts still see the Traefik pod network.
Keycloak/OIDC URLs, Access protection and Registry authentication retain their
public hostnames.

The connector's liveness check withdraws it when its local Traefik stops serving;
its readiness check requires a connected tunnel. Before changing public DNS to
the tunnel CNAME, the configurator checks connectors on at least three distinct
Ready control planes and performs verified HTTPS origin probes. The API token
also needs account-level **Cloudflare Tunnel Edit** permission. Use the ordered
[HA migration](guides/high-availability.md#activate-in-order); allow a maintenance
window for replacing the direct ingress path.

Tunnel replicas provide connection redundancy; they do not promise even
traffic distribution or preserve an interrupted client connection. See
[Cloudflare's replica and load-balancer distinction](https://developers.cloudflare.com/tunnel/routing/#replicas-versus-load-balancers).
Public uploads still pass through Cloudflare's request limits. Verify Registry
push/pull and application login through the tunnel before relying on failover.

## Related Guides

- [Architecture](architecture.md#node-topology)
- [Deployment](deployment.md)
- [Security](security.md)
- [High availability](guides/high-availability.md)
- [Repository onboarding](guides/repository-onboarding.md)
