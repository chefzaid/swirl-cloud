# Platform Mail

The platform sends email through one SMTP relay that you choose: your mail
provider's submission server or a transactional email service. Platform mail is
off until you configure it; the decision is recorded in
[ADR 0029](../adr/0029-platform-transactional-mail.md).

| Service | Sends |
| --- | --- |
| Alertmanager | Critical and warning alerts, and their resolution, to the alert recipients. GitLab's alert list keeps receiving every alert. |
| Keycloak | Password reset and email verification messages for the organization realm. |
| GitLab | Notifications such as failed pipelines, merge requests and invitations. |
| Odoo | Quotes, invoices and other messages through its "Platform mail" outgoing server. |

Grafana has no mail server: users sign in through Keycloak and alerts go
through Alertmanager, so Grafana has nothing to send.

## Choose a relay

The relay needs a login, TLS (implicit TLS, usually port 465, or STARTTLS,
usually port 587) and must accept the sender address. Verify the sender's
domain at the provider (SPF and DKIM records) so that messages are not marked
as spam. Odoo sends as its users' addresses within the sender's domain, so the
provider must accept any sender in that domain, as transactional email
services do once the domain is verified.

The relay receives alert text, account emails and Odoo messages; list it among
your [external services](external-services.md). Its sending limits are shared
with anything else that uses the same account, such as a website's contact
form. Alert email is grouped and repeated once a day to stay well below them.

## Configure

During installation, answer the **Email** section of the setup hub or set the
inputs below; the installer configures the relay after Vault starts, so every
service starts with it.

| Input | Meaning |
| --- | --- |
| `CONFIGURE_PLATFORM_MAIL` | `true` to configure the relay. |
| `PLATFORM_MAIL_HOST` | Relay host name, such as `smtp.example.com`. |
| `PLATFORM_MAIL_TLS` | `ssl` (implicit TLS, default) or `starttls`. |
| `PLATFORM_MAIL_PORT` | Defaults to 465 for `ssl` and 587 for `starttls`. |
| `PLATFORM_MAIL_USERNAME`, `PLATFORM_MAIL_PASSWORD` | The relay login. The password can come from a protected file (`PLATFORM_MAIL_PASSWORD_FILE`). |
| `PLATFORM_MAIL_FROM` | Sender address; defaults to `cloud@<public domain>`. |
| `PLATFORM_MAIL_FROM_NAME` | Sender name; defaults to `<organization> Cloud`. |
| `PLATFORM_MAIL_ALERT_TO` | Alert recipients, comma-separated; defaults to the platform administrator's email. |

On an installed platform, run the configurator on a control plane. It reads the
password from stdin or from a file only you can read, never from the command
line:

```bash
printf '%s\n' "$RELAY_PASSWORD" | scripts/configure-platform-mail.py \
  --host smtp.example.com --username apikey --password-stdin \
  --from cloud@example.com --alert-to ops@example.com --test-to you@example.com
```

It checks the relay's certificate, login and sender before storing anything,
then:

1. stores the settings in Vault `infra/smtp` (nothing else changes when they are
   the same as before);
2. sends the test message to `--test-to`;
3. refreshes the `platform-mail` and `alertmanager-config` Secrets from Vault;
4. checks the rendered Alertmanager configuration with `amtool` and restarts
   Alertmanager;
5. syncs the platform Application, whose Keycloak reconciliation sets the realm's
   mail server;
6. lists GitLab and Odoo, which read the settings when they start. Restart them
   in a quiet period (GitLab is unavailable for several minutes), or pass
   `--restart-services` to restart them now.

`--refresh` repeats steps 3 to 6 when Vault is unchanged, for example after an
interrupted run.

## Alert email

Every alert reaches GitLab's **Monitor → Alerts** as before. While platform mail
is on, alerts with severity `critical` or `warning` are also emailed: one
message per alert name, sent a minute after the first alert, batched every 15
minutes while it changes, repeated once a day while it fires, and followed by a
resolution message. `AlertNotificationsFailing` fires when every notification
through one receiver failed for an hour; it still reaches the other receiver.

## Verify

- **Relay:** `--test-to` sends a message through the stored settings.
- **Alerts:** post a test alert that ends after five minutes; the email arrives
  about a minute later and the resolution message within 15 minutes:

  ```bash
  kubectl -n infra exec deploy/alertmanager -- amtool alert add PlatformMailTest \
    severity=warning --annotation='summary="Platform mail test"' \
    --end="$(date -u -d '+5 minutes' +%Y-%m-%dT%H:%M:%SZ)" \
    --alertmanager.url=http://127.0.0.1:9093
  ```

- **Keycloak:** choose **Forgot password?** on the sign-in page, or **Test
  connection** under **Realm settings → Email** in the administration console
  (it mails the signed-in administrator, who needs an email address).
- **GitLab:**

  ```bash
  kubectl -n infra exec deploy/gitlab -- gitlab-rails runner \
    "Notify.test_email('you@example.com', 'GitLab mail test', 'GitLab can send email.').deliver_now"
  ```

- **Odoo:** open **Settings → Technical → Outgoing Mail Servers → Platform mail**
  and choose **Test Connection**. A more specific server, such as a website's,
  keeps precedence for its sender domain.

## Change or turn off

Run the configurator again with the new values to rotate the password or change
provider, then restart GitLab and Odoo. To turn platform mail off:

```bash
scripts/configure-platform-mail.py --disable
```

This removes the stored password, clears the Keycloak realm's mail server and
leaves Alertmanager with the GitLab receiver only. GitLab stops sending and
Odoo archives its "Platform mail" server at their next restart. Revoke the
password or key at the provider as well.

## Where the password lives

Vault `infra/smtp` is the source. External Secrets copies it into the
`platform-mail` Secrets in `infra` and `corp` and into the rendered
`alertmanager-config` Secret. Keycloak keeps it in its realm settings, GitLab in
its generated configuration on the `gitlab-data` volume, and Odoo in its
database, so the nightly recovery archive holds it in the Vault snapshot and the
database dumps. If any copy is exposed, rotate the password at the provider and
rerun the configurator.

## Troubleshooting

| Message | Cause |
| --- | --- |
| `refused the username or password` | Wrong login, a revoked key, or a key without sending permission. |
| `could not be reached` | Wrong host or port, or outbound traffic to the relay is blocked. |
| `certificate check failed` | The host name does not match the relay's certificate, or `ssl` was used on a STARTTLS port. |
| `does not offer STARTTLS` | Use `--tls ssl` or the provider's STARTTLS port. |
| `refused ... as the sender` | Verify the sender's domain at the provider or use an allowed address. |
| No alert email | Check the alert's severity (only `critical` and `warning`), Alertmanager's logs and [AlertNotificationsFailing](alert-runbooks.md#alertnotificationsfailing). |

## Related Guides

- [Alert runbooks](alert-runbooks.md)
- [External services](external-services.md)
- [Security reference](../security.md)
- [Operations runbook](../operations.md)
