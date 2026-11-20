#!/usr/bin/env bash
# First setup of a fresh Ubuntu server: root password, the admin account under
# its own name with passwordless sudo, and Claude Code for that account.
set -euo pipefail
umask 077

CLOUD_INIT_SUDOERS=/etc/sudoers.d/90-cloud-init-users
CLAUDE_INSTALLER=https://claude.ai/install.sh
DRY_RUN=false
RENAME_BACKUP=''

error() { printf '[ERROR] %s\n' "$*" >&2; exit 1; }
info() { printf '[INFO] %s\n' "$*"; }
dry() { printf '[DRY RUN] %s\n' "$*"; }
# Reports a finished step; a dry run has finished nothing.
done_step() { [[ "$DRY_RUN" == true ]] || info "$*"; }

usage() {
    cat <<'EOF'
Prepare a fresh Ubuntu server for administration with Claude Code.

Usage: sudo ./init/init-server.sh [--dry-run]

Asks for the current admin account, its new name, the full name and the root
and account passwords, shows the plan, then:
  1. sets the root password
  2. grants the account passwordless sudo
  3. renames the account and its group (same UID and GID) and moves its home
  4. sets the account's full name and password
  5. installs Claude Code for the account with a claude-yolo alias

Options:
  --dry-run   Run every check and show each change, including the account and
              sudoers file edits, without making it. Passwords are not asked.
  -h, --help  Show this help.
EOF
}

on_exit() {
    if (( $1 != 0 )) && [[ -n "$RENAME_BACKUP" ]]; then
        printf '[ERROR] The rename stopped. The account files from before it are in %s;\n' "$RENAME_BACKUP" >&2
        printf '        the root password set earlier works with su -.\n' >&2
    fi
}

# Runs a command, or shows it in a dry run.
run() {
    if [[ "$DRY_RUN" == true ]]; then
        dry "Would run:$(printf ' %q' "$@")"
    else
        "$@"
    fi
}

valid_account() { [[ "$1" =~ ^[a-z][a-z0-9_-]{0,31}$ ]]; }
valid_full_name() { [[ "$1" != *[:,=]* ]]; }

# ask VARIABLE PROMPT DEFAULT VALIDATOR MESSAGE
ask() {
    local answer
    while true; do
        read -rp "$2${3:+ [$3]}: " answer || error 'No input.'
        answer="${answer:-$3}"
        if [[ -n "$answer" ]] && "$4" "$answer"; then
            printf -v "$1" '%s' "$answer"
            return
        fi
        printf '%s\n' "$5" >&2
    done
}

# ask_password VARIABLE LABEL: typed twice, never echoed.
ask_password() {
    local first second
    while true; do
        read -rsp "$2: " first || error 'No input.'
        printf '\n'
        read -rsp "Confirm the $2: " second || error 'No input.'
        printf '\n'
        if [[ -z "$first" ]]; then
            printf 'The password must not be empty.\n' >&2
        elif [[ "$first" != "$second" ]]; then
            printf 'The passwords do not match.\n' >&2
        else
            printf -v "$1" '%s' "$first"
            return
        fi
    done
}

# set_password ACCOUNT PASSWORD: through a pipe, never a command line or history.
set_password() {
    if [[ "$DRY_RUN" == true ]]; then
        dry "Would set the password of $1."
    else
        printf '%s:%s\n' "$1" "$2" | chpasswd
    fi
}

# install_sudoers SOURCE TARGET: installs only a rule file visudo accepts.
install_sudoers() {
    visudo -c -f "$1" >/dev/null || error "$2 would not be valid sudoers syntax; it was left unchanged."
    if [[ "$DRY_RUN" == true ]]; then
        dry "Would write $2 (visudo accepts it):"
        sed 's/^/    /' "$1"
    else
        install -m 0440 -o root -g root -- "$1" "$2"
    fi
}

# Renames the account in field 1 and in the comma-separated member fields
# listed in `lists`; `home` replaces field 6 of the renamed passwd entry.
ACCOUNT_AWK='
BEGIN { FS = OFS = ":"; count = split(lists, fields, " ") }
$1 == old { $1 = new; if (home != "") $6 = home }
{
    for (i = 1; i <= count; i++) {
        f = fields[i] + 0
        if (f > NF || $f == "") continue
        n = split($f, names, ",")
        joined = (names[1] == old ? new : names[1])
        for (j = 2; j <= n; j++) joined = joined "," (names[j] == old ? new : names[j])
        $f = joined
    }
    print
}'

