# ADR 0007: Scale Host Security Controls With Node Exposure

- Status: Accepted
- Date: 2026-03-08
- Amended: 2026-05-21, 2026-06-10, 2026-09-09

## Context

The bootstrap server has a public IP and receives continuous SSH brute-force
traffic. Later workers and extra control planes sit on a private network. A single
security profile would either leave the public host under-protected or load
private hosts with unnecessary agents.

## Decision

Apply one shared script, [`configure-node-security.sh`](../../scripts/configure-node-security.sh),
with a role and exposure:

| Node | Controls |
| --- | --- |
| Internet-facing control plane | UFW, Fail2ban (escalating bans), CrowdSec with firewall bouncer and slow brute-force scenarios, SSH hardening, Lynis |
| Private control plane | UFW, Lynis |
| Worker | UFW default deny, SSH only from control-plane private IPs, forwarding guard |

Publish the policy as `infra/host-security-config`. Lock public HTTP/HTTPS to
Cloudflare ranges. Enforce an AppArmor profile for containerd. Prove the private
SSH path before applying any firewall change.

## Rationale

Intrusion prevention belongs where attackers can reach; private nodes rely on
network isolation. Proving the SSH source first prevents operators from locking
themselves out of remote hosts.

## Consequences

Password SSH remains enabled on internet-facing control planes and relies on
Fail2ban/CrowdSec. The original `todo.txt` items for auditd, malware scanning,
EDR and an HTTP IDS are not implemented; they are open items in [TODO.md](../../TODO.md).
Worker Lynis audits run on demand through `swirl-cloud-audit-nodes`.
