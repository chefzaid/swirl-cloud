# Server Init

[`init-server.sh`](init-server.sh) prepares a fresh Ubuntu server so that it can be
administered with Claude Code. It runs once, as root, before anything else is
installed. In order, it:

1. Sets the root password, which is also the way back in with `su -` if a later step fails.
2. Grants the admin account passwordless sudo in `/etc/sudoers.d/91-<account>`,
   checked with `visudo` before it is installed.
3. Renames the image's default account (for example `ubuntu`) and its group. The
   UID and GID stay the same, and group memberships, the crontab and the mail
   spool follow the new name. The home directory moves to `/home/<account>`,
   and `/home/<old>` is left as a symlink for the session that is still open.
   cloud-init's sudo rule for the old name is removed. The account files from
   before the rename are copied to `/root/user-rename-backup-<timestamp>/`.
4. Sets the account's full name and password.
5. Installs Claude Code for the account and adds `~/.local/bin` to its `PATH`
   and the `claude-yolo` alias (`claude --dangerously-skip-permissions`) to its
   `~/.bashrc`.

The script asks for every value: the current account (by default, the one that
runs `sudo`), the new account name (by default, unchanged), the full name, and
the root and account passwords. Passwords are typed twice and never echoed,
passed on a command line or written to shell history. The script checks
everything and shows the plan before it changes anything. You can safely run it
again: an account that is already renamed, an existing Claude Code install and
an existing alias are left as they are.

It targets the Ubuntu images OVH installs, such as Ubuntu 26.04 LTS with
cloud-init's `ubuntu` account, sudo-rs and the Rust coreutils.

## Launch

Log in as the image's default account, then use one of these:

From a clone of this repository on the server:

```bash
sudo ./init-server/init-server.sh --dry-run
sudo ./init-server/init-server.sh
```

From your workstation, copy the script and run it over SSH (`-t` keeps the
prompts interactive):

```bash
scp init-server/init-server.sh ubuntu@<server>:
ssh -t ubuntu@<server> 'sudo bash init-server.sh --dry-run'
ssh -t ubuntu@<server> 'sudo bash init-server.sh'
```

`--dry-run` asks the same questions except the passwords and runs every check,
including `visudo` on the new rule files and a request to the Claude Code
installer URL. It then prints each change without making it: the account and
group lines as they are now and as they would become, the `/etc/shadow` and
`/etc/gshadow` entries that would change (names only, never hashes), each
sudoers file as it would be written, and the moves and commands that would
follow.

## Afterwards

Keep the current session open until a new login works, then:

1. Reconnect as the new account: `ssh <account>@<server>`.
2. Run `claude` and sign in.
3. Run `claude-yolo` once and accept the bypass-permissions prompt.
4. When nothing uses the old path any more, remove the symlink: `sudo rm /home/<old>`.

The platform itself is installed next with [`./install.sh`](../README.md#get-started).