# rename_in FILE OLD NEW HOME LISTS: rewrites an account file in place,
# keeping its owner and mode. A dry run shows the change; for the shadow
# files only the entry names, never the hashes.
rename_in() {
    local file="$1" tmp
    [[ -e "$file" ]] || return 0
    if [[ "$DRY_RUN" == true ]]; then tmp="$(mktemp)"; else tmp="$(mktemp "$file.XXXXXX")"; fi
    awk -v old="$2" -v new="$3" -v home="$4" -v lists="$5" "$ACCOUNT_AWK" "$file" > "$tmp"
    if cmp -s "$file" "$tmp"; then
        rm -f -- "$tmp"
        return 0
    fi
    if [[ "$DRY_RUN" == true ]]; then
        # The rewrite keeps every line in place, so line N compares with line N.
        if [[ "$file" == *shadow ]]; then
            dry "Would rename $2 in these $file entries: $(awk 'NR == FNR { line[FNR] = $0; next }
                line[FNR] != $0 { split(line[FNR], field, ":"); printf "%s ", field[1] }' "$file" "$tmp")"
        else
            dry "Would change $file:"
            awk 'NR == FNR { line[FNR] = $0; next }
                line[FNR] != $0 { printf "    %s  ->  %s\n", line[FNR], $0 }' "$file" "$tmp"
        fi
        rm -f -- "$tmp"
        return 0
    fi
    chown --reference="$file" -- "$tmp"
    chmod --reference="$file" -- "$tmp"
    mv -f -- "$tmp" "$file"
}

