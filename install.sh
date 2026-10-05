#!/usr/bin/env bash
# Swirl Cloud entry point.
#
#   ./install.sh                         guided setup hub (terminal UI)
#   ./install.sh --output FILE           hub with a different input file
#   ./install.sh --env-file FILE         unattended install/update from a saved file
#   ./install.sh --env-file FILE --validate-only
#   ... | ./install.sh --env-file FILE --secrets-stdin   secrets as NAME=value lines on stdin
#   ./install.sh --status                verify an installed platform
#   ./install.sh --plan [--env-file FILE]  show installed, missing or drifted stages
#   ./install.sh --support-bundle        private troubleshooting archive (secrets redacted)
#   ./install.sh --compliance [--evidence-pack]  control status for NIS2/ISO 27001/DORA/GDPR/CIS
#   ./install.sh --access-review [--output-dir DIR]  who has access to what, with findings
#   ./install.sh --incident-report --start TIME [--end TIME] [--aware TIME]  evidence and NIS2 drafts
#   ./install.sh --vulnerability-report [--all]  Critical/High vulnerabilities past their deadline
#   ./install.sh --credential-age [--max-age-days N]  when every Vault secret was last written
#   ./install.sh --policy-report         application workloads that break admission policies
#   ./install.sh --processing-records    draft GDPR Article 30 records from the running platform
set -Eeuo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    sed -n '3,18s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"
}

if [[ $EUID -eq 0 ]]; then
    echo "[ERROR] Run ./install.sh as a regular user with sudo rights, not as root." >&2
    exit 1
fi

missing_packages() {
    local packages=()
    command -v python3 >/dev/null 2>&1 || packages+=(python3)
    if [[ "${1:-}" == tui ]]; then
        command -v whiptail >/dev/null 2>&1 || packages+=(whiptail)
    fi
    printf '%s\n' "${packages[@]}"
}

ensure_packages() {
    local packages answer
    mapfile -t packages < <(missing_packages "$1")
    [[ ${#packages[@]} -gt 0 && -n "${packages[0]}" ]] || return 0
    command -v apt-get >/dev/null 2>&1 || {
        echo "[ERROR] Install ${packages[*]} with your package manager, then rerun ./install.sh." >&2
        exit 1
    }
    [[ -t 0 ]] || { echo "[ERROR] Missing ${packages[*]}; install them first." >&2; exit 1; }
    read -rp "The setup needs ${packages[*]}. Install with apt-get now? [Y/n] " answer
    [[ "${answer:-Y}" =~ ^[Yy]$ ]] || exit 1
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${packages[@]}"
}

case "${1:-}" in
    --env-file|--env-file=*)
        ensure_packages engine
        exec "$ROOT/install-control-plane.sh" "$@"
        ;;
    --status)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/verify-platform.py" "$@"
        ;;
    --plan)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/install-plan.py" "$@"
        ;;
    --support-bundle)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/support-bundle.py" "$@"
        ;;
    --compliance)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/compliance-report.py" "$@"
        ;;
    --access-review)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/access-review.py" "$@"
        ;;
    --incident-report)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/incident-report.py" "$@"
        ;;
    --vulnerability-report)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/vulnerability-report.py" "$@"
        ;;
    --credential-age)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/credential-age.py" "$@"
        ;;
    --policy-report)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/policy-report.py" "$@"
        ;;
    --processing-records)
        shift
        ensure_packages engine
        exec python3 "$ROOT/scripts/processing-records.py" "$@"
        ;;
    -h|--help)
        usage
        ;;
    ""|--output|--output=*)
        [[ -t 0 && -t 1 ]] || { echo "[ERROR] The setup hub needs a terminal; use --env-file for automation." >&2; exit 1; }
        ensure_packages tui
        exec python3 "$ROOT/scripts/install-tui.py" "$@"
        ;;
    *)
        echo "[ERROR] Unknown option: $1" >&2
        usage >&2
        exit 1
        ;;
esac
