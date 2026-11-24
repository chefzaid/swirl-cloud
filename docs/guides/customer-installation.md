# Customer Installation Checklist

For an installation the vendor performs for a customer, on the customer's
servers. The customer owns the result: the installer hands over every key and
keeps no copy, unless a managed-service contract says otherwise.

## Before installing

- [ ] The servers are rented or owned in the customer's name, with the customer
      holding the provider account; the installer works through a delegated or
      temporary access.
- [ ] The servers meet the [support matrix](../../README.md) and the sizing
      profile chosen ([capacity planning](capacity-planning.md)).
- [ ] The domain, its DNS provider access and the mail relay are the customer's
      ([platform mail](platform-mail.md)).
- [ ] The off-site backup target exists and is the customer's.
- [ ] The customer named a first administrator, who signs in during the
      acceptance.

## Install

Run the setup hub or `./install.sh --env-file FILE` on the first server. Keep the
inputs file; it is handed over.

## Acceptance

1. Restore drill: restore the first nightly archive on a disposable host
   ([restore after losing the host](../operations.md#restore-after-losing-the-host)),
   then `./install.sh --duty done restore-drill`.
2. Recovery kit: `scripts/export-recovery-kit.sh --separate-archives`, check it
   with `--verify FILE`, hand it over (below), delete the local copy, then
   `./install.sh --duty done recovery-kit`.
3. The customer's first administrator signs in through the portal and changes
   their password; the [sign-in probe](service-levels.md#alerts) shows sign-in
   working.
4. Write the record:

   ```bash
   ./install.sh --acceptance --customer "Example SAS" --output acceptance.md
   ```

   It runs `verify-platform.py --strict` (warnings count as failures), checks
   that the restore drill and the recovery kit were recorded in the last 30 days
   and that the sign-in probe passes, and lists the handover items and the
   signature lines. It exits 1 until every item passes.

## Handover

- [ ] The recovery kit and its passphrase, kept in two separate places the
      customer chose; the installer keeps no copy.
- [ ] The administrator accounts, with the passwords changed by the customer.
- [ ] Vault's unseal key and root token handling, as recorded in the kit.
- [ ] The provider console access, in the customer's name; the installer's
      delegated access removed.
- [ ] The inputs file and the documentation of the installed version.

Both sides sign the acceptance record; the customer keeps the original. Then
remove the installer's own access: SSH keys, Keycloak accounts and any
just-in-time grants (`./install.sh --admin-access list`, `revoke`).
