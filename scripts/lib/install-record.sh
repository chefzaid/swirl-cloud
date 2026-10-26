#!/usr/bin/env bash
# Installer run records: one JSON line per event (start, each step, finish) in
# /var/lib/swirl-cloud/install-runs/<run>.jsonl, shown by ./install.sh --status.
# Records hold timings, step names, versions and the exit class; never inputs.
# Recording is best effort: it must never stop or slow an installation.

INSTALL_RECORD_DIR="${INSTALL_RECORD_DIR:-/var/lib/swirl-cloud/install-runs}"
INSTALL_RECORD_KEEP=50
INSTALL_RECORD_FILE=""
INSTALL_RECORD_STARTED="$(date +%s)"

# Appends one event; arguments are key=value pairs.
install_record_event() {
    [[ -n "$INSTALL_RECORD_FILE" ]] || return 0
    python3 - "$@" >> "$INSTALL_RECORD_FILE" 2>/dev/null <<'PY' || true
import json, sys, time
from datetime import datetime, timezone
event = dict(argument.split("=", 1) for argument in sys.argv[1:])
event["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
event["epoch"] = int(time.time())
print(json.dumps(event))
PY
}

# Creates this run's record while sudo is fresh; later events need no sudo
# because the file belongs to the invoking user. Keeps the newest records.
install_record_start() {
    local mode="$1" run old
    run="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    if ! sudo install -d -o root -g root -m 0755 "$INSTALL_RECORD_DIR" ||
        ! sudo install -o "$(id -u)" -g "$(id -g)" -m 0644 /dev/null "$INSTALL_RECORD_DIR/$run.jsonl"; then
        warn "Could not create the installation record in $INSTALL_RECORD_DIR; continuing without it."
        return 0
    fi
    INSTALL_RECORD_FILE="$INSTALL_RECORD_DIR/$run.jsonl"
    while IFS= read -r old; do
        sudo rm -f -- "$old" || true
    done < <(find "$INSTALL_RECORD_DIR" -maxdepth 1 -name '*.jsonl' -type f | LC_ALL=C sort -r | tail -n +"$((INSTALL_RECORD_KEEP + 1))")
    install_record_event event=start mode="$mode" started="$INSTALL_RECORD_STARTED" \
        revision="$(git -C "$SCRIPT_DIR" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)" \
        chart="$(awk '$1 == "version:" {print $2; exit}' "$SCRIPT_DIR/k8s/Chart.yaml" 2>/dev/null)" \
        k3s="$K3S_INSTALL_VERSION" os="$( (. /etc/os-release && echo "$PRETTY_NAME") 2>/dev/null)" host="$(hostname)"
}

install_record_step() {
    install_record_event event=step name="$*"
}

# Called from the EXIT trap with the script's exit status.
install_record_finish() {
    local status="$1" result=failed
    [[ -n "$INSTALL_RECORD_FILE" ]] || return 0
    case "$status" in
        0) result=success ;;
        129|130|143) result=interrupted ;;
    esac
    install_record_event event=finish result="$result" exit_code="$status" \
        seconds="$(( $(date +%s) - INSTALL_RECORD_STARTED ))"
}
