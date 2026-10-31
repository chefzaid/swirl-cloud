# Public DNS And Ingress

The platform owns the managed Cloudflare zone, platform hostnames, origin TLS, the
Traefik ingress controller and cluster-only service names. Applications own their
hostnames, Ingress resources and redirects. See also
[private node transport](guides/node-enrollment.md#private-node-network) and
[host exposure](security.md#exposure-boundaries).

## Service DNS and registry routing

CoreDNS resolves `<service>.internal.<your-domain>` to the matching Service in
`infra`, keeping extra labels for headless services; curated aliases route services
such as Longhorn to their own namespaces. The zone is cluster-only: Cloudflare does
not publish it and host resolvers cannot use it. Application services use canonical
Kubernetes DNS names.

K3s/containerd maps `registry.<your-domain>` to the fixed internal Registry Service
endpoint, and cluster automation uses internal GitLab and API routes. The Dependency
Proxy uses canonical `gitlab.<your-domain>` HTTPS, Kubernetes API clients keep
`kubernetes.default.svc` (it matches the server certificate), and public Docker
clients use `registry.<your-domain>` over HTTPS.

The shared HA Redis cache accepts application traffic only through HAProxy on port
6379 from `infra`, `apps` and `corp`; its Redis and Sentinel ports accept only that
release's server and HAProxy pods, so applications cannot reach Sentinel management.

## Cloudflare

For the platform host inventories in [config/platform.env](../config/platform.env)
(`DEFAULT_CLOUDFLARE_*`), the configurator reconciles proxied DNS, Origin CA TLS,
DNSSEC, WAF and cache rules, and Keycloak-backed Access for administration. It also
enables Smart Tiered Cache zone-wide. Apex DNS is off unless
`CLOUDFLARE_PUBLISH_APEX=true`. Applications own their DNS records, redirects
(including apex/`www`), their cache rules in the zone's cache ruleset and their
purges; the configurator reconciles only its own rule there.

Homepage is at `cloud.<your-domain>` behind Access. Node administration has a separate
unproxied hostname, `CLOUDFLARE_NODE_DNS_LABEL` (default `node-01`); HA
reconciliation does not publish it, so maintain any needed record yourself.

```bash
./scripts/configure-cloudflare.sh --zone example.com
```

The script asks for a scoped Cloudflare **User API Token** (`cfut_...`; Account
tokens and the Global API Key are rejected) and prints the permissions it needs. For
automation, supply `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCESS_ALLOWED_EMAILS` and
`CLOUDFLARE_ACCESS_TEAM_NAME`, and finish registrar nameserver delegation and DNSSEC
first; interactive setup pauses with the registrar values. Some features need more:

| Feature | Extra token permission |
| --- | --- |
| [HA public ingress](#ha-public-ingress) | Account **Cloudflare Tunnel Edit** |
| [Outage watch](guides/service-levels.md#outage-watch) | Account **Workers Scripts Edit** |
| [Website chat Email Worker](guides/odoo-website.md) | **Workers Scripts Edit** and **Email Routing Rules Edit** |

Registry clients cannot answer browser bot challenges, so reconciliation disables
basic Bot Fight Mode (it has no hostname exceptions) and skips Super Bot Fight Mode
only for the Registry hostname. Other WAF, rate-limit and TLS controls stay on.

### Origin behind NAT

On a LAN, Traefik's ServiceLB address is private, so the configurator refuses to
publish it. Forward TCP 80 and 443 on the router to the first control plane's LAN
address and pass the router's public IPv4 (`--origin-ip` or `CLOUDFLARE_ORIGIN_IP`,
which the installer forwards):

```bash
CLOUDFLARE_ORIGIN_IP=203.0.113.25 ./scripts/configure-cloudflare.sh --zone example.com
```

Forwarding keeps the client address, so the Cloudflare-only origin firewall still
applies. Rerun the configurator when a dynamic public address changes. `local`
exposure needs none of this.

### Application DNS ownership

Each application declares and owns its DNS records.
[`add-repos.sh`](guides/repository-onboarding.md) reconciles exactly those hosts from
`infra/onboarding.json`, adding no names to this repository. Direct ingress uses
proxied A records to the unique public IPv4. A renamed host's old record is not
deleted; retire it after verifying the new route.

After HA activation, use proxied CNAMEs to `<publishedTunnelID>.cfargotunnel.com`.
The platform publishes its nonsecret endpoint state:

```bash
kubectl -n infra get configmap swirl-cloud-public-ingress -o json
```

Switch DNS only when `data.mode=tunnel`, `data.publishedTunnelID` is nonempty and
equals `data.tunnelID`, and `data.domain` matches the zone; a prepared tunnel is not
active. Repository onboarding checks this; rerun it for each app's cutover after
platform HA activation. Conflicting address records or another application's
Ingress stop reconciliation; unrelated MX/TXT records are kept. App-owned checks
still verify the route and failover.

The tunnel accepts the zone apex and all subdomains
([hostname wildcard matching](https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/#wildcards))
and forwards them to local Traefik, where Ingress resources pick the application.
It creates no wildcard DNS record and no platform ownership of applications. A host
without a matching Ingress gets Traefik's default 404. Other public zones need their
own ingress and DNS arrangement.

### Ingress controller

The platform installs Traefik as its own pinned Helm release, with K3s's bundled
Traefik disabled. Native Kubernetes Ingress keeps application-owned hosts,
DNS/Homepage discovery and TLS secrets; same-namespace Middleware adds
authentication and request limits, and Thoughty's canary uses native weighted
routes.

Client addresses:

- Only HTTP/HTTPS entry points get public application routers. The shared header
  Middleware removes RFC `Forwarded`, `X-Real-IP` and forwarded routing overrides
  but keeps Traefik's appended `X-Forwarded-For` chain, from which app rate limiters
  take the address just before the edge (or a direct client's single address).
- For platform hosts, a Cloudflare request-header transform strips incoming
  `X-Forwarded-For` and Cloudflare sets the visitor address
  ([transform behavior](https://developers.cloudflare.com/rules/transform/request-header-modification/#important-remarks)).
  Verify it at the live edge during cutover, especially behind Workers or extra
  proxies.
- Traefik publishes its trusted edge ranges in `infra/ingress-proxy-trust`. GitLab's
  Rails and bundled NGINX combine them with its private proxy trust, and NGINX
  passes the resolved visitor to Workhorse for audit logs and rate limits. Restart
  GitLab in a maintenance window after changing ingress mode or edge ranges;
  without ingress it uses private proxy defaults.

The HTTPS entry point repeats the zone's header policy on routed responses, replacing
any application value: HSTS with `includeSubDomains` and no preload, and
`X-Content-Type-Options: nosniff`.

- `configure-ingress.sh` takes the max-age from `CLOUDFLARE_HSTS_MAX_AGE` (default
  in `config/platform.env`) and drops HSTS when `CLOUDFLARE_ENABLE_HSTS=false`; use
  the same values as the Cloudflare configurator. With neither set, a rerun keeps
  the deployed value.
- A local installation (`SERVER_EXPOSURE=local` without HA) has no edge and gets
  only `nosniff`.
- Remote application clusters get the policy at
  [registration](deployment.md#register-an-environment); rerun it for each existing
  remote environment.
- The HTTP entry point only redirects, and cluster-only internal names bypass
  Traefik. Applications own framing and Content Security Policy (the website frames
  Odoo's chat page).

Argo CD does not manage Traefik: changes apply when `./scripts/configure-ingress.sh`
(or the installer or Ansible ingress step) runs again. Verify on a host whose
application sends no HSTS itself, such as Grafana (Keycloak does):

```bash
kubectl -n infra get middleware security-headers
kubectl -n infra port-forward svc/traefik 8443:443 &
curl -sSI --cacert config/cloudflare-origin-ca.pem \
  --connect-to grafana.example.com:443:127.0.0.1:8443 https://grafana.example.com/login |
  grep -iE '^(strict-transport-security|x-content-type-options):'
```

### HA public ingress

The [HA ingress profile](../config/traefik-ha-values.yaml) runs Traefik as a
DaemonSet on control planes with a `cloudflared` container per pod. All connectors
share one tunnel identity and connect outbound, so hosts need no inbound public web
listener or shared IP; Traefik uses pod networking and an internal Service.

Each connector sends HTTPS to its own Traefik over loopback, verifies the Origin CA
certificate against the public domain and keeps the Host header. Traefik trusts the
forwarded address chain only from that loopback peer; a shared Middleware clears
forwarded routing headers and the public scheme is HTTPS. Application proxy and
NetworkPolicy contracts still see the Traefik pod network, and Keycloak/OIDC URLs,
Access and Registry authentication keep their public hostnames.

Liveness withdraws a connector when its Traefik stops serving; readiness requires a
connected tunnel. Before pointing public DNS at the tunnel, the configurator requires
Ready connectors on at least three distinct control planes and verified HTTPS origin
probes. Follow the ordered [HA migration](guides/high-availability.md#activate-in-order)
in a maintenance window.

Replicas give connection redundancy, not even load distribution, and do not keep an
interrupted connection
([replicas versus load balancers](https://developers.cloudflare.com/tunnel/routing/#replicas-versus-load-balancers)).
Uploads still pass Cloudflare's request limits. Verify Registry push/pull and
application login through the tunnel before relying on failover.

## Related Guides

[Architecture](architecture.md#node-topology), [Deployment](deployment.md),
[Security](security.md), [high availability](guides/high-availability.md) and
[repository onboarding](guides/repository-onboarding.md).