main() {
    while (( $# )); do
        case "$1" in
            --dry-run) DRY_RUN=true ;;
            -h|--help) usage; exit 0 ;;
            *) usage >&2; exit 2 ;;
        esac
        shift
    done
    (( EUID == 0 )) || error 'Run as root: sudo ./init/init-server.sh'
    [[ -t 0 ]] || error 'Run interactively; over SSH use ssh -t.'
    local command
    for command in awk chpasswd chown cmp curl getent install runuser sudo usermod visudo; do
        command -v "$command" >/dev/null || error "Required command is missing: $command"
    done
    if [[ "$DRY_RUN" == true ]]; then
        info 'Dry run: every check runs, nothing is changed.'
    fi

    local current="${SUDO_USER:-}" old_user new_user full_name root_password='' user_password='' reply
    [[ -n "$current" && "$current" != root ]] || current=ubuntu
    ask old_user 'Current admin account' "$current" valid_account 'Enter an account name.'
    ask new_user 'New account name' "$old_user" valid_account \
        'Use lowercase letters, digits, - or _, starting with a letter, 32 characters at most.'
    [[ "$new_user" != root ]] || error 'The admin account cannot be root.'
    ask full_name 'Full name' '' valid_full_name 'Enter a full name without :, , or =.'

    # Every check runs before the first change.
    local rename=false old_uid='' old_home='' new_home='' home gecos
    if [[ "$old_user" != "$new_user" ]] && getent passwd "$old_user" >/dev/null; then
        if getent passwd "$new_user" >/dev/null; then error "Account $new_user already exists."; fi
        if getent group "$new_user" >/dev/null; then error "Group $new_user already exists."; fi
        IFS=: read -r _ _ old_uid _ gecos old_home _ < <(getent passwd "$old_user")
        (( old_uid >= 1000 )) || error "$old_user is a system account (UID $old_uid)."
        [[ -d "$old_home" ]] || error "The home directory $old_home of $old_user does not exist."
        new_home="$(dirname -- "$old_home")/$new_user"
        if [[ -e "$new_home" || -L "$new_home" ]]; then error "$new_home already exists."; fi
        rename=true
        home="$new_home"
    elif getent passwd "$new_user" >/dev/null; then
        IFS=: read -r _ _ _ _ gecos home _ < <(getent passwd "$new_user")
    else
        error "Account $new_user does not exist."
    fi

    if [[ "$DRY_RUN" != true ]]; then
        ask_password root_password 'root password'
        ask_password user_password "password for $new_user"
    fi

    printf '\nPlan:\n  - set the root password\n  - passwordless sudo for %s in /etc/sudoers.d/91-%s\n' "$new_user" "$new_user"
    if [[ "$rename" == true ]]; then
        printf '  - rename %s to %s (UID %s kept), move %s to %s\n' "$old_user" "$new_user" "$old_uid" "$old_home" "$new_home"
    fi
    printf '  - full name "%s" and password for %s\n  - Claude Code and the claude-yolo alias for %s\n\n' \
        "$full_name" "$new_user" "$new_user"
    if [[ "$DRY_RUN" != true ]]; then
        read -rp 'Continue? [y/N] ' reply || error 'No input.'
        [[ "$reply" == [yY]* ]] || error 'Cancelled; nothing was changed.'
    fi

    # Root first: if a later step fails, su - still reaches root.
    set_password root "$root_password"
    done_step 'Root password set.'

    local tmp
    tmp="$(mktemp)"
    printf '# Passwordless sudo for %s (init/init-server.sh)\n%s ALL=(ALL) NOPASSWD:ALL\n' "$new_user" "$new_user" > "$tmp"
    install_sudoers "$tmp" "/etc/sudoers.d/91-$new_user"
    rm -f -- "$tmp"
    done_step "Passwordless sudo granted to $new_user."

    if [[ "$rename" == true ]]; then
        local backup file spool
        backup="/root/user-rename-backup-$(date +%Y%m%d-%H%M%S)"
        if [[ "$DRY_RUN" == true ]]; then
            dry "Would back up the account files to $backup."
        else
            mkdir -p -- "$backup"
            for file in /etc/passwd /etc/shadow /etc/group /etc/gshadow /etc/subuid /etc/subgid "$CLOUD_INIT_SUDOERS"; do
                if [[ -e "$file" ]]; then cp -a -- "$file" "$backup/"; fi
            done
            RENAME_BACKUP="$backup"
        fi

        # usermod refuses to rename an account with running processes, and this
        # script runs from that account's session, so the files are edited directly.
        rename_in /etc/passwd "$old_user" "$new_user" "$new_home" ''
        rename_in /etc/shadow "$old_user" "$new_user" '' ''
        rename_in /etc/group "$old_user" "$new_user" '' '4'
        rename_in /etc/gshadow "$old_user" "$new_user" '' '3 4'
        rename_in /etc/subuid "$old_user" "$new_user" '' ''
        rename_in /etc/subgid "$old_user" "$new_user" '' ''

        # The open session still uses the old path; the symlink keeps it working.
        run mv -- "$old_home" "$new_home"
        run ln -s -- "$new_home" "$old_home"
        for spool in /var/spool/cron/crontabs /var/mail; do
            if [[ -e "$spool/$old_user" && ! -e "$spool/$new_user" ]]; then
                run mv -- "$spool/$old_user" "$spool/$new_user"
            fi
        done

        # The 91 rule above replaces cloud-init's grant for the old name.
        if [[ -f "$CLOUD_INIT_SUDOERS" ]]; then
            tmp="$(mktemp)"
            awk -v old="$old_user" '$1 != old && $0 != ("# User rules for " old)' "$CLOUD_INIT_SUDOERS" > "$tmp"
            if ! cmp -s "$CLOUD_INIT_SUDOERS" "$tmp"; then
                install_sudoers "$tmp" "$CLOUD_INIT_SUDOERS"
            fi
            rm -f -- "$tmp"
        fi

        if [[ "$DRY_RUN" != true ]]; then
            [[ "$(id -u "$new_user")" == "$old_uid" ]] || error "$new_user does not resolve to UID $old_uid."
            info "Renamed $old_user to $new_user; backup in $backup."
            RENAME_BACKUP=''
        fi
    fi

    if [[ "$gecos" != "$full_name" ]]; then run usermod -c "$full_name" "$new_user"; fi
    set_password "$new_user" "$user_password"
    unset root_password user_password
    done_step "Full name and password set for $new_user."

    if [[ "$DRY_RUN" != true ]]; then
        visudo -c >/dev/null || error 'The sudoers configuration is not valid.'
        local rules
        rules="$(sudo -l -U "$new_user")"
        [[ "$rules" == *'NOPASSWD: ALL'* ]] || error "$new_user has no passwordless sudo rule."
    fi

    # Last: Claude Code goes into the final home, as the account, never as root.
    # A dry run reads the home directory where it still is.
    local home_now="$home"
    if [[ "$rename" == true && "$DRY_RUN" == true ]]; then home_now="$old_home"; fi
    if [[ -x "$home_now/.local/bin/claude" ]]; then
        info "Claude Code is already installed for $new_user."
    elif [[ "$DRY_RUN" == true ]]; then
        curl -fsS -o /dev/null "$CLAUDE_INSTALLER" || error "The Claude Code installer $CLAUDE_INSTALLER is unreachable."
        dry "Would install Claude Code for $new_user with $CLAUDE_INSTALLER (reachable)."
    else
        # ~/.profile adds ~/.local/bin only if it existed at login, so the
        # installer gets it in PATH here; ~/.bashrc gets it below.
        runuser -l "$new_user" -s /bin/bash -c \
            "set -o pipefail; export PATH=\"\$HOME/.local/bin:\$PATH\"; curl -fsSL $CLAUDE_INSTALLER | bash"
        [[ -x "$home/.local/bin/claude" ]] || error "Claude Code is not in $home/.local/bin after the installer ran."
        info "Claude Code installed for $new_user."
    fi

    if grep -qsF -e 'alias claude-yolo=' "$home_now/.bashrc"; then
        info "The claude-yolo alias already exists for $new_user."
    elif [[ "$DRY_RUN" == true ]]; then
        dry "Would add the ~/.local/bin PATH line and the claude-yolo alias to $home/.bashrc."
    else
        runuser -u "$new_user" -- tee -a -- "$home/.bashrc" >/dev/null <<'EOF'

# Claude Code: the native installer's directory and the permission-bypass alias
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
alias claude-yolo='claude --dangerously-skip-permissions'
EOF
        info "PATH and the claude-yolo alias added to $home/.bashrc."
    fi

    if [[ "$DRY_RUN" == true ]]; then
        printf '\nDry run complete; nothing was changed. Run again without --dry-run to apply.\n'
        return
    fi
    printf '\nDone. Keep this session open until a new login works, then:\n'
    printf '  1. ssh %s@<server>\n' "$new_user"
    printf '  2. claude        and sign in\n'
    printf '  3. claude-yolo   and accept the bypass-permissions prompt once\n'
}

trap 'on_exit $?' EXIT
main "$@"
