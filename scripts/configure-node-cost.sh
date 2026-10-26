#!/usr/bin/env bash
# Publish this server's monthly cost for cost showback.
#
#   configure-node-cost.sh --monthly-cost 89.90 [--currency EUR] [--shared backup-storage=12.50 ...]
#                          [--price-date 2026-10-26] [--price-source "Hetzner AX52 order"]
#   configure-node-cost.sh --show
#   configure-node-cost.sh --remove
#
# Writes node-exporter textfile metrics that Prometheus allocates to workloads
# (see docs/guides/cost-showback.md). Use the server's fixed monthly price
# including tax: its rent, or its purchase price spread over its service life
# plus hosting and power. Showback shares that price out; it is not usage
# billing. --shared records platform costs not tied to a server (backup
# storage, domains, licences); give them on one server only so they are not
# counted twice. --price-date (default today) and --price-source date the
# prices, so the Platform Report can flag those older than six months.
set -Eeuo pipefail

METRICS_DIR=/var/lib/node_exporter/textfile_collector
METRICS_FILE="$METRICS_DIR/swirl_cloud_cost.prom"
AMOUNT='^[0-9]{1,9}([.][0-9]{1,2})?$'
# A Prometheus label value: no quotes, backslashes or newlines to escape.
SOURCE='^[A-Za-z0-9][A-Za-z0-9 ._:/@()+,-]{0,119}$'

error() { echo "[ERROR] $*" >&2; exit 1; }
usage() { sed -n '2,16s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

monthly_cost=""
currency=EUR
shared=()
price_date="$(date -u +%Y-%m-%d)"
price_source="not recorded"
action="write"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --monthly-cost) [[ $# -ge 2 ]] || error "--monthly-cost needs an amount"; monthly_cost="$2"; shift 2 ;;
    --currency) [[ $# -ge 2 ]] || error "--currency needs a code"; currency="$2"; shift 2 ;;
    --shared) [[ $# -ge 2 ]] || error "--shared needs ITEM=AMOUNT"; shared+=("$2"); shift 2 ;;
    --price-date) [[ $# -ge 2 ]] || error "--price-date needs a date"; price_date="$2"; shift 2 ;;
    --price-source) [[ $# -ge 2 ]] || error "--price-source needs a description"; price_source="$2"; shift 2 ;;
    --show) action=show; shift ;;
    --remove) action=remove; shift ;;
    -h|--help) usage; exit 0 ;;
    *) error "Unknown option: $1 (see --help)" ;;
  esac
done

case "$action" in
  show)
    if [[ -r "$METRICS_FILE" ]]; then grep -v '^#' "$METRICS_FILE"; else echo "No cost recorded on this server."; fi
    exit 0
    ;;
  remove)
    sudo rm -f "$METRICS_FILE"
    echo "Removed the cost metrics from this server."
    exit 0
    ;;
esac

[[ "$monthly_cost" =~ $AMOUNT ]] || error "--monthly-cost must be an amount such as 89 or 89.90."
currency="${currency^^}"
[[ "$currency" =~ ^[A-Z]{3}$ ]] || error "--currency must be a three-letter ISO 4217 code such as EUR."
[[ "$price_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] && priced="$(date -u -d "$price_date" +%s 2>/dev/null)" && \
  [[ "$(date -u -d "@$priced" +%Y-%m-%d)" == "$price_date" ]] || error "--price-date must be a date such as 2026-10-26."
(( priced <= $(date -u +%s) )) || error "--price-date cannot be in the future."
[[ "$price_source" =~ $SOURCE ]] || \
  error "--price-source must be up to 120 letters, digits, spaces or ._:/@()+,- characters, for example \"Hetzner AX52 order\"."

metrics="# HELP swirl_cloud_node_monthly_cost Monthly cost of this server (rent, or amortized purchase plus hosting).
# TYPE swirl_cloud_node_monthly_cost gauge
swirl_cloud_node_monthly_cost{currency=\"$currency\"} $monthly_cost
# HELP swirl_cloud_node_cost_priced_timestamp_seconds Date of the prices recorded on this server, with their source.
# TYPE swirl_cloud_node_cost_priced_timestamp_seconds gauge
swirl_cloud_node_cost_priced_timestamp_seconds{source=\"$price_source\"} $priced
"
if [[ ${#shared[@]} -gt 0 ]]; then
  metrics+="# HELP swirl_cloud_shared_monthly_cost Monthly platform cost not tied to one server.
# TYPE swirl_cloud_shared_monthly_cost gauge
"
  for entry in "${shared[@]}"; do
    item="${entry%%=*}"
    amount="${entry#*=}"
    [[ "$entry" == *=* && "$item" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || \
      error "--shared must be ITEM=AMOUNT with a lowercase item name, for example backup-storage=12.50."
    [[ "$amount" =~ $AMOUNT ]] || error "--shared $item: the amount must look like 12 or 12.50."
    metrics+="swirl_cloud_shared_monthly_cost{currency=\"$currency\",item=\"$item\"} $amount
"
  done
fi

sudo install -d -o root -g root -m 0755 "$METRICS_DIR"
temporary="$(mktemp)"
trap 'rm -f "$temporary"' EXIT
printf '%s' "$metrics" > "$temporary"
# Write next to the target and rename, so node-exporter never reads a partial file.
sudo install -o root -g root -m 0644 "$temporary" "$METRICS_FILE.tmp"
sudo mv -f "$METRICS_FILE.tmp" "$METRICS_FILE"
echo "Recorded a monthly cost of $monthly_cost $currency for $(hostname)${shared:+ and ${#shared[@]} shared cost(s)}, priced $price_date ($price_source)."
