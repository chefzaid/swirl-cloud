#!/usr/bin/env bash
# Firewall-independent host hardening for configure-node-security.sh: SSH,
# banners, packages, kernel, audit, integrity scans, metrics and log retention.
# Callers provide logging and root-file helpers.

configure_sshd() {
  local config_path="/etc/ssh/sshd_config.d/40-swirl-cloud-hardening.conf"
  local backup_path="/var/backups/swirl-cloud/config${config_path}.bak"

  [[ "$SSH_ALLOWED_USERS" =~ ^[a-zA-Z0-9_.@-]+([[:space:]]+[a-zA-Z0-9_.@-]+)*$ ]] || \
    err "SSH_ALLOWED_USERS must be a space-separated list of local usernames"

  backup_file_if_exists "$config_path"
  write_root_file "$config_path" 0600 <<EOF
# Managed by scripts/configure-node-security.sh
# Password login is intentionally retained by operator request.
PasswordAuthentication yes
PermitRootLogin no
MaxAuthTries 3
LoginGraceTime 30
AllowUsers $SSH_ALLOWED_USERS
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
DisableForwarding yes
PermitUserRC no
MaxStartups 10:30:30
PerSourceMaxStartups 3
EOF

  if ! sudo sshd -t; then
    if sudo test -f "$backup_path"; then
      sudo cp "$backup_path" "$config_path"
    else
      sudo rm -f "$config_path"
    fi
    err "Refusing to reload SSH because the hardened configuration failed validation"
  fi
  sudo systemctl reload ssh
}

configure_login_banner() {
  local path
  for path in /etc/issue /etc/issue.net; do
    backup_file_if_exists "$path"
    write_root_file "$path" 0644 <<'EOF'
Authorized access only. Activity on this system is monitored and recorded.
Disconnect immediately if you are not an authorized user.
EOF
  done
}

# Lynis DEB-0280, DEB-0811, PKGS-7370 and PKGS-7394. apt-listchanges uses the
# text frontend so interactive APT runs never stop in a pager.
configure_package_audit_tools() {
  printf '%s\n' 'apt-listchanges apt-listchanges/frontend select text' | sudo debconf-set-selections
  ensure_packages libpam-tmpdir apt-listchanges debsums apt-show-versions
  if ! sudo grep -Eq '^frontend[[:space:]]*=[[:space:]]*text$' /etc/apt/listchanges.conf 2>/dev/null; then
    sudo env DEBIAN_FRONTEND=noninteractive dpkg-reconfigure apt-listchanges >/dev/null
  fi
  # Weekly report-only package checksum verification; findings go to syslog.
  edit_root_file /etc/default/debsums 's/^CRON_CHECK=.*/CRON_CHECK=weekly/'
}

