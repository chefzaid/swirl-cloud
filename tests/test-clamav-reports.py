#!/usr/bin/env python3
"""Guards for ClamAV's scan reports: clamd logs every result, the logging agent
marks clamd's result lines (and drops the pod's own test scans) into the
malware-scans-* indices, whose mapping holds every field it sets; the logging
agent may write them, Grafana and the security detection job may read them, and
the dashboard asks only for mapped fields and for metrics the exporter serves."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
T = "Tue Nov 17 01:24:19 2026 -> "
CASES = [
    ("malware", T + "instream(10.42.0.93@38070): Win.Trojan.Agent-1 FOUND",
     {"result": "infected", "signature": "Win.Trojan.Agent-1", "category": "malware", "client_address": "10.42.0.93"}),
    ("clean", T + "instream(10.42.0.93@38071): OK", {"result": "clean", "client_address": "10.42.0.93"}),
    ("policy", T + "instream(10.42.0.5@1): Heuristics.Encrypted.Zip FOUND",
     {"result": "infected", "signature": "Heuristics.Encrypted.Zip", "category": "heuristic"}),
    ("unwanted", T + "instream(10.42.0.5@2): PUA.Pdf.Trojan.EmbeddedJavaScript-1 FOUND", {"category": "pua"}),
    ("error", T + "instream(10.42.0.9@5): Can't allocate memory ERROR", {"result": "error", "detail": "Can't allocate memory ERROR"}),
]


def documents(path):
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


def lua_table(value):
    if isinstance(value, dict):
        return "{" + ", ".join(f"[{json.dumps(key)}] = {lua_table(item)}" for key, item in value.items()) + "}"
    return json.dumps(value)


def config_map(path, key):
    return next(d for d in documents(path) if d["kind"] == "ConfigMap" and key in d.get("data", {}))["data"]


class ClamAVReports(unittest.TestCase):
    def test_clamd_logs_every_result(self):
        settings = (ROOT / "k8s/files/clamav/clamd.conf").read_text()
        self.assertRegex(settings, r"(?m)^LogClean yes$")
        self.assertRegex(settings, r"(?m)^Foreground yes$")
        self.assertNotRegex(settings, r"(?m)^LogFile ")

    def test_results_have_their_own_indices(self):
        agent = config_map("k8s/platform/logging-agent.yaml", "fluent-bit.conf")
        conf = agent["fluent-bit.conf"]
        self.assertIn("Rule                      $log_source ^malware-scan$ malware.clamav false", conf)
        output = conf.split("Alias                     malware_scans")[1].split("[OUTPUT]")[0]
        self.assertIn("Match                     malware.*", output)
        self.assertIn("Logstash_Prefix           malware-scans", output)
        template = json.loads(agent["malware-scans-template.json"])
        self.assertEqual(["malware-scans-*"], template["index_patterns"])
        mapped = set(template["mappings"]["properties"]["clamav"]["properties"])
        lua = agent["enrich-kubernetes.lua"]
        self.assertEqual(set(re.findall(r'scan\["(\w+)"\]', lua)) | {"client_address"}, mapped)
        init = next(d for d in documents("k8s/platform/logging-agent.yaml") if d["kind"] == "DaemonSet")
        script = init["spec"]["template"]["spec"]["initContainers"][0]["command"][-1]
        self.assertIn("@/config/malware-scans-template.json", script)

    def test_roles_and_datasource(self):
        elk = (ROOT / "k8s/platform/elk.yaml").read_text()
        for role in ("logs_writer", "logs_reader", "security_detection"):
            body = re.search(r"--data-binary '(\{[^']*\})' \\\s*\"\$\{endpoint\}/_security/role/" + role + '"', elk)
            self.assertIsNotNone(body, role)
            names = [name for index in json.loads(body[1])["indices"] for name in index["names"]]
            self.assertIn("malware-scans-*", names, role)
        sources = yaml.safe_load(config_map("k8s/platform/monitoring.yaml", "datasources.yaml")["datasources.yaml"])
        source = next(s for s in sources["datasources"] if s.get("uid") == "malware-scans")
        self.assertEqual("malware-scans-*", source["database"])
        self.assertIn("Malware scans", [d["name"] for d in sources["deleteDatasources"]])

    def test_dashboard_asks_for_what_exists(self):
        dashboard = json.loads((ROOT / "k8s/files/dashboards/clamav-malware-scanning.json").read_text())
        template = json.loads(config_map("k8s/platform/logging-agent.yaml", "malware-scans-template.json")["malware-scans-template.json"])
        fields = {f"clamav.{name}" for name in template["mappings"]["properties"]["clamav"]["properties"]}
        fields |= {"@timestamp", "kubernetes.pod_name"}
        exporter = (ROOT / "k8s/files/clamav/exporter.mjs").read_text()
        served = set(re.findall(r'metric\("(clamav_\w+)"', exporter))
        for panel in dashboard["panels"]:
            for target in panel["targets"]:
                if target["datasource"]["uid"] == "malware-scans":
                    used = {a["field"] for a in target.get("bucketAggs", []) if a.get("field")}
                    used |= set(re.findall(r"([\w.]+):", target["query"]))
                    columns = next((t["options"].get("includeByName", {}) for t in panel.get("transformations", [])), {})
                    self.assertLessEqual(used | set(columns), fields, panel["title"])
                else:
                    for metric in re.findall(r"\b(clamav_\w+)", target["expr"]):
                        self.assertIn(metric, served, panel["title"])
        self.assertEqual("clamav-malware-scanning", dashboard["uid"])
        homepage = (ROOT / "k8s/platform/homepage.yaml").read_text()
        self.assertIn("/d/clamav-malware-scanning", homepage)

    @unittest.skipUnless(shutil.which("luajit") or shutil.which("lua"), "no Lua interpreter")
    def test_filter_marks_only_clamd_results(self):
        lua = config_map("k8s/platform/logging-agent.yaml", "enrich-kubernetes.lua")["enrich-kubernetes.lua"]
        records = [(name, line, "infra", "clamd") for name, line, _ in CASES]
        records += [("test scan", T + "instream(127.0.0.1@41000): Eicar-Signature FOUND", "infra", "clamd"),
                    ("other line", T + "SelfCheck: Database status OK.", "infra", "clamd"),
                    ("other namespace", CASES[0][1], "apps", "clamd"),
                    ("other container", CASES[0][1], "infra", "freshclam")]
        calls = []
        for name, line, namespace, container in records:
            record = lua_table({"log": line, "kubernetes": {"namespace_name": namespace, "container_name": container,
                                                            "labels": {"app": "clamav"}}})
            calls.append(textwrap.dedent(f"""\
                do
                  local code, _, out = enrich_kubernetes("kube.x", 0, {record})
                  local scan = out["clamav"] or {{}}
                  local fields = {{}}
                  for _, key in ipairs({{"result", "signature", "category", "client_address", "detail"}}) do
                    fields[#fields + 1] = key .. "=" .. tostring(scan[key])
                  end
                  print({json.dumps(name)} .. "|" .. code .. "|" .. tostring(out["log_source"]) .. "|" .. table.concat(fields, ";"))
                end"""))
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "run.lua"
            script.write_text(lua + "\n" + "\n".join(calls) + "\n")
            output = subprocess.run([shutil.which("luajit") or shutil.which("lua"), str(script)],
                                    check=True, capture_output=True, text=True).stdout
        results = {}
        for line in output.splitlines():
            name, code, source, fields = line.split("|", 3)
            results[name] = (code, source, dict(item.split("=", 1) for item in fields.split(";")))
        for name, _, expected in CASES:
            code, source, fields = results[name]
            self.assertEqual(("2", "malware-scan"), (code, source), name)
            for key, value in expected.items():
                self.assertEqual(value, fields[key], (name, key))
        self.assertEqual("-1", results["test scan"][0])
        for name in ("other line", "other namespace", "other container"):
            self.assertEqual("nil", results[name][1], name)


if __name__ == "__main__":
    unittest.main()
