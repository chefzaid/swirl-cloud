#!/usr/bin/env bash
# Shared interactive prompt primitives. Callers own strict-mode settings and
# validation; these functions only collect values consistently.

installer_prompt_section() {
  local title="$1"
  shift
  printf '\n== %s ==\n' "$title" >&2
  if (( $# > 0 )); then
    printf '  %s\n' "$@" >&2
  fi
}

installer_prompt_value() {
  local variable_name="$1" prompt_text="$2" default_value="${3:-}" answer=""
  read -rp "$prompt_text${default_value:+ [$default_value]}: " answer
  printf -v "$variable_name" '%s' "${answer:-$default_value}"
}

installer_prompt_secret() {
  local variable_name="$1" prompt_text="$2" answer=""
  IFS= read -rsp "$prompt_text: " answer
  printf '\n' >&2
  printf -v "$variable_name" '%s' "$answer"
}

installer_prompt_confirmed_secret() {
  local variable_name="$1" prompt_text="$2" confirmation_text="$3"
  local value="" confirmation=""

  installer_prompt_secret value "$prompt_text"
  installer_prompt_secret confirmation "$confirmation_text"
  if [[ "$value" != "$confirmation" ]]; then
    value=""
    confirmation=""
    printf -v "$variable_name" '%s' ''
    return 1
  fi
  printf -v "$variable_name" '%s' "$value"
  value=""
  confirmation=""
}

installer_prompt_yes_no() {
  local prompt_text="$1" default_choice="${2:-N}" non_interactive="${3:-false}"
  local answer="" suffix=""

  case "${default_choice^^}" in
    Y) default_choice=Y; suffix='[Y/n]' ;;
    N) default_choice=N; suffix='[y/N]' ;;
    *) printf '[ERROR] Invalid yes/no default: %s\n' "$default_choice" >&2; return 2 ;;
  esac
  if [[ "$non_interactive" == "true" ]]; then
    [[ "$default_choice" == "Y" ]]
    return
  fi
  read -rp "$prompt_text $suffix " answer
  answer="${answer:-$default_choice}"
  [[ "$answer" =~ ^[Yy]$ ]]
}

installer_select_node_transport() {
  local variable_name="$1" current_value="${2:-}" default_value="${3:-vrack}"
  local non_interactive="${4:-false}" answer="" selected="" default_choice

  case "${current_value,,}" in
    vrack|v-rack|ovh) selected=vrack ;;
    tailscale|tailnet|ts) selected=tailscale ;;
    lan|local|private-network) selected=lan ;;
    '') ;;
    *) return 2 ;;
  esac
  if [[ -n "$selected" ]]; then
    printf -v "$variable_name" '%s' "$selected"
    return
  fi
  [[ "$non_interactive" != "true" ]] || return 1

  case "${default_value,,}" in
    tailscale|tailnet|ts) default_value=tailscale; default_choice=2 ;;
    lan|local|private-network) default_value=lan; default_choice=3 ;;
    *) default_value=vrack; default_choice=1 ;;
  esac
  printf '%s\n' \
    "Select the private node transport:" \
    "  1) OVHcloud vRack (OVHcloud-only)" \
    "  2) Tailscale (hybrid cloud or non-OVHcloud providers)" \
    "  3) Local LAN (nodes already share a private subnet; no provider API or overlay)" >&2
  while true; do
    read -rp "Select 1, 2 or 3 [$default_choice]: " answer
    case "${answer:-$default_value}" in
      1|vrack|vRack|v-rack|ovh) selected=vrack ;;
      2|tailscale|tailnet|ts) selected=tailscale ;;
      3|lan|LAN|local|private-network) selected=lan ;;
      *) printf 'Enter 1 for vRack, 2 for Tailscale or 3 for a local LAN.\n' >&2; continue ;;
    esac
    printf -v "$variable_name" '%s' "$selected"
    return
  done
}
