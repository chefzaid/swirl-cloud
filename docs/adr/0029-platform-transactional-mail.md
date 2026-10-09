# ADR 0029: Send Platform Email Through One Operator-Supplied SMTP Relay

- Status: Accepted
- Date: 2026-10-09

## Context

The platform could not send email. Keycloak offered "Forgot password?" without
a mail server, alerts reached only GitLab's alert list, and Odoo had no
outgoing server unless a website integration supplied one. Mailboxes and the
directory are a separate, open decision (Microsoft 365 or self-hosted); sending
transactional mail must not wait for it. Installations use different mail
providers, so the source cannot name one.

## Decision

- The operator supplies one SMTP relay at installation: host, port, TLS mode
  (implicit TLS or STARTTLS), username, password, sender address and name, and
  the alert recipients. `scripts/configure-platform-mail.py` checks the login,
  then stores the values in Vault `infra/smtp`. `configure-vault.sh` seeds that
  path disabled, so platform mail stays off until an operator configures it.
- External Secrets projects `infra/smtp` into `platform-mail` Secrets in
  `infra` and `corp`. Each service reads its settings at start:
  - **Alertmanager:** External Secrets renders its whole configuration. While
    mail is enabled, critical and warning alerts are also emailed, grouped by
    alert name and repeated daily; GitLab keeps receiving every alert.
  - **Keycloak:** the realm reconciliation sets the realm mail server, so
    password reset and verification emails work.
  - **GitLab:** Omnibus reads the settings from the environment.
  - **Odoo:** the bootstrap keeps a "Platform mail" outgoing server for the
    sender's domain, after any more specific server (such as a website's).
- Grafana gets no mail server: users sign in through Keycloak and alerts go
  through Alertmanager, so Grafana has nothing to send. Argo CD
  notifications and mail for applications are separate follow-ups.

## Rationale

SMTP with a login is the one interface every consumer supports. Keeping the
values in Vault rather than Helm values keeps the password out of Git and lets
the operator change providers without a commit. Rendering Alertmanager's file
through External Secrets keeps the routing in Git while the credentials stay in
Vault. A seeded, disabled record means every ExternalSecret syncs on every
installation, with or without mail.

## Consequences

The relay is an external service that receives alert text, account emails and
Odoo messages ([external services](../guides/external-services.md)). Its
sending limits are shared with anything else using the same account. A changed
setting reaches Alertmanager and Keycloak through the configurator; GitLab and
Odoo read it only when they restart. Alert email stops when the cluster or the
relay is down, so the outage check in TODO §14 is still needed.
