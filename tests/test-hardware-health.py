#!/usr/bin/env python3
"""Guards for bare-metal hardware health: a disk smartctl cannot read is reported
as unreadable and never as healthy, BMC sensors keep their state and a BMC that
does not answer is reported, iSCSI disks (Longhorn volumes) are left out, the metrics
file is valid exposition format, and a virtual machine installs nothing and loses a
previous install."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "scripts/lib/hardware-health.sh"
DISKS = {
    "/dev/sda": {"smart_status": {"passed": True},
                 "ata_smart_attributes": {"table": [{"id": 5, "raw": {"value": 8}}, {"id": 9, "raw": {"value": 1000}}]}},
    "/dev/sdb": {"smart_status": {"passed": False}},
    "/dev/nvme0": {"smart_status": {"passed": True}, "nvme_smart_health_information_log": {"percentage_used": 7}},
    "/dev/sdc": None,  # smartctl prints no JSON: the disk does not answer.
}


def exporter_source():
    text = LIBRARY.read_text()
    return re.search(r"swirl-cloud-smart-metrics 0755 <<'EOF'\n(.*?)\nEOF\n", text, re.S)[1]


class HardwareHealthTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def stub(self, name, body):
        path = self.directory / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(body)
        path.chmod(0o755)

    def export(self, ipmi_devices=()):
        """Run the exporter with the stubbed tools; return the metrics file."""
        if not (self.directory / "bin" / "smartctl").exists():
            self.stub("smartctl", "#!/bin/sh\necho '{}'\n")
        exporter = self.directory / "exporter.py"
        exporter.write_text(exporter_source())
        metrics = self.directory / "smart.prom"
        sys_block = self.directory / "sys-block"  # Every disk is local unless a test links it to a session.
        program = (f"import runpy; module = runpy.run_path({str(exporter)!r}); "
                   f"module['main']({str(metrics)!r}, {[str(device) for device in ipmi_devices]!r}, {str(sys_block)!r})")
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                                env={**os.environ, "PATH": f"{self.directory / 'bin'}:{os.environ['PATH']}"})
        self.assertEqual(0, result.returncode, result.stderr)
        text = metrics.read_text()
        if shutil.which("promtool"):
            check = subprocess.run(["promtool", "check", "metrics"], input=text, capture_output=True, text=True)
            self.assertEqual(0, check.returncode, check.stdout + check.stderr)
        return text

    def test_unreadable_disk_is_never_reported_healthy(self):
        scan = {"devices": [{"name": name, "type": "nvme" if "nvme" in name else "sat"} for name in DISKS]}
        self.stub("smartctl", textwrap.dedent(f"""\
            #!{sys.executable}
            import json, sys
            disks, scan = {json.dumps(DISKS)!r}, {json.dumps(scan)!r}
            args = sys.argv[1:]
            if "--scan-open" in args:
                print(scan)
            else:
                data = json.loads(disks)[args[-1]]
                print(json.dumps(data) if data else "smartctl: open failed")
            """))
        text = self.export()
        self.assertIn('swirl_cloud_smart_read_ok{device="/dev/sdc"} 0', text)
        self.assertNotIn('swirl_cloud_smart_healthy{device="/dev/sdc"}', text)
        self.assertIn('swirl_cloud_smart_healthy{device="/dev/sdb"} 0', text)
        self.assertIn('swirl_cloud_smart_healthy{device="/dev/sda"} 1', text)
        self.assertIn('swirl_cloud_smart_reallocated_sectors{device="/dev/sda"} 8', text)
        self.assertIn('swirl_cloud_nvme_percentage_used{device="/dev/nvme0"} 7', text)
        self.assertEqual([], [p for p in self.directory.iterdir() if p.name.startswith(".swirl_cloud_smart.")])
        self.assertNotIn("swirl_cloud_bmc_", text)  # No IPMI device: no BMC metrics.

    def test_iscsi_disks_are_left_out(self):
        scan = {"devices": [{"name": "/dev/sda", "type": "sat"}, {"name": "/dev/sdb", "type": "scsi"}]}
        self.stub("smartctl", textwrap.dedent(f"""\
            #!/bin/sh
            case "$*" in
              *--scan-open*) echo {json.dumps(json.dumps(scan))} ;;
              *) echo '{{"smart_status": {{"passed": true}}}}' ;;
            esac
            """))
        devices = self.directory / "devices"
        for name, parent in (("sda", "pci0000:00/0000:00:1f.2/ata1/host0/target0:0:0/0:0:0:0"),
                             ("sdb", "platform/host6/session394/target6:0:0/6:0:0:1")):
            (devices / parent / "block" / name).mkdir(parents=True)
            (self.directory / "sys-block").mkdir(exist_ok=True)
            (self.directory / "sys-block" / name).symlink_to(devices / parent / "block" / name)
        text = self.export()
        self.assertIn('swirl_cloud_smart_healthy{device="/dev/sda"} 1', text)
        self.assertNotIn("/dev/sdb", text)

    def test_bmc_sensors_keep_their_state(self):
        device = self.directory / "ipmi0"
        device.write_text("")
        self.stub("ipmitool", textwrap.dedent("""\
            #!/bin/sh
            cat <<'EOF'
            CPU1 Temp        | 45.000     | degrees C  | ok    | na        | 0.000     | 5.000     | 85.000    | 90.000    | 95.000
            FAN1             | 0.000      | RPM        | cr    | na        | 300.000   | 500.000   | na        | na        | na
            Inlet "Temp"     | 41.000     | degrees C  | nc    | na        | na        | na        | 40.000    | 45.000    | na
            CPU1 Temp        | 47.000     | degrees C  | ok    | na        | 0.000     | 5.000     | 85.000    | 90.000    | 95.000
            PS1 Status       | 0x1        | discrete   | 0x0100| na        | na        | na        | na        | na        | na
            FAN2             | na         | RPM        | na    | na        | na        | na        | na        | na        | na
            EOF
            """))
        text = self.export([device])
        self.assertIn("swirl_cloud_bmc_read_ok 1", text)
        self.assertIn('swirl_cloud_bmc_sensor_status{sensor="FAN1",unit="RPM"} 2', text)
        self.assertIn('swirl_cloud_bmc_sensor_status{sensor="Inlet \\"Temp\\"",unit="degrees C"} 1', text)
        self.assertIn('swirl_cloud_bmc_sensor_value{sensor="CPU1 Temp",unit="degrees C"} 45', text)
        self.assertIn('swirl_cloud_bmc_sensor_value{sensor="CPU1 Temp #2",unit="degrees C"} 47', text)
        self.assertNotIn("PS1 Status", text)
        self.assertNotIn("FAN2", text)
        # A BMC that stops answering midway is not read, whatever it printed.
        for output in ("", "FAN1 | 900.000 | RPM | ok | na | na | na | na | na | na\n"):
            self.stub("ipmitool", f"#!/bin/sh\nprintf '{output}'\necho 'Get Device ID command failed' >&2\nexit 1\n")
            self.assertIn("swirl_cloud_bmc_read_ok 0", self.export([device]))

    def test_virtual_machine_installs_nothing_and_removes_a_previous_install(self):
        log = self.directory / "calls.log"
        self.stub("systemd-detect-virt", "#!/bin/sh\nexit 0\n")
        program = textwrap.dedent(f"""\
            set -euo pipefail
            sudo() {{ printf 'sudo %s\\n' "$*" >> {log}; }}
            write_root_file() {{ printf 'write %s\\n' "$1" >> {log}; cat >/dev/null; }}
            apt_update() {{ printf 'apt\\n' >> {log}; }}
            source {LIBRARY}
            configure_hardware_health
            """)
        etc = self.directory / "etc-timer"
        for present in (False, True):
            with self.subTest(previous_install=present):
                log.write_text("")
                # The library checks this path for a previous install.
                script = program.replace("/etc/systemd/system/swirl-cloud-smart-metrics.timer", str(etc))
                library = self.directory / "hardware-health.sh"
                library.write_text(LIBRARY.read_text().replace("/etc/systemd/system/swirl-cloud-smart-metrics.timer", str(etc)))
                script = script.replace(str(LIBRARY), str(library))
                if present:
                    etc.write_text("")
                result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                        env={**os.environ, "PATH": f"{self.directory / 'bin'}:{os.environ['PATH']}"})
                self.assertEqual(0, result.returncode, result.stderr)
                calls = log.read_text()
                self.assertNotIn("write ", calls)
                self.assertNotIn("apt", calls)
                self.assertEqual(present, "rm -f" in calls, calls)


if __name__ == "__main__":
    unittest.main()
