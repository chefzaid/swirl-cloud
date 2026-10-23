# Platform Mail

The platform sends email through one SMTP relay you choose: your mail provider's
submission server or a transactional email service. It is off until configured
([ADR 0029](../adr/0029-platform-transactional-mail.md)).

| Service | Sends |
| --- | --- |
| Alertmanager | Critical and warning alerts and their resolution. Paused since 2026-10-11 (see [Alert email](#alert-email)). |
| Keycloak | Password reset and email verification for the organization realm. |
| GitLab | Notifications such as failed pipelines, merge requests and invitations. |
| Odoo | Quotes, invoices and other messages through its "Platform mail" outgoing server. |

Grafana has no mail server: users sign in through Keycloak and alerts go through
Alertmanager.

## Choose a relay

The relay needs a login, TLS (implicit TLS, usually port 465, or STARTTLS, usually
587) and must accept any sender in the sender's domain, because Odoo sends as its
users' addresses. Verify that domain at the provider (SPF and DKIM) to avoid spam
filtering.

The relay sees alert text, account emails and Odoo messages; list it among your
[external services](external-services.md). Its sending limits are shared with
anything else on the same account, such as a website contact form.

## Configure

During installation, answer the **Email** section of the setup hub or set these
inputs. The installer configures the relay right after Vault starts, so every
service starts with it.

| Input | Meaning |
| --- | --- |
| `CONFIGURE_PLATFORM_MAIL` | `true` to configure the relay. |
| `PLATFORM_MAIL_HOST` | Relay host, such as `smtp.example.com`. |
| `PLATFORM_MAIL_TLS` | `ssl` (implicit TLS, default) or `starttls`. |
| `PLATFORM_MAIL_PORT` | Default 465 for `ssl`, 587 for `starttls`. |
| `PLATFORM_MAIL_USERNAME`, `PLATFORM_MAIL_PASSWORD` | Relay login. The password can come from a protected file (`PLATFORM_MAIL_PASSWORD_FILE`). |
| `PLATFORM_MAIL_FROM` | Sender address; default `cloud@<public domain>`. |
| `PLATFORM_MAIL_FROM_NAME` | Sender name; default `<organization> Cloud`. |
| `PLATFORM_MAIL_ALERT_TO` | Comma-separated alert recipients; default the platform administrator's email. |

On an installed platform, run the configurator on a control plane. The password
comes from stdin (`--password-stdin`) or an owner-only file (`--password-file`),
never the command line:

```bash
printf '%s\n' "$RELAY_PASSWORD" | scripts/configure-platform-mail.py \
  --host smtp.example.com --username apikey --password-stdin \
  --from cloud@example.com --alert-to ops@example.com --test-to you@example.com
```

Flags mirror the inputs (`--port`, `--tls`, `--from-name`). After checking the
relay's certificate, login and sender, it:

1. stores the settings in Vault `infra/smtp` (no further change if identical);
2. sends the test message to `--test-to`;
3. refreshes the `platform-mail` and `alertmanager-config` Secrets from Vault;
4. validates the Alertmanager configuration with `amtool` and restarts Alertmanager;
5. syncs the platform Application, whose Keycloak reconciliation sets the realm's
   mail server;
6. lists GitLab and Odoo, which read the settings at start. Restart them in a quiet
   period (GitLab is down for several minutes) or pass `--restart-services`.

`--refresh` repeats steps 3 to 6 when Vault is unchanged, e.g. after an interrupted
run.

## Alert email

Alert notifications are paused since 2026-10-11: Alertmanager routes every alert to
its `paused` receiver, which sends nothing, so neither GitLab nor email receives
alerts. The `gitlab` and `email` receivers stay defined; restoring their routes
needs the owner's approval ([TODO](../../TODO.md)).

Once restored, every alert reaches GitLab's **Monitor → Alerts**. With platform mail
on, `critical` and `warning` alerts are also emailed: one message per alert name,
sent a minute after the first alert, batched every 15 minutes while it changes,
repeated daily while it fires, then a resolution message.
`AlertNotificationsFailing` fires when every notification through one receiver
failed for an hour; it still reaches the other receiver. Alertmanager stops with the
cluster, so the [outage watch](service-levels.md#outage-watch) checks public
endpoints from outside and mails through the same relay.

## Verify

- **Relay:** `--test-to` sends a message with the stored settings.
- **Alerts** (only once alert routes are restored): post a test alert that ends after
  five minutes. The email arrives about a minute later, the resolution within 15
  minutes.

  ```bash
  kubectl -n infra exec deploy/alertmanager -- amtool alert add PlatformMailTest \
    severity=warning --annotation='summary="Platform mail test"' \
    --end="$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)" \
    --alertmanager.url=http://127.0.0.1:9093
  ```

- **Keycloak:** **Forgot password?** on the sign-in page, or **Test connection**
  under **Realm settings → Email** (mails the signed-in administrator, who needs an
  email address).
- **GitLab:**

  ```bash
  kubectl -n infra exec deploy/gitlab -- gitlab-rails runner \
    "Notify.test_email('you@example.com', 'GitLab mail test', 'GitLab can send email.').deliver_now"
  ```

- **Odoo:** **Settings → Technical → Outgoing Mail Servers → Platform mail → Test
  Connection**. A more specific server, such as a website's, keeps precedence for
  its sender domain.

## Change or turn off

To rotate the password or change provider, rerun the configurator with the new
values, then restart GitLab and Odoo. To turn mail off:

```bash
scripts/configure-platform-mail.py --disable
```

This removes the stored password, clears the Keycloak realm's mail server and leaves
Alertmanager without an email target. GitLab stops sending and Odoo archives its
"Platform mail" server at their next restart. Revoke the password or key at the
provider too.

## Where the password lives

Vault `infra/smtp` is the source. External Secrets copies it into the
`platform-mail` Secrets in `infra` and `corp` and the rendered `alertmanager-config`
Secret; the [outage watch](service-levels.md#outage-watch) holds it as a Cloudflare
Worker secret. Keycloak stores it in realm settings, GitLab in its generated
configuration on the `gitlab-data` volume and Odoo in its database, so the nightly
archive holds it in the Vault snapshot and database dumps. If any copy is exposed,
rotate it at the provider and rerun the configurator.

## Troubleshooting

| Message | Cause |
| --- | --- |
| `refused the username or password` | Wrong login, a revoked key, or a key without sending permission. |
| `could not be reached` | Wrong host or port, or outbound traffic to the relay is blocked. |
| `certificate check failed` | The host name does not match the relay's certificate, or `ssl` was used on a STARTTLS port. |
| `does not offer STARTTLS` | Use `--tls ssl` or the provider's STARTTLS port. |
| `refused ... as the sender` | Verify the sender's domain at the provider or use an allowed address. |
| No alert email | Alert notifications are [paused](#alert-email). Once restored: check the severity (only `critical` and `warning`), Alertmanager's logs and [AlertNotificationsFailing](alert-runbooks.md#alertnotificationsfailing). |

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [External services](external-services.md)
- [Security reference](../security.md)
- [Operations runbook](../operations.md)
