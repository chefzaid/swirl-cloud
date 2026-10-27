# External Services

Every outside service the platform contacts, what it receives and whether it is
required. This is the supplier and data-flow inventory for the `supply-chain`
[compliance](compliance-evidence.md) control. Applications list their own
external services in their repositories.

## Required or selected at installation

| Service | Provider (headquarters) | Used for | Data it receives | Avoid or replace |
| --- | --- | --- | --- | --- |
| Cloudflare DNS, proxy, TLS, WAF and Access | Cloudflare, Inc. (United States) | Public DNS, edge TLS, DDoS protection, sign-in gate for administration hosts | The DNS zone; all public HTTP(S) traffic, decrypted at the edge; Access sign-in identities | `SERVER_EXPOSURE=local` installs without it; an edge without Cloudflare is planned (TODO §33) |
| Tailscale coordination | Tailscale Inc. (Canada) | Private node network when `K3S_NODE_TRANSPORT=tailscale` | Node public keys, hostnames, addresses and tailnet membership; traffic stays end-to-end encrypted and may pass Tailscale relays | vRack or LAN transport |
| GitHub | GitHub, Inc., Microsoft (United States) | Optional import and two-way branch and tag mirroring of application repositories | Repository content and history of mirrored projects | Leave repository import unconfigured |
| OVHcloud API | OVH SAS (France) | Optional vRack attachment of new servers | Server and vRack service names | Attach servers manually |
| S3-compatible backup target | Chosen by the operator | Encrypted off-node recovery archives and volume backups | Archives encrypted before upload (restic) | Keep archives on another host you control |
| SMTP relay | Chosen by the operator | Optional [platform mail](platform-mail.md): alerts, Keycloak account emails, GitLab notifications and Odoo mail | Message content and recipient addresses; the relay login | Leave platform mail off, or run your own mail server |
| Cloudflare Workers | Cloudflare, Inc. (United States) | Optional [outage watch](service-levels.md#outage-watch) checking public endpoints from outside the cluster | The public endpoint list, the relay login (as a Worker secret) and the outage emails it sends | Leave the outage watch undeployed |

## Software and update sources

Downloads only; these services learn the requesting address and what was downloaded.

| Source | Provider | Delivers |
| --- | --- | --- |
| `docker.io`, `registry-1.docker.io` | Docker, Inc. (United States) | Most container images |
| `quay.io` | Red Hat (United States) | Keycloak, Prometheus exporters and other images |
| `ghcr.io` | GitHub (United States) | External Secrets, Homepage, Kafbat UI, CloudNativePG, the Grafana dashboard sidecar and Trivy node-collector images; the Trivy vulnerability database |
| `mirror.gcr.io` | Google (United States) | Trivy vulnerability database mirror, tried before `ghcr.io` |
| `registry.k8s.io` | Kubernetes project | kube-state-metrics and Kubernetes images |
| `public.ecr.aws` | Amazon Web Services (United States) | HAProxy images in front of Redis in HA profiles |
| `docker.elastic.co` | Elastic N.V. (Netherlands) | Elasticsearch, Kibana and Logstash images |
| `cr.fluentbit.io` | Fluent Bit project | The Fluent Bit image |
| `get.k3s.io`, GitHub releases | K3s project | K3s installer and binaries |
| Helm chart repositories (`charts.longhorn.io`, `helm.releases.hashicorp.com`, GitHub Pages) | Respective projects | Platform Helm charts at pinned versions |
| Ubuntu archives | Canonical Ltd. (United Kingdom) | Operating-system packages and security updates |
| `install.crowdsec.net`, CrowdSec package repository | CrowdSec SAS (France) | CrowdSec packages on internet-exposed hosts |
| `rdap.org` | RDAP bootstrap service | Registrar and DNSSEC check for the domain name during installation |

Images and charts are pinned by version and digest. An offline installation
bundle is planned (TODO §32).

## Data shared while running

| Service | Provider | Data | Status |
| --- | --- | --- | --- |
| CrowdSec Central API | CrowdSec SAS (France) | Addresses that attacked the host and the matching scenarios, in exchange for the community block list | Active on internet-exposed hosts with CrowdSec's default enrollment; kept deliberately (2026-10-05) for the community block list |

## Vendor telemetry and update checks (switched off)

The platform switches off these defaults so no usage data leaves the
installation:

| Component | Default behaviour | Setting |
| --- | --- | --- |
| GitLab | Service Ping usage data and version checks | `usage_ping_enabled` and `version_check_enabled` set to false by the SSO bootstrap |
| Grafana | Usage reports, update checks, news feed and Gravatar images | `GF_ANALYTICS_*`, `GF_NEWS_NEWS_FEED_ENABLED` and `GF_SECURITY_DISABLE_GRAVATAR` |
| Kibana | Usage telemetry | `--telemetry.optIn=false`, locked for users |
| SonarQube | Anonymous statistics | `SONAR_TELEMETRY_ENABLE=false` |
| Portainer | Anonymous statistics | `EnableTelemetry: false` set by its bootstrap |
| Longhorn | Upgrade checker | `upgradeChecker=false` at installation |
| Traefik | Version check | `global.checkNewVersion` and `sendAnonymousUsage` false |
| Odoo | Daily publisher-warranty report with the database UUID, user counts and installed apps | Its scheduled action is switched off at every Odoo start |

The Elasticsearch GeoIP database downloader only runs when an ingest pipeline
uses a GeoIP processor; none is configured.

## Keeping this record current

Add a row, with what it receives, whenever a change adds an outside dependency
(image registry, API, update check, AI provider). Review the list with the
quarterly access review.

## Related Guides

- [Compliance evidence](compliance-evidence.md)
- [Security](../security.md)
- [DNS and ingress](../dns.md)
