#!/usr/bin/env bash
# shellcheck disable=SC2034 # INSTALL_ENV_PROBLEMS is reported by the installer that sources this library.
# Import a per-install input file (scripts/lib/install_inputs.py) as data.
# Values are assigned with printf -v and never sourced or evaluated.

INSTALL_ENV_PROBLEMS=()

install_env_import() {
    local file="$1" from_stdin="${2:-false}" tool name value problems secrets="" flag=()
    tool="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/install-inputs.py"
    [[ -r "$file" ]] || { printf '[ERROR] Installation input file is not readable: %s\n' "$file" >&2; return 1; }

    # The file is the only source of installation inputs; ignore the caller's shell.
    while IFS= read -r name; do
        unset "$name"
    done < <(python3 "$tool" names)

    if [[ "$from_stdin" == true ]]; then
        # Secret NAME=value lines arrive on stdin and are passed on through pipes
        # (printf is a builtin): never arguments, here-string files or the disk.
        secrets="$(cat)"
        flag=(--secrets-stdin)
    fi
    if ! problems="$(printf '%s\n' "$secrets" | python3 "$tool" check "$file" "${flag[@]}")"; then
        mapfile -t INSTALL_ENV_PROBLEMS <<< "$problems"
    fi
    while IFS= read -r -d '' name && IFS= read -r -d '' value; do
        printf -v "$name" '%s' "$value"
        export "${name?}"
    done < <(printf '%s\n' "$secrets" | python3 "$tool" pairs "$file" "${flag[@]}")
    secrets=""
}
