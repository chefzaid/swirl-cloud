#!/usr/bin/env bash
# Guard the K3s registry mirror without a node: a fresh node gets a root-only
# registries.yaml with the platform Registry's mirror and the containerd image
# drop-in, an existing file keeps its other mirrors, one without a mirrors
# mapping is refused, and K3s restarts only when something changed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
BIN="$STATE/bin"
mkdir -p "$BIN"
LOG="$STATE/log"
# PATH stubs: sudo runs the command, install drops ownership (tests may not run as
# root) and records modes, systemctl reports k3s active and records restarts.
cat > "$BIN/sudo" <<'EOF'
#!/usr/bin/env bash
exec "$@"
EOF
cat > "$BIN/install" <<EOF
#!/usr/bin/env bash
args=()
while (( \$# )); do
    case "\$1" in
        -o|-g) shift 2 ;;
        -m) printf 'mode %s %s\n' "\$2" "\${@: -1}" >> "$LOG"; args+=("\$1" "\$2"); shift 2 ;;
        *) args+=("\$1"); shift ;;
    esac
done
exec /usr/bin/install "\${args[@]}"
EOF
cat > "$BIN/systemctl" <<EOF
#!/usr/bin/env bash
case "\$*" in
    "is-active --quiet k3s") exit 0 ;;
    "restart k3s") printf 'restart k3s\n' >> "$LOG" ;;
    *) exit 1 ;;
esac
EOF
cat > "$BIN/kubectl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN"/*
CONFIG="$STATE/etc/registries.yaml"
DROPIN="$STATE/containerd/20-swirl-cloud-images.toml"
mirror() {
    : > "$LOG"
    env PATH="$BIN:$PATH" PLATFORM_DOMAIN=example.com K3S_REGISTRY_CONFIG="$CONFIG" K3S_CONTAINERD_DROPIN="$DROPIN" "$@" \
        bash "$ROOT/scripts/configure-k3s-registry-mirror.sh" 2>&1
}
yaml() { python3 -c 'import sys, yaml, json; print(json.dumps(yaml.safe_load(open(sys.argv[1])), sort_keys=True))' "$1"; }

# A fresh node.
mirror >/dev/null || fail "A fresh node fails: $(mirror)"
[[ "$(yaml "$CONFIG")" == '{"mirrors": {"registry.example.com": {"endpoint": ["http://10.43.255.251:5050"]}}}' ]] || \
    fail "Unexpected registries.yaml: $(cat "$CONFIG")"
grep -Fqx "mode 0600 $CONFIG" "$LOG" || fail 'registries.yaml is not root-only'
grep -Fqx 'discard_unpacked_layers = true' <(sed 's/^ *//' "$DROPIN") || fail 'Unpacked layers are kept'
grep -Fqx 'restart k3s' "$LOG" || fail 'K3s does not restart for a new mirror'

# A rerun changes nothing and does not restart K3s.
output="$(mirror)"
if [[ "$output" != *"already configured"* ]] || grep -q restart "$LOG"; then fail "An unchanged node restarts K3s: $output"; fi

# An existing file keeps its other mirrors, and the platform's credentials under
# configs do not count as its mirror; the mirror is added once.
printf 'mirrors:\n  "docker.io":\n    endpoint:\n      - "https://mirror.example.net"\nconfigs:\n  "registry.example.com":\n    tls:\n      insecure_skip_verify: false\n' > "$CONFIG"
mirror >/dev/null || fail 'An existing registries.yaml fails'
[[ "$(yaml "$CONFIG")" == '{"configs": {"registry.example.com": {"tls": {"insecure_skip_verify": false}}}, "mirrors": {"docker.io": {"endpoint": ["https://mirror.example.net"]}, "registry.example.com": {"endpoint": ["http://10.43.255.251:5050"]}}}' ]] || \
    fail "Other mirrors are not kept: $(cat "$CONFIG")"
mirror >/dev/null
[[ "$(grep -c '"registry.example.com":' "$CONFIG")" == 2 ]] || fail "The mirror is added twice: $(cat "$CONFIG")"
printf 'mirrors:\n  registry.example.com:\n    endpoint:\n      - "http://10.0.0.5:5050"\n' > "$CONFIG"
[[ "$(mirror)" == *"already configured"* ]] || fail 'An unquoted existing mirror is added again'

# A file whose mirrors are not a top-level mapping is refused unchanged.
printf 'configs: {}\n' > "$CONFIG"
if output="$(mirror K3S_REGISTRY_HOST=registry.other.example)"; then fail 'A file without mirrors is changed'; fi
[[ "$output" == *"without a top-level mirrors mapping"* && "$(cat "$CONFIG")" == "configs: {}" ]] || fail "Unexpected result: $output"

# The image setting is validated, and changing it restarts K3s.
if output="$(mirror K3S_DISCARD_UNPACKED_LAYERS=maybe)"; then fail 'An invalid layer setting is accepted'; fi
[[ "$output" == *"must be true or false"* ]] || fail "Unexpected error: $output"
rm -f "$CONFIG"
mirror >/dev/null
mirror K3S_DISCARD_UNPACKED_LAYERS=false >/dev/null
grep -Fqx 'discard_unpacked_layers = false' <(sed 's/^ *//' "$DROPIN") || fail 'A changed image setting is not written'
grep -Fqx 'restart k3s' "$LOG" || fail 'A changed image setting does not restart K3s'

printf '[PASS] K3s registry mirror\n'