# Lynis PKGS-7346. Kernel updates leave config-only ("rc") packages behind.
# Other rc packages may be kept on purpose and are left alone.
purge_residual_kernel_packages() {
  local -a residual=()
  mapfile -t residual < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 2>/dev/null | \
    awk '$1 == "rc" && $2 ~ /^linux-(image|modules|modules-extra|main-modules-zfs|headers)-[0-9]/ {print $2}')
  if [[ ${#residual[@]} -gt 0 ]]; then
    info "Purging configuration left by ${#residual[@]} removed kernel package(s)."
    sudo env DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq "${residual[@]}" >/dev/null
  fi
}

# Lynis AUTH-9262. pam_pwquality applies only to password changes, not to
# existing passwords, key login or service accounts.
configure_password_quality() {
  ensure_packages libpam-pwquality
  write_root_file /etc/security/pwquality.conf.d/90-swirl-cloud.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
minlen = 12
dictcheck = 1
usercheck = 1
EOF
}

# Lynis KRNL-6000, limited to values that Kubernetes does not depend on.
# Deliberately unchanged: ip_forward and rp_filter (pod routing), modules_disabled
# (CNI, iSCSI and Longhorn load modules), sysrq (console recovery), suid_dumpable
# (owned by apport) and log_martians (every new pod sandbox logs benign martians).
configure_kernel_hardening() {
  write_root_file /etc/sysctl.d/90-swirl-cloud-hardening.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
kernel.kptr_restrict = 2
kernel.dmesg_restrict = 1
kernel.unprivileged_bpf_disabled = 1
net.core.bpf_jit_harden = 2
fs.protected_fifos = 2
fs.protected_regular = 2
fs.protected_symlinks = 1
fs.protected_hardlinks = 1
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
EOF
  sudo sysctl -q -p /etc/sysctl.d/90-swirl-cloud-hardening.conf
}

# Lynis NETW-3200: rare protocols no platform workload or Service uses. USB
# storage stays available because remote consoles attach recovery media as USB.
configure_unused_protocol_modules() {
  write_root_file /etc/modprobe.d/swirl-cloud-unused-protocols.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
blacklist dccp
install dccp /bin/false
blacklist rds
install rds /bin/false
blacklist sctp
install sctp /bin/false
blacklist tipc
install tipc /bin/false
EOF
}

# Lynis FILE-7524: root-only scheduler and SSH daemon configuration. Packaged
# paths get a dpkg statoverride so package upgrades keep the restricted mode.
configure_restricted_file_modes() {
  local mode path current owner group current_mode
  while read -r mode path; do
    sudo test -e "$path" || continue
    if dpkg -S "$path" >/dev/null 2>&1; then
      current="$(sudo dpkg-statoverride --list "$path" || true)"
      read -r owner group current_mode _ <<< "$current"
      if [[ "$owner" != root || "$group" != root || -z "$current_mode" ]] || \
          (( 8#$current_mode != 8#$mode )); then
        [[ -z "$current" ]] || sudo dpkg-statoverride --remove "$path" >/dev/null
        sudo dpkg-statoverride --update --add root root "$mode" "$path"
      fi
    fi
    sudo chown root:root "$path"
    sudo chmod "$mode" "$path"
  done <<'EOF'
0600 /etc/crontab
0600 /etc/ssh/sshd_config
0700 /etc/cron.d
0700 /etc/cron.hourly
0700 /etc/cron.daily
0700 /etc/cron.weekly
0700 /etc/cron.monthly
EOF
}

# Accepted Lynis findings. Each entry skips only the named check or value, so a
# new gap in the same test is still reported. Review every entry by its date.
configure_lynis_accepted_findings() {
  write_root_file /etc/lynis/custom.prf <<'EOF'
# Managed by scripts/configure-node-security.sh
# Accepted Lynis findings. Owner: platform administrators. Review by 2027-04-04.
# AUTH-9230: yescrypt hashing is stronger than the SHA-512 rounds this test reads.
skip-test=AUTH-9230
# AUTH-9328: UMASK 027 would make sudo/cron files and workspace sources group-only,
# breaking image COPY for non-root containers; user-private groups isolate users.
skip-test=AUTH-9328
# BOOT-5122: a GRUB password blocks remote console recovery.
skip-test=BOOT-5122
# FILE-6310: separate /home and /var partitions require reinstalling the host.
skip-test=FILE-6310
# USB-1000: remote consoles attach recovery media as USB storage.
skip-test=USB-1000
# LOGG-2154: Fluent Bit forwards the journal to Elasticsearch; Lynis only detects
# syslog-daemon forwarding. An off-host copy needs the Elasticsearch snapshot
# repository (TODO §14/§16).
skip-test=LOGG-2154
# SSH-7408: port 22 is kept for tooling; MaxSessions 4 leaves room for automation.
skip-test=SSH-7408:port
skip-test=SSH-7408:maxsessions
# KRNL-6000: Kubernetes routes pods (forwarding, loose rp_filter) and loads CNI,
# iSCSI and Longhorn modules; SysRq stays for console recovery; apport owns
# suid_dumpable; every new pod sandbox logs a benign martian.
skip-test=KRNL-6000:net.ipv4.conf.all.forwarding
skip-test=KRNL-6000:net.ipv4.conf.all.rp_filter
skip-test=KRNL-6000:kernel.modules_disabled
skip-test=KRNL-6000:kernel.sysrq
skip-test=KRNL-6000:fs.suid_dumpable
skip-test=KRNL-6000:net.ipv4.conf.all.log_martians
skip-test=KRNL-6000:net.ipv4.conf.default.log_martians
EOF
}

# Lynis SSH-7408 session options for every node. Client-alive probes detect dead
# sessions instead of TCP keepalives; a reload keeps existing sessions open.
configure_ssh_session_limits() {
  local config_path=/etc/ssh/sshd_config.d/41-swirl-cloud-sessions.conf
  local previous=""

  sudo test -f "$config_path" && previous="$(sudo cat "$config_path")"
  write_root_file "$config_path" 0600 <<'EOF'
# Managed by scripts/configure-node-security.sh
ClientAliveInterval 300
ClientAliveCountMax 2
TCPKeepAlive no
MaxSessions 4
LogLevel VERBOSE
EOF
  if ! sudo sshd -t; then
    if [[ -n "$previous" ]]; then
      printf '%s\n' "$previous" | write_root_file "$config_path" 0600
    else
      sudo rm -f "$config_path"
    fi
    err "Refusing to reload SSH because the session configuration failed validation"
  fi
  if [[ "$(sudo cat "$config_path")" != "$previous" ]] && systemctl is-active --quiet ssh; then
    sudo systemctl reload ssh
  fi
}

# node-exporter reads host metrics (such as the Lynis run) from here; the
# directory exists on every node so the collector never errors.
configure_metrics_textfile_directory() {
  sudo install -d -o root -g root -m 0755 /var/lib/node_exporter/textfile_collector
}

# Unattended upgrades never reboot. Export the pending-reboot flag so
# Prometheus can alert when a reboot stays outstanding.
configure_reboot_required_metrics() {
  write_root_file /usr/local/sbin/swirl-cloud-reboot-metrics 0755 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
set -euo pipefail
metrics_dir=/var/lib/node_exporter/textfile_collector
metrics_file="$metrics_dir/swirl_cloud_reboot.prom"
flag=/var/run/reboot-required
required=0
since=0
if [[ -e "$flag" ]]; then
  required=1
  # Later updates rewrite the flag; keep the first time this reboot was seen.
  if [[ -r "$metrics_file" ]]; then
    since="$(awk '$1 == "swirl_cloud_reboot_required_since_timestamp_seconds" {print $2}' "$metrics_file")"
  fi
  [[ "$since" =~ ^[1-9][0-9]*$ ]] || since="$(stat -c %Y "$flag")"
fi
temporary="$(mktemp "$metrics_dir/.swirl_cloud_reboot.XXXXXX")"
{
  printf '# HELP swirl_cloud_reboot_required Whether installed updates require a reboot.\n'
  printf '# TYPE swirl_cloud_reboot_required gauge\n'
  printf 'swirl_cloud_reboot_required %s\n' "$required"
  printf '# HELP swirl_cloud_reboot_required_since_timestamp_seconds When the pending reboot was first requested, or 0.\n'
  printf '# TYPE swirl_cloud_reboot_required_since_timestamp_seconds gauge\n'
  printf 'swirl_cloud_reboot_required_since_timestamp_seconds %s\n' "$since"
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_file"
EOF
  write_root_file /etc/systemd/system/swirl-cloud-reboot-metrics.service <<'EOF'
[Unit]
Description=Export the Swirl Cloud pending-reboot state for node-exporter

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-reboot-metrics
EOF
  write_root_file /etc/systemd/system/swirl-cloud-reboot-metrics.timer <<'EOF'
[Unit]
Description=Refresh the Swirl Cloud pending-reboot metric

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-reboot-metrics.timer >/dev/null
  sudo systemctl start swirl-cloud-reboot-metrics.service
}

# Lynis ACCT-9622/9628: process accounting and a small, watch-based audit rule
# set. Execve and time syscalls are not audited: CI pods and chrony would flood
# the log. Rules stay mutable (no -e 2) so reconciliation never needs a reboot;
# a full audit disk suspends auditing instead of halting the node.
configure_host_audit() {
  ensure_packages auditd acct
  write_root_file /etc/audit/rules.d/50-swirl-cloud.rules 0640 <<'EOF'
## Managed by scripts/configure-node-security.sh
## containerd/runc load a BPF device filter per container; skip those records.
-a never,exclude -F msgtype=BPF
## Ubuntu 26.04's who AppArmor profile denies the Rust coreutils binary reading
## /etc/passwd; SSH clients that poll who each second flood the log otherwise.
-a never,exclude -F exe=/usr/lib/cargo/bin/coreutils/who
-a always,exit -F path=/etc/passwd -F perm=wa -k identity
-a always,exit -F path=/etc/group -F perm=wa -k identity
-a always,exit -F path=/etc/shadow -F perm=wa -k identity
-a always,exit -F path=/etc/gshadow -F perm=wa -k identity
-a always,exit -F path=/etc/sudoers -F perm=wa -k privilege
-a always,exit -F dir=/etc/sudoers.d -F perm=wa -k privilege
-a always,exit -F path=/etc/ssh/sshd_config -F perm=wa -k sshd
-a always,exit -F dir=/etc/ssh/sshd_config.d -F perm=wa -k sshd
-a always,exit -F path=/etc/crontab -F perm=wa -k cron
-a always,exit -F dir=/etc/cron.d -F perm=wa -k cron
-a always,exit -F dir=/var/spool/cron -F perm=wa -k cron
-a always,exit -F dir=/etc/systemd/system -F perm=wa -k systemd
-a always,exit -F dir=/etc/rancher -F perm=wa -k k3s-config
-a always,exit -F dir=/etc/swirl-cloud -F perm=wa -k swirl-cloud
-a always,exit -F dir=/etc/ufw -F perm=wa -k firewall
-a always,exit -F path=/etc/localtime -F perm=wa -k time-change
-a always,exit -F arch=b64 -S init_module,finit_module,delete_module -k modules
EOF
  edit_root_file /etc/audit/auditd.conf 's/^max_log_file[[:space:]]*=.*/max_log_file = 32/; s/^num_logs[[:space:]]*=.*/num_logs = 8/'
  # Container churn writes roughly 250 MB of raw accounting a day; keep two weeks.
  edit_root_file /etc/logrotate.d/acct 's/^([[:space:]]*)rotate [0-9]+$/\1rotate 14/'
  sudo augenrules --load >/dev/null
  sudo systemctl enable --now auditd acct >/dev/null 2>&1
}

# Lynis HRDN-7230: weekly, report-only rkhunter scan. Recommends are skipped
# because they pull in a listening mail server. Warnings go to the journal and
# a node-exporter metric; package updates refresh the file-property baseline.
configure_rootkit_scan() {
  if ! dpkg -s rkhunter >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends rkhunter >/dev/null
  fi
  write_root_file /etc/rkhunter.conf.local 0640 <<'EOF'
# Managed by scripts/configure-node-security.sh
PKGMGR=DPKG
# PermitRootLogin no is set in sshd_config.d, which rkhunter does not read.
ALLOW_SSH_ROOT_USER=unset
# Java preferences, systemd's update stamp and systemd-resolved's backup.
ALLOWHIDDENDIR=/etc/.java
ALLOWHIDDENFILE=/etc/.updated
ALLOWHIDDENFILE=/etc/.resolv.conf.systemd-resolved.bak
EOF
  edit_root_file /etc/default/rkhunter 's/^APT_AUTOGEN=.*/APT_AUTOGEN="true"/'
  sudo rkhunter --config-check --nocolors >/dev/null || err "rkhunter rejected its managed configuration"
  if ! sudo test -s /var/lib/rkhunter/db/rkhunter.dat; then
    sudo rkhunter --propupd --nocolors >/dev/null
  fi
  write_root_file /usr/local/sbin/swirl-cloud-rkhunter-weekly 0750 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
set -uo pipefail
metrics_dir=/var/lib/node_exporter/textfile_collector
output="$(rkhunter --check --sk --rwo --nocolors 2>&1)"
status=$?
[[ -z "$output" ]] || printf '%s\n' "$output"
warnings="$(grep -c '^Warning:' <<< "$output")"
temporary="$(mktemp "$metrics_dir/.swirl_cloud_rkhunter.XXXXXX")"
{
  printf '# HELP swirl_cloud_rkhunter_last_run_timestamp_seconds Last rkhunter scan.\n'
  printf '# TYPE swirl_cloud_rkhunter_last_run_timestamp_seconds gauge\n'
  printf 'swirl_cloud_rkhunter_last_run_timestamp_seconds %s\n' "$(date +%s)"
  printf '# HELP swirl_cloud_rkhunter_exit_code Exit code of the last scan (1 means warnings).\n'
  printf '# TYPE swirl_cloud_rkhunter_exit_code gauge\n'
  printf 'swirl_cloud_rkhunter_exit_code %s\n' "$status"
  printf '# HELP swirl_cloud_rkhunter_warnings Warnings reported by the last scan.\n'
  printf '# TYPE swirl_cloud_rkhunter_warnings gauge\n'
  printf 'swirl_cloud_rkhunter_warnings %s\n' "$warnings"
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_dir/swirl_cloud_rkhunter.prom"
# Report-only: warnings are findings to review, not a failed run.
(( status <= 1 )) || exit "$status"
EOF
  write_root_file /etc/systemd/system/swirl-cloud-rkhunter.service <<'EOF'
[Unit]
Description=Swirl Cloud weekly rkhunter scan (report-only)

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-rkhunter-weekly
Nice=10
IOSchedulingClass=idle
EOF
  write_root_file /etc/systemd/system/swirl-cloud-rkhunter.timer <<'EOF'
[Unit]
Description=Run the Swirl Cloud rkhunter scan weekly

[Timer]
OnCalendar=Sun *-*-* 04:30:00
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-rkhunter.timer >/dev/null
}

# Record host maintenance service results for Prometheus. Drop-ins may exist
# before their unit, and the optional collector path never blocks a unit.
configure_host_unit_metrics() {
  local unit
  write_root_file /usr/local/sbin/swirl-cloud-unit-metrics 0755 <<'EOF'
#!/bin/bash
# Managed by scripts/configure-node-security.sh
# Called from ExecStopPost; systemd provides SERVICE_RESULT for the run.
set -euo pipefail
unit="$1"
[[ "$unit" =~ ^[A-Za-z0-9@._-]+$ ]] || exit 1
metrics_dir=/var/lib/node_exporter/textfile_collector
metrics_file="$metrics_dir/swirl_cloud_unit_${unit//[^A-Za-z0-9]/_}.prom"
now="$(date +%s)"
success=0
last_success=""
[[ "${SERVICE_RESULT:-}" != success ]] || success=1
if [[ -r "$metrics_file" ]]; then
  last_success="$(awk '$1 ~ /^swirl_cloud_systemd_unit_last_success_timestamp_seconds/ {print $2}' "$metrics_file")"
fi
(( success == 0 )) || last_success="$now"
temporary="$(mktemp "$metrics_dir/.swirl_cloud_unit.XXXXXX")"
{
  printf '# HELP swirl_cloud_systemd_unit_last_run_timestamp_seconds Last run of a host maintenance service.\n'
  printf '# TYPE swirl_cloud_systemd_unit_last_run_timestamp_seconds gauge\n'
  printf 'swirl_cloud_systemd_unit_last_run_timestamp_seconds{unit="%s"} %s\n' "$unit" "$now"
  printf '# HELP swirl_cloud_systemd_unit_last_run_success Whether the last run succeeded.\n'
  printf '# TYPE swirl_cloud_systemd_unit_last_run_success gauge\n'
  printf 'swirl_cloud_systemd_unit_last_run_success{unit="%s"} %s\n' "$unit" "$success"
  if [[ -n "$last_success" ]]; then
    printf '# HELP swirl_cloud_systemd_unit_last_success_timestamp_seconds Last successful run.\n'
    printf '# TYPE swirl_cloud_systemd_unit_last_success_timestamp_seconds gauge\n'
    printf 'swirl_cloud_systemd_unit_last_success_timestamp_seconds{unit="%s"} %s\n' "$unit" "$last_success"
  fi
} > "$temporary"
chmod 0644 "$temporary"
mv -f "$temporary" "$metrics_file"
EOF
  # "+" runs the hook with full privileges, also for units with their own user.
  for unit in swirl-cloud-k3s-backup.service swirl-cloud-k3s-backup-verify.service \
    swirl-cloud-vault-unseal.service dailyaidecheck.service; do
    write_root_file "/etc/systemd/system/$unit.d/50-swirl-cloud-metrics.conf" <<'EOF'
# Managed by scripts/configure-node-security.sh
[Service]
ReadWritePaths=-/var/lib/node_exporter/textfile_collector
ExecStopPost=-+/usr/local/sbin/swirl-cloud-unit-metrics %n
EOF
  done
  sudo systemctl daemon-reload
}

# Lynis FINT-4350: weekly, report-only AIDE check via the packaged dailyaidecheck
# units. High-churn paths are excluded and package changes filtered; reports go
# to the journal (no mailer). The first database build runs in the background.
configure_file_integrity() {
  if ! dpkg -s aide-common >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends aide aide-common >/dev/null
  fi
  write_root_file /etc/aide/aide.conf.d/98_swirl-cloud <<'EOF'
# Managed by scripts/configure-node-security.sh
!/var/lib/(rancher|longhorn|kubelet|containerd|docker|cni|fluent-bit|node_exporter)(/|$)
!/var/lib/swirl-cloud/filebeat-lynis(/|$)
!/var/log/(pods|containers|audit|account|lynis-reports|journal)(/|$)
!/var/backups/swirl-cloud(/|$)
!/run/.
!/var/lib/crowdsec/data(/|$)
!/(var/)?tmp/.
# Home directories: only ~/.ssh and login shell files are monitored.
!/home/[^/]+/(?!\.ssh(/|$)|\.(bashrc|profile|bash_profile|bash_logout)$)
EOF
  write_root_file /usr/local/sbin/swirl-cloud-aide-report 0755 <<'EOF'
#!/bin/sh
# Managed by scripts/configure-node-security.sh
# dailyaidecheck's MAILCMD: ignore the mail arguments and log the report.
exec systemd-cat -t aide-report
EOF
  edit_root_file /etc/default/aide 's/^COPYNEWDB=.*/COPYNEWDB=yes/; s/^FILTERUPDATES=.*/FILTERUPDATES=yes/; s/^FILTERINSTALLATIONS=.*/FILTERINSTALLATIONS=yes/'
  if sudo grep -q '^MAILCMD=' /etc/default/aide; then
    edit_root_file /etc/default/aide 's#^MAILCMD=.*#MAILCMD=/usr/local/sbin/swirl-cloud-aide-report#'
  else
    printf 'MAILCMD=/usr/local/sbin/swirl-cloud-aide-report\n' | sudo tee -a /etc/default/aide >/dev/null
  fi
  write_root_file /etc/systemd/system/dailyaidecheck.timer.d/50-swirl-cloud-weekly.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
[Timer]
OnCalendar=
OnCalendar=Sun *-*-* 02:50:00
RandomizedDelaySec=1h
EOF
  sudo aide --config=/etc/aide/aide.conf --config-check >/dev/null || err "AIDE rejected its managed configuration"
  sudo systemctl daemon-reload
  sudo systemctl enable --now dailyaidecheck.timer >/dev/null
  if ! sudo test -s /var/lib/aide/aide.db && ! systemctl is-active --quiet swirl-cloud-aide-init.service; then
    info "Building the initial AIDE database in the background (swirl-cloud-aide-init.service)."
    sudo systemd-run --quiet --unit=swirl-cloud-aide-init --property=Nice=10 \
      --property=IOSchedulingClass=idle /usr/sbin/aideinit --yes --force
  fi
}

# Controls that never touch the firewall or Kubernetes networking, so they are
# safe on a live host. SSH changes are limited to validated session options.
configure_host_baseline() {
  configure_login_banner
  configure_metrics_textfile_directory
  configure_reboot_required_metrics
  configure_host_unit_metrics
  configure_hardware_health
  configure_package_audit_tools
  purge_residual_kernel_packages
  configure_password_quality
  configure_kernel_hardening
  configure_host_audit
  configure_rootkit_scan
  configure_file_integrity
  configure_unused_protocol_modules
  configure_restricted_file_modes
  configure_lynis_accepted_findings
  configure_ssh_session_limits
  info "Host baseline applied."
}

disable_unused_rpcbind() {
  # NFSv4 does not need rpcbind; keep it only while an NFS mount is active.
  if findmnt -rn -t nfs,nfs4 >/dev/null 2>&1; then
    warn "An NFS mount is active; leaving rpcbind unchanged."
    return 0
  fi

  if systemctl list-unit-files rpcbind.service rpcbind.socket --no-legend 2>/dev/null | grep -q rpcbind; then
    sudo systemctl disable --now rpcbind.socket rpcbind.service >/dev/null 2>&1 || true
  fi
}

configure_log_retention() {
  local cloud_init_config="/etc/logrotate.d/cloud-init"
  local duplicate_config="/etc/logrotate.d/cloud-init-base"
  local legacy_backup

  write_root_file /etc/systemd/journald.conf.d/99-swirl-cloud-retention.conf <<'EOF'
# Managed by scripts/configure-node-security.sh
Storage=persistent
SystemMaxUse=4G
MaxRetentionSec=3month
Compress=yes
Seal=yes
EOF

  backup_file_if_exists /etc/logrotate.d/rsyslog
  write_root_file /etc/logrotate.d/rsyslog <<'EOF'
# Managed by scripts/configure-node-security.sh
# Keep authentication evidence for approximately three months while retaining
# the higher-volume general system logs for four weeks.
/var/log/auth.log
{
    rotate 13
    weekly
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
}

/var/log/syslog
/var/log/mail.log
/var/log/kern.log
/var/log/user.log
/var/log/cron.log
{
    rotate 4
    weekly
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate
    endscript
}
EOF

  # Some Ubuntu releases define the cloud-init log twice, which fails every
  # logrotate run. Move the duplicate out of logrotate.d.
  if sudo test -f "$cloud_init_config" && sudo test -f "$duplicate_config" && \
     sudo cmp -s "$cloud_init_config" "$duplicate_config"; then
    sudo install -d -o root -g root -m 0700 /var/backups/swirl-cloud/config/etc/logrotate.d
    sudo mv "$duplicate_config" /var/backups/swirl-cloud/config/etc/logrotate.d/cloud-init-base.disabled-swirl-cloud
  fi

  # logrotate reads *.bak-* files in logrotate.d as policies; move them out
  # before validating.
  while IFS= read -r legacy_backup; do
    sudo install -d -o root -g root -m 0700 /var/backups/swirl-cloud/config/etc/logrotate.d
    sudo mv "$legacy_backup" "/var/backups/swirl-cloud/config/etc/logrotate.d/$(basename "$legacy_backup")"
  done < <(sudo find /etc/logrotate.d -maxdepth 1 -type f -name '*.bak-*' -print)

  if sudo logrotate --debug /etc/logrotate.conf 2>&1 | grep -q '^error:'; then
    err "The generated logrotate configuration failed validation"
  fi
  sudo systemctl restart systemd-journald
  sudo systemctl reset-failed logrotate.service
  sudo systemctl start logrotate.service
}
