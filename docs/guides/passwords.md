# Staff Passwords (Vaultwarden)

Vaultwarden at `https://passwords.<domain>` keeps staff passwords for outside
services, for the official Bitwarden apps ([ADR 0032](../adr/0032-vaultwarden-for-staff-passwords.md)).
It is in the portal for everyone signed in. Machine secrets stay in HashiCorp
Vault.

## First sign-in

1. Open `https://passwords.<domain>` (or, in a Bitwarden app, choose
   **Self-hosted** and enter that address as the server URL).
2. Choose **Use single sign-on**. The SSO identifier can be any word, for
   example the company name; Vaultwarden does not use it.
3. Sign in through the platform's usual page, then choose a **master password**.
   It encrypts the vault on the device and is never sent to the server: nobody,
   administrators included, can recover it.

The account is created at that first sign-in and matched by verified email; it
cannot be created any other way.

## Sharing

The platform's first administrator creates an organization (for example the
company) and its collections (Finance, Suppliers, IT). Members of the
organization see the collections they are given. Invitations are sent through
the platform mail relay when it is configured; otherwise the person signs in
once and is then confirmed in the organization's **Members** page.

### The read-only profile's demo collection

Members of the [read-only profile](../security.md#read-only-profile) keep their
own vault but see no existing organization or password. To show them how
sharing works, an administrator creates one demo collection in the web vault
(items are encrypted in the browser, so no server job can create them):

1. Sign in once as the test account `readonly-test` (its password is in the
   Secret `infra/readonly-test-credentials`), so its Vaultwarden account exists,
   then sign out.
2. As an administrator: **New organization** `Read-only demo`; in it, the
   collection `Read-only demo`, and one login item `shared_password_test` with a
   made-up username and a generated password, nothing real.
3. **Members, Invite member**: `readonly-test@<your-domain>`, role **User**,
   access to the `Read-only demo` collection only, with **Can view** (the
   fake password is meant to be seen; no edit or manage). Confirm the member
   after the invitation is accepted.

Never add the test account, or the profile's members, to an existing
organization. `./install.sh --verify-profile` fails when the account belongs to
any organization but `Read-only demo`.

## When a master password is lost

The vault cannot be decrypted without it. Two ways back exist and must be set up
in advance by each person:

- **Emergency access** (**Settings, Emergency access**): a trusted colleague who
  may view or take over the vault after a waiting period the owner can refuse.
- An encrypted export (**Tools, Export vault**, format *.json (Encrypted)*,
  protected by a separate password), kept off the platform.

Logins that belong to the company go in an organization collection, so they
survive a person leaving.

## Backups and restore

The database is the `vaultwarden` database on the platform PostgreSQL, in the
nightly `pg_dumpall`; attachments, Sends and the server's signing key are on the
`vaultwarden-data` volume, in the daily off-node Longhorn backups. To restore,
restore both from the same night: the database from the dump
([restore](../operations.md#restore-after-losing-the-host)) and the volume from
its Longhorn backup, then restart the `vaultwarden` Deployment. Vault contents
are encrypted with the users' master passwords, so a backup is unreadable
without them.

## Settings

Everything is set in [`vaultwarden.yaml`](../../k8s/platform/vaultwarden.yaml);
the admin page is off. Sign-in is through Keycloak only (`SSO_ONLY`), sign-ups
are off, only the platform's first administrator creates organizations,
password hints are off, and organization events are kept 400 days. The
Keycloak client `vaultwarden` and its secret are reconciled by
`keycloak-sso-reconcile`.
