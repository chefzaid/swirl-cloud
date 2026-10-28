#!/usr/bin/env bash
# Hardware health on bare-metal nodes, sourced by scripts/configure-node-security.sh.
# node-exporter already reads software RAID (mdadm), memory ECC (EDAC) and sensor
# temperatures (hwmon); this adds disk SMART health, NVMe wear and BMC sensors
# (IPMI) through the textfile collector. Virtual machines have no physical disks
# to read: they get nothing, and a previous install is removed. Alert rules: k8s/files/prometheus/rules.yaml.

configure_hardware_health() {
  if systemd-detect-virt --vm --quiet; then
    if [[ -e /etc/systemd/system/swirl-cloud-smart-metrics.timer ]]; then
      sudo systemctl disable --now swirl-cloud-smart-metrics.timer >/dev/null 2>&1 || true
      sudo rm -f /etc/systemd/system/swirl-cloud-smart-metrics.{service,timer} /usr/local/sbin/swirl-cloud-smart-metrics \
        /var/lib/node_exporter/textfile_collector/swirl_cloud_smart.prom /etc/modules-load.d/swirl-cloud-ipmi.conf
      sudo systemctl daemon-reload
    fi
    return 0
  fi
  if ! command -v smartctl >/dev/null 2>&1; then
    apt_update
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends smartmontools >/dev/null
  fi
  # A BMC appears as an IPMI device once its drivers load; without one they fail.
  sudo modprobe -q ipmi_devintf >/dev/null 2>&1 || true
  sudo modprobe -q ipmi_si >/dev/null 2>&1 || true
  if [[ -e /dev/ipmi0 || -e /dev/ipmi/0 || -e /dev/ipmidev/0 ]]; then
    printf 'ipmi_devintf\nipmi_si\n' | write_root_file /etc/modules-load.d/swirl-cloud-ipmi.conf
    if ! command -v ipmitool >/dev/null 2>&1; then
      apt_update
      sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends ipmitool >/dev/null
    fi
  fi
  write_root_file /usr/local/sbin/swirl-cloud-smart-metrics 0755 <<'EOF'
#!/usr/bin/env python3
# Managed by scripts/configure-node-security.sh
"""Write SMART health, NVMe wear and reallocated sectors of every local disk, and the BMC's
threshold sensors, for node-exporter. A disk smartctl cannot read is reported as
unreadable, never as healthy; so is a BMC ipmitool cannot read. iSCSI disks, such as
Longhorn volumes, have no hardware behind them and are left out."""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

METRICS = "/var/lib/node_exporter/textfile_collector/swirl_cloud_smart.prom"
IPMI_DEVICES = ("/dev/ipmi0", "/dev/ipmi/0", "/dev/ipmidev/0")
SYS_BLOCK = "/sys/block"
BMC_STATUS = {"ok": 0, "nc": 1, "cr": 2, "nr": 3}  # ipmitool's threshold states.
FAMILIES = {
    "swirl_cloud_smart_read_ok": "Whether smartctl returned this disk's health.",
    "swirl_cloud_smart_healthy": "Whether the disk's SMART overall health self-assessment passed.",
    "swirl_cloud_nvme_percentage_used": "NVMe endurance used, in percent of the rated life.",
    "swirl_cloud_smart_reallocated_sectors": "Reallocated sector count (ATA attribute 5).",
}
BMC_FAMILIES = (  # Name, description and the sensor tuple's column.
    ("swirl_cloud_bmc_sensor_value", "BMC sensor reading, in the unit label's unit.", 1),
    ("swirl_cloud_bmc_sensor_status", "BMC sensor state: 0 ok, 1 non-critical, 2 critical, 3 non-recoverable.", 2),
)


def smartctl(*args):
    result = subprocess.run(["smartctl", "--json", *args], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}


def network_disk(name, sys_block=SYS_BLOCK):
    """An iSCSI disk sits under its session in sysfs; smartctl lists it as a plain SCSI disk."""
    return re.search(r"/session[0-9]+/", os.path.realpath(os.path.join(sys_block, os.path.basename(name)))) is not None


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


def label(value):
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def bmc_sensors(devices):
    """None without a BMC; otherwise whether ipmitool answered and its threshold
    sensors as (labels, reading, state). Discrete and unread sensors are skipped."""
    if not any(os.path.exists(device) for device in devices):
        return None
    try:
        result = subprocess.run(["ipmitool", "sensor"], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False, []
    sensors, seen = [], set()
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 4 or fields[3] not in BMC_STATUS:
            continue
        try:
            reading = float(fields[1])
        except ValueError:
            continue
        name, count = fields[0], 2
        while name in seen:  # A repeated series would reject the whole file.
            name, count = f"{fields[0]} #{count}", count + 1
        seen.add(name)
        sensors.append((f'sensor="{label(name)}",unit="{label(fields[2])}"', reading, BMC_STATUS[fields[3]]))
    return result.returncode == 0 and bool(result.stdout.strip()), sensors


def render(disks, bmc=None):
    lines = []
    for family, description in FAMILIES.items():
        rows = [f"{family}{{{labels}}} {samples[family]}" for labels, samples in disks if family in samples]
        if rows:
            lines += [f"# HELP {family} {description}", f"# TYPE {family} gauge", *rows]
    if bmc is not None:
        answered, sensors = bmc
        lines += ["# HELP swirl_cloud_bmc_read_ok Whether ipmitool returned the BMC's sensors.",
                  "# TYPE swirl_cloud_bmc_read_ok gauge", f"swirl_cloud_bmc_read_ok {int(answered)}"]
        for family, description, column in BMC_FAMILIES if sensors else ():
            lines += [f"# HELP {family} {description}", f"# TYPE {family} gauge",
                      *(f"{family}{{{sensor[0]}}} {sensor[column]:g}" for sensor in sensors)]
    lines += ["# HELP swirl_cloud_smart_metrics_timestamp_seconds When these metrics were written.",
              "# TYPE swirl_cloud_smart_metrics_timestamp_seconds gauge",
              f"swirl_cloud_smart_metrics_timestamp_seconds {int(time.time())}"]
    return "\n".join(lines) + "\n"


def main(path=METRICS, ipmi_devices=IPMI_DEVICES, sys_block=SYS_BLOCK):
    devices = smartctl("--scan-open").get("devices", [])
    disks = [disk_metrics(device["name"], device.get("type", "auto")) for device in devices
             if device.get("name") and not network_disk(device["name"], sys_block)]
    bmc = bmc_sensors(ipmi_devices)
    directory = os.path.dirname(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".swirl_cloud_smart.", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(render(disks, bmc))
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
