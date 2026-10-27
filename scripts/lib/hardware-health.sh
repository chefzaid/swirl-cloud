#!/usr/bin/env bash
# Hardware health on bare-metal nodes, sourced by scripts/configure-node-security.sh.
# node-exporter already reads software RAID (mdadm), memory ECC (EDAC) and sensor
# temperatures (hwmon); this adds disk SMART health and NVMe wear through the
# textfile collector. Virtual machines have no physical disks to read: they get
# nothing, and a previous install is removed. Alert rules: k8s/files/prometheus/rules.yaml.

configure_hardware_health() {
  if systemd-detect-virt --vm --quiet; then
    if [[ -e /etc/systemd/system/swirl-cloud-smart-metrics.timer ]]; then
      sudo systemctl disable --now swirl-cloud-smart-metrics.timer >/dev/null 2>&1 || true
      sudo rm -f /etc/systemd/system/swirl-cloud-smart-metrics.{service,timer} /usr/local/sbin/swirl-cloud-smart-metrics \
        /var/lib/node_exporter/textfile_collector/swirl_cloud_smart.prom
      sudo systemctl daemon-reload
    fi
    return 0
  fi
  if ! command -v smartctl >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends smartmontools >/dev/null
  fi
  write_root_file /usr/local/sbin/swirl-cloud-smart-metrics 0755 <<'EOF'
#!/usr/bin/env python3
# Managed by scripts/configure-node-security.sh
"""Write SMART health, NVMe wear and reallocated sectors of every disk for node-exporter.
A disk smartctl cannot read is reported as unreadable, never as healthy."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

METRICS = "/var/lib/node_exporter/textfile_collector/swirl_cloud_smart.prom"
FAMILIES = {
    "swirl_cloud_smart_read_ok": "Whether smartctl returned this disk's health.",
    "swirl_cloud_smart_healthy": "Whether the disk's SMART overall health self-assessment passed.",
    "swirl_cloud_nvme_percentage_used": "NVMe endurance used, in percent of the rated life.",
    "swirl_cloud_smart_reallocated_sectors": "Reallocated sector count (ATA attribute 5).",
}


def smartctl(*args):
    result = subprocess.run(["smartctl", "--json", *args], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def disk_metrics(name, kind):
    data = smartctl("-H", "-A", "-d", kind, name)
    labels = f'device="{re.sub(r"[^A-Za-z0-9/_.-]", "_", name)}"'
    passed = data.get("smart_status", {}).get("passed")
    samples = {"swirl_cloud_smart_read_ok": 1 if isinstance(passed, bool) else 0}
    if isinstance(passed, bool):
        samples["swirl_cloud_smart_healthy"] = int(passed)
    used = data.get("nvme_smart_health_information_log", {}).get("percentage_used")
    if isinstance(used, (int, float)):
        samples["swirl_cloud_nvme_percentage_used"] = used
    for attribute in data.get("ata_smart_attributes", {}).get("table", []):
        if attribute.get("id") == 5 and isinstance(attribute.get("raw", {}).get("value"), int):
            samples["swirl_cloud_smart_reallocated_sectors"] = attribute["raw"]["value"]
    return labels, samples


def render(disks):
    lines = []
    for family, description in FAMILIES.items():
        rows = [f"{family}{{{labels}}} {samples[family]}" for labels, samples in disks if family in samples]
        if rows:
            lines += [f"# HELP {family} {description}", f"# TYPE {family} gauge", *rows]
    lines += ["# HELP swirl_cloud_smart_metrics_timestamp_seconds When these metrics were written.",
              "# TYPE swirl_cloud_smart_metrics_timestamp_seconds gauge",
              f"swirl_cloud_smart_metrics_timestamp_seconds {int(time.time())}"]
    return "\n".join(lines) + "\n"


def main(path=METRICS):
    devices = smartctl("--scan-open").get("devices", [])
    disks = [disk_metrics(device["name"], device.get("type", "auto")) for device in devices if device.get("name")]
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".swirl_cloud_smart.", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(render(disks))
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    sys.exit(main())
EOF
  write_root_file /etc/systemd/system/swirl-cloud-smart-metrics.service <<'EOF'
[Unit]
Description=Export Swirl Cloud disk SMART health for node-exporter

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/swirl-cloud-smart-metrics
EOF
  write_root_file /etc/systemd/system/swirl-cloud-smart-metrics.timer <<'EOF'
[Unit]
Description=Refresh the Swirl Cloud disk SMART metrics hourly

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now swirl-cloud-smart-metrics.timer >/dev/null
  sudo systemctl start swirl-cloud-smart-metrics.service
}
