#!/usr/bin/env bash
# shellcheck disable=SC2034 # APT_UPDATED is shared with configure-node-security.sh.
# CrowdSec and Fail2ban packages and configuration files for
# configure-node-security.sh, which provides logging and root-file helpers.

ensure_crowdsec_repo() {
  local candidate

  candidate="$(apt-cache policy crowdsec 2>/dev/null | awk '/Candidate:/ {candidate=$2} END {print candidate}')"
  if [[ -n "$candidate" && "$candidate" != "(none)" ]]; then
    return 0
  fi

  info "Installing CrowdSec package repository..."
  ensure_packages ca-certificates curl gpg
  curl -fsSL https://install.crowdsec.net | sudo -E sh >/dev/null
  APT_UPDATED=false
}

ensure_crowdsec_collection() {
  local collection="$1"

  if sudo cscli collections list -o raw 2>/dev/null | awk -v target="$collection" '$1 == target {found=1} END {exit(found ? 0 : 1)}'; then
    return 0
  fi

  info "Installing CrowdSec collection ${collection}..."
  sudo cscli collections install "$collection" >/dev/null
}

write_fail2ban_config() {
  backup_file_if_exists /etc/fail2ban/jail.local
  write_root_file /etc/fail2ban/jail.local <<EOF
# Managed by scripts/configure-node-security.sh
[DEFAULT]
# Stricter SSH brute-force policy: fewer attempts, longer observation window,
# longer bans, and exponential repeat-offender escalation.
bantime = 24h
bantime.increment = true
bantime.factor = 2
bantime.rndtime = 10m
bantime.maxtime = 30d
findtime = 1h
maxretry = 3
backend = systemd
usedns = warn
banaction = ufw

[sshd]
enabled = true
port = $HARDENED_SSH_PORT
filter = sshd[mode=aggressive]
backend = systemd
journalmatch = _SYSTEMD_UNIT=ssh.service + _COMM=sshd
EOF
}

write_crowdsec_profiles() {
  backup_file_if_exists /etc/crowdsec/profiles.yaml
  write_root_file /etc/crowdsec/profiles.yaml <<'EOF'
name: default_ip_remediation
#debug: true
filters:
 - Alert.Remediation == true && Alert.GetScope() == "Ip"
decisions:
 - type: ban
   duration: 7d
# notifications:
#   - slack_default  # Set the webhook in /etc/crowdsec/notifications/slack.yaml before enabling this.
#   - splunk_default # Set the splunk url and token in /etc/crowdsec/notifications/splunk.yaml before enabling this.
#   - http_default   # Set the required http parameters in /etc/crowdsec/notifications/http.yaml before enabling this.
#   - email_default  # Set the required email parameters in /etc/crowdsec/notifications/email.yaml before enabling this.
on_success: break
---
name: default_range_remediation
#debug: true
filters:
 - Alert.Remediation == true && Alert.GetScope() == "Range"
decisions:
 - type: ban
   duration: 7d
# notifications:
#   - slack_default  # Set the webhook in /etc/crowdsec/notifications/slack.yaml before enabling this.
#   - splunk_default # Set the splunk url and token in /etc/crowdsec/notifications/splunk.yaml before enabling this.
#   - http_default   # Set the required http parameters in /etc/crowdsec/notifications/http.yaml before enabling this.
#   - email_default  # Set the required email parameters in /etc/crowdsec/notifications/email.yaml before enabling this.
on_success: break
EOF
}

