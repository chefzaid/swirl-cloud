#!/usr/bin/env python3
"""Guards for the ClamAV metrics exporter (k8s/files/clamav/exporter.mjs), run
against a fake clamd: it reports the daily signature version and when it was
published, the threads, queue and signature memory, and a test scan that sends
the EICAR file through INSTREAM. With clamd down it reports clamav_up 0 and a
failed test scan, never an old success."""
from pathlib import Path
import shutil
import socket
import socketserver
import subprocess
import threading
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = b"ClamAV 1.5.4/27821/Tue Nov 17 08:21:02 2026\0"
STATS = (b"POOLS: 1\n\nSTATE: VALID PRIMARY\nTHREADS: live 2  idle 6 max 8 idle-timeout 30\nQUEUE: 1 items\n"
         b"\tSTATS 0.000033 \n\nMEMSTATS: heap N/A mmap N/A used N/A free N/A releasable N/A pools 1 "
         b"pools_used 1288.393M pools_total 1290.500M\nEND\0")


class FakeClamd(socketserver.BaseRequestHandler):
    def exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def handle(self):
        command = b""
        while not command.endswith(b"\0"):
            command += self.exact(1)
        if command == b"zVERSION\0":
            self.request.sendall(VERSION)
        elif command == b"zSTATS\0":
            self.request.sendall(STATS)
        elif command == b"zINSTREAM\0":
            data = b""
            while size := int.from_bytes(self.exact(4), "big"):
                data += self.exact(size)
            self.server.streams.append(data)
            found = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data
            self.request.sendall(b"stream: Eicar-Signature FOUND\0" if found else b"stream: OK\0")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ClamAVExporterTest(unittest.TestCase):
    def scrape(self, clamd_port):
        port = free_port()
        env = {"PATH": "/usr/bin:/bin", "CLAMD_PORT": str(clamd_port), "METRICS_PORT": str(port), "CLAMD_TIMEOUT_MS": "2000"}
        exporter = subprocess.Popen([shutil.which("node"), str(ROOT / "k8s/files/clamav/exporter.mjs")], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(exporter.wait)
        self.addCleanup(exporter.kill)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
                    text = response.read().decode()
                break
            except OSError:
                time.sleep(0.1)
        else:
            self.fail("the exporter did not answer")
        return {line.rsplit(" ", 1)[0]: float(line.rsplit(" ", 1)[1])
                for line in text.splitlines() if line and not line.startswith("#")}

    def test_reports_signatures_threads_and_the_test_scan(self):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FakeClamd)
        server.streams = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        metrics = self.scrape(server.server_address[1])
        self.assertEqual(1, metrics["clamav_up"])
        self.assertEqual(1, metrics['clamav_build_info{version="1.5.4"}'])
        self.assertEqual(27821, metrics["clamav_signatures_version"])
        self.assertEqual(1794903662, metrics["clamav_signatures_published_timestamp_seconds"])  # 2026-11-17 08:21:02 UTC
        self.assertEqual((2, 6, 8), tuple(metrics[f'clamav_threads{{state="{state}"}}'] for state in ("live", "idle", "max")))
        self.assertEqual(1, metrics["clamav_queue_items"])
        self.assertEqual(round(1288.393 * 1024 ** 2), metrics["clamav_memory_pool_used_bytes"])
        self.assertEqual(1, metrics["clamav_canary_detected"])
        self.assertEqual(1, len(server.streams))
        self.assertTrue(server.streams[0].startswith(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR"))

    def test_clamd_down_is_a_failed_test_scan(self):
        metrics = self.scrape(free_port())
        self.assertEqual(0, metrics["clamav_up"])
        self.assertEqual(0, metrics["clamav_canary_detected"])
        self.assertNotIn("clamav_signatures_version", metrics)


if __name__ == "__main__":
    unittest.main()
