#!/usr/bin/env bash
# Guard the server cost metrics without a node: valid prices become one
# well-formed textfile, written next to the target and renamed, and every
# malformed input (amount, currency, date, source, shared item, a shared item
# given twice) is refused before anything is written.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
STATE="$(mktemp -d)"
trap 'rm -rf "$STATE"' EXIT
BIN="$STATE/bin"
mkdir -p "$BIN"
# sudo stub: the textfile directory lives under $STATE; ownership is dropped
# because the test may not run as root.
cat > "$BIN/sudo" <<EOF
#!/usr/bin/env bash
args=("\${@//\/var\/lib\/node_exporter/$STATE/node_exporter}")
if [[ "\${args[0]}" == install ]]; then
    filtered=()
    set -- "\${args[@]:1}"
    while (( \$# )); do case "\$1" in -o|-g) shift 2 ;; *) filtered+=("\$1"); shift ;; esac; done
    args=(install "\${filtered[@]}")
fi
printf '%s\n' "\${args[*]}" >> "$STATE/sudo.log"
exec "\${args[@]}"
EOF
chmod +x "$BIN/sudo"
FILE="$STATE/node_exporter/textfile_collector/swirl_cloud_cost.prom"
cost() { env PATH="$BIN:$PATH" bash "$ROOT/scripts/configure-node-cost.sh" "$@" 2>&1; }

cost --monthly-cost 89.90 --currency eur --price-date 2026-10-26 --price-source "Hetzner AX52 order" \
    --shared backup-storage=12.50 --shared domains=2 >/dev/null || fail "Valid prices are refused: $(cost --monthly-cost 89.90)"
grep -Fqx 'swirl_cloud_node_monthly_cost{currency="EUR"} 89.90' "$FILE" || fail "The server cost is not recorded: $(cat "$FILE")"
grep -Fqx "swirl_cloud_node_cost_priced_timestamp_seconds{source=\"Hetzner AX52 order\"} $(date -u -d 2026-10-26 +%s)" "$FILE" || \
    fail 'The price date is not recorded'
grep -Fqx 'swirl_cloud_shared_monthly_cost{currency="EUR",item="backup-storage"} 12.50' "$FILE" || fail 'A shared cost is not recorded'
if command -v promtool >/dev/null 2>&1; then
    promtool check metrics < "$FILE" || fail 'The textfile is not valid exposition format'
fi
grep -q "swirl_cloud_cost.prom.tmp $STATE/node_exporter/textfile_collector/swirl_cloud_cost.prom$" "$STATE/sudo.log" || \
    fail 'The file is not written next to the target and renamed'

refuse() {
    local message="$1" output before
    shift
    before="$(cat "$FILE")"
    if output="$(cost "$@")"; then fail "cost $* is accepted"; fi
    [[ "$output" == *"$message"* ]] || fail "cost $* failed with '$output', expected '$message'"
    [[ "$(cat "$FILE")" == "$before" ]] || fail "cost $* changed the recorded costs"
}
refuse "--monthly-cost must be an amount" --monthly-cost 89,90
refuse "--monthly-cost must be an amount" --monthly-cost 1.999
refuse "--monthly-cost must be an amount" --monthly-cost ""
refuse "three-letter ISO 4217 code" --monthly-cost 89 --currency euro
refuse "--price-date must be a date" --monthly-cost 89 --price-date 2026-02-30
refuse "--price-date must be a date" --monthly-cost 89 --price-date yesterday
refuse "cannot be in the future" --monthly-cost 89 --price-date "$(date -u -d '+2 days' +%Y-%m-%d)"
refuse "--price-source must be up to 120" --monthly-cost 89 --price-source 'say "hi"'
refuse "--price-source must be up to 120" --monthly-cost 89 --price-source $'two\nlines'
refuse "lowercase item name" --monthly-cost 89 --shared Backup=1
refuse "lowercase item name" --monthly-cost 89 --shared backup
refuse "the amount must look like" --monthly-cost 89 --shared backup=lots
refuse "is given twice" --monthly-cost 89 --shared backup=1 --shared backup=2
refuse "Unknown option" --monthly-cost 89 --bogus

cost --remove >/dev/null
[[ ! -e "$FILE" ]] || fail '--remove leaves the cost metrics'

printf '[PASS] Server cost metrics\n'