write_crowdsec_ssh_time_based_scenario() {
  backup_file_if_exists /etc/crowdsec/scenarios/ssh-time-based-bf.yaml
  write_root_file /etc/crowdsec/scenarios/ssh-time-based-bf.yaml <<'EOF'
# ssh time-based bruteforce with false positive reduction
type: conditional
name: crowdsecurity/ssh-time-based-bf
description: "Detect time-based ssh bruteforce attempts that evade rate limiting (with false positive reduction)"
filter: "evt.Meta.service == 'ssh' && evt.Meta.log_type in ['ssh_failed-auth', 'auth_success']"
groupby: evt.Meta.source_ip
capacity: -1
cancel_on: "evt.Meta.log_type == 'auth_success'"
condition: |
    let failedAuths = filter(queue.Queue, {#.Meta.log_type == 'ssh_failed-auth'});
    len(failedAuths) >= 4 &&
    MedianInterval(map(failedAuths[-4:], {#.Time})) > duration("10m")
leakspeed: 2h
blackhole: 5m
reprocess: true
labels:
  service: ssh
  behavior: "ssh:bruteforce"
  spoofable: 0
  confidence: 3
  classification:
    - attack.T1110
  label: "SSH Time-Based Bruteforce"
  remediation: true
---
# ssh user-enum time-based with false positive reduction
type: conditional
name: crowdsecurity/ssh-time-based-bf_user-enum
description: "Detect time-based ssh user enum bruteforce attempts (with false positive reduction)"
filter: "evt.Meta.service == 'ssh' && evt.Meta.log_type in ['ssh_failed-auth', 'auth_success']"
groupby: evt.Meta.source_ip
distinct: evt.Meta.target_user
capacity: -1
cancel_on: "evt.Meta.log_type == 'auth_success'"
condition: |
    let failedAuths = filter(queue.Queue, {#.Meta.log_type == 'ssh_failed-auth'});
    len(failedAuths) >= 4 &&
    MedianInterval(map(failedAuths[-4:], {#.Time})) > duration("10m")
leakspeed: 2h
blackhole: 5m
reprocess: true
labels:
  service: ssh
  behavior: "ssh:bruteforce"
  spoofable: 0
  confidence: 3
  classification:
    - attack.T1589
    - attack.T1110
  label: "SSH Time-Based User Enumeration"
  remediation: true
EOF
}

configure_crowdsec_inotify_limits() {
  write_root_file /etc/sysctl.d/99-crowdsec-inotify.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
# Allow CrowdSec and container-heavy workloads to create enough filesystem watches.
fs.inotify.max_user_instances = 1024
fs.inotify.max_user_watches = 1048576
EOF

  sudo sysctl -w fs.inotify.max_user_instances=1024 >/dev/null
  sudo sysctl -w fs.inotify.max_user_watches=1048576 >/dev/null
}

write_crowdsec_acquisitions() {
  backup_file_if_exists /etc/crowdsec/acquis.d/setup.linux.yaml
  backup_file_if_exists /etc/crowdsec/acquis.d/setup.sshd.yaml
  backup_file_if_exists /etc/crowdsec/acquis.yaml
  sudo rm -f /etc/crowdsec/acquis.yaml

  write_root_file /etc/crowdsec/acquis.d/setup.linux.yaml <<'EOF'
# Managed by scripts/configure-node-security.sh
filenames:
  - /var/log/messages
  - /var/log/syslog
  - /var/log/kern.log
labels:
  type: syslog
  source: file
EOF

  write_root_file /etc/crowdsec/acquis.d/setup.sshd.yaml <<'EOF'
# Managed by scripts/configure-node-security.sh
filenames:
  - /var/log/auth.log
  - /var/log/secure
labels:
  type: syslog
  source: file
EOF
}

get_crowdsec_bouncer_api_key() {
  local config_path="/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml"
  local api_key=""
  local bouncer_name=""

  if sudo test -f "$config_path"; then
    api_key="$(sudo awk -F': ' '$1=="api_key"{gsub(/"/,"",$2); print $2; exit}' "$config_path")"
  fi

  if [[ -z "$api_key" ]]; then
    bouncer_name="swirl-cloud-$(hostname -s)-firewall-bouncer-$(date +%s)"
    api_key="$(sudo cscli bouncers add "$bouncer_name" -o raw 2>/dev/null || true)"
  fi

  [[ -n "$api_key" ]] || err "Unable to determine CrowdSec bouncer API key"
  printf '%s\n' "$api_key"
}

write_crowdsec_bouncer_config() {
  local api_key="$1"

  backup_file_if_exists /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
  write_root_file /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml <<EOF
# Managed by scripts/configure-node-security.sh
mode: nftables
update_frequency: 10s
log_mode: file
log_dir: /var/log/
log_level: info
log_compression: true
log_max_size: 100
log_max_backups: 3
log_max_age: 30
api_url: http://127.0.0.1:8080/
api_key: ${api_key}
insecure_skip_verify: false
disable_ipv6: false
deny_action: DROP
deny_log: false
supported_decisions_types:
  - ban
blacklists_ipv4: crowdsec-blacklists
blacklists_ipv6: crowdsec6-blacklists
ipset_type: nethash
iptables_chains:
  - INPUT
iptables_add_rule_comments: true
nftables:
  ipv4:
    enabled: true
    set-only: false
    table: crowdsec
    chain: crowdsec-chain
    priority: -10
  ipv6:
    enabled: true
    set-only: false
    table: crowdsec6
    chain: crowdsec6-chain
    priority: -10
nftables_hooks:
  - input
  - forward
pf:
  anchor_name: ""
prometheus:
  enabled: false
  listen_addr: 127.0.0.1
  listen_port: 60601
EOF
}
