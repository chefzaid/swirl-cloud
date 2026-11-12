#!/usr/bin/env bash
# Guard installer and playbook run records without a host: events are valid JSON,
# a failed run names its last step, old records are pruned, an unwritable directory
# never stops the installer or a playbook, playbook records hold no variables, and
# --status reads the newest record.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
# The record directory is root-owned on a host; here sudo just runs the command.
sudo() { "$@"; }
SCRIPT_DIR="$ROOT"
K3S_INSTALL_VERSION=v1.36.4+k3s1
export INSTALL_RECORD_DIR="$WORK/runs"
# shellcheck source=../scripts/lib/install-record.sh
source "$ROOT/scripts/lib/install-record.sh"

mkdir -p "$INSTALL_RECORD_DIR"
for old in $(seq -w 1 60); do : > "$INSTALL_RECORD_DIR/20260101T0000${old}Z-1.jsonl"; done
install_record_start env-file
install_record_step 'Installing K3s "v1.36" with a quote...'
install_record_finish 1
(( $(find "$INSTALL_RECORD_DIR" -name '*.jsonl' | wc -l) == 50 )) || fail 'Old records are not pruned to the newest 50'
[[ ! -e "$INSTALL_RECORD_DIR/20260101T000001Z-1.jsonl" && -e "$INSTALL_RECORD_DIR/20260101T000060Z-1.jsonl" ]] || \
    fail 'Pruning removes newer records instead of the oldest'

python3 - "$INSTALL_RECORD_FILE" "$ROOT" <<'PY' || fail 'The record or its --status summary is wrong'
import importlib.util, json, sys
from pathlib import Path
events = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
assert [event["event"] for event in events] == ["start", "step", "finish"], events
start, step, finish = events
assert start["mode"] == "env-file" and start["k3s"] == "v1.36.4+k3s1" and start["revision"], start
assert step["name"] == 'Installing K3s "v1.36" with a quote...', step
assert finish["result"] == "failed" and finish["exit_code"] == "1", finish
spec = importlib.util.spec_from_file_location("verify", Path(sys.argv[2]) / "scripts/verify-platform.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
verify.INSTALL_RECORDS = Path(sys.argv[1]).parent
report = verify.Report()
verify.check_install_record(report)
[result] = report.results
assert result["status"] == verify.WARN and "failed" in result["summary"], result
assert result["details"][0] == 'last step: Installing K3s "v1.36" with a quote...', result
assert result["hint"], result
broken = Path(sys.argv[1]).parent / "29991231T235959Z-1.jsonl"
broken.write_text('{"event": "finish", "seconds": "not a number"}\n')
report = verify.Report()
verify.check_install_record(report)
assert report.results[0]["status"] == verify.WARN and "unreadable" in report.results[0]["summary"], report.results
broken.unlink()
verify.INSTALL_RECORDS = Path(sys.argv[1]).parent / "missing"
report = verify.Report()
verify.check_install_record(report)
assert report.results == [], "a host installed before records existed gets a warning"
PY

for case in 0:success 130:interrupted 143:interrupted 2:failed; do
    INSTALL_RECORD_FILE="$WORK/finish.jsonl"
    : > "$INSTALL_RECORD_FILE"
    install_record_finish "${case%:*}"
    grep -q "\"result\": \"${case#*:}\"" "$INSTALL_RECORD_FILE" || fail "Exit ${case%:*} is not recorded as ${case#*:}"
done

# Under root's 0700 /var/lib/swirl-cloud the user cannot reach its own record;
# events then go through passwordless sudo.
INSTALL_RECORD_FILE="$WORK/unreachable/run.jsonl"
# shellcheck disable=SC2329  # called by install_record_event
sudo() { [[ "$*" == "-n tee -a $INSTALL_RECORD_FILE" ]] && cat >> "$WORK/through-sudo.jsonl"; }
install_record_step 'Through sudo' 2>/dev/null || fail 'An unreachable record stops the installer'
grep -q '"name": "Through sudo"' "$WORK/through-sudo.jsonl" 2>/dev/null || \
    fail 'Events are not appended through sudo when the record cannot be reached'

INSTALL_RECORD_FILE=""
sudo() { return 1; }
install_record_start prompts 2>/dev/null || fail 'An unwritable record directory stops the installer'
[[ -z "$INSTALL_RECORD_FILE" ]] || fail 'A record that could not be created is still written'
install_record_step 'No record' || fail 'Steps fail without a record'
install_record_finish 0 || fail 'Finishing fails without a record'

# Playbook runs: the real callback plugin, loaded from beside a disposable playbook.
mkdir -p "$WORK/repo/ansible/callback_plugins" "$WORK/repo/config" "$WORK/repo/k8s"
cp "$ROOT/ansible/callback_plugins/swirl_cloud_record.py" "$WORK/repo/ansible/callback_plugins/"
cp "$ROOT/config/platform.env" "$WORK/repo/config/"
cp "$ROOT/k8s/Chart.yaml" "$WORK/repo/k8s/"
cat > "$WORK/repo/ansible/deploy.yml" <<'YAML'
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Use an input
      ansible.builtin.command: "echo {{ admin_password }}"
      no_log: true
    - name: Reconcile something that fails
      ansible.builtin.command: "false"
YAML
playbook() {
    ANSIBLE_PYTHON_INTERPRETER=auto_silent ANSIBLE_LOCALHOST_WARNING=false \
        ansible-playbook -i localhost, -e admin_password=not-recorded-secret "$@" "$WORK/repo/ansible/deploy.yml" > "$WORK/ansible.log" 2>&1
}
export INSTALL_RECORD_DIR="$WORK/playbook-runs"
playbook && fail 'The disposable playbook did not fail'
playbook --syntax-check || { cat "$WORK/ansible.log" >&2; fail 'The playbook does not pass a syntax check'; }
playbook --check || true
records=("$INSTALL_RECORD_DIR"/*.jsonl)
(( ${#records[@]} == 1 )) || fail 'Syntax checks or check-mode runs leave records, or the real run left none'
! grep -q 'not-recorded-secret' "${records[0]}" || fail 'A playbook record holds a variable value'
python3 - "${records[0]}" "$ROOT" <<'PY' || fail 'The playbook record or its --status summary is wrong'
import importlib.util, json, sys
from pathlib import Path
events = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines()]
assert [event["event"] for event in events] == ["start", "step", "step", "finish"], events
assert events[0]["mode"] == "ansible:deploy" and events[0]["k3s"].startswith("v") and events[0]["chart"], events[0]
assert events[-1]["result"] == "failed" and events[-1]["exit_code"] == "2", events[-1]
spec = importlib.util.spec_from_file_location("verify", Path(sys.argv[2]) / "scripts/verify-platform.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
verify.INSTALL_RECORDS = Path(sys.argv[1]).parent
report = verify.Report()
verify.check_install_record(report)
[result] = report.results
assert result["status"] == verify.WARN and "ansible:deploy" in result["summary"], result
assert result["details"][0] == "last step: Reconcile something that fails", result
PY
INSTALL_RECORD_DIR=/dev/null/runs playbook && fail 'The disposable playbook did not fail'
grep -q 'Reconcile something that fails' "$WORK/ansible.log" || fail 'An unwritable record directory stops a playbook'

printf '[PASS] Installer and playbook run records\n'
