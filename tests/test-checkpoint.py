#!/usr/bin/env python3
"""Guards for checkpoints, run with a fake kubectl and a fake Kubernetes API:
create snapshots every bound Longhorn claim (and only those), records the
deployed revision before any snapshot, and names snapshots within 63
characters; the expiry job deletes expired checkpoint objects only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("checkpoint", ROOT / "scripts/checkpoint.py")
checkpoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checkpoint)

CLAIMS = [
    {"metadata": {"namespace": "infra", "name": "postgres-v18-pvc"}, "spec": {"storageClassName": "longhorn"},
     "status": {"phase": "Bound", "capacity": {"storage": "2Gi"}}},
    {"metadata": {"namespace": "apps-indezy-prod", "name": "data"}, "spec": {"storageClassName": "longhorn-encrypted"},
     "status": {"phase": "Bound", "capacity": {"storage": "5Gi"}}},
    {"metadata": {"namespace": "infra", "name": "scratch"}, "spec": {"storageClassName": "local-path"}, "status": {"phase": "Bound"}},
    {"metadata": {"namespace": "infra", "name": "pending"}, "spec": {"storageClassName": "longhorn"}, "status": {"phase": "Pending"}},
]
FAKE_KUBECTL = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, os, sys
    args = [a for a in sys.argv[1:] if not a.startswith("--request-timeout")]
    log = os.environ["KUBECTL_LOG"]
    def say(value):
        print(json.dumps(value))
    if args[:2] == ["apply", "-f"]:
        with open(log, "a") as out:
            out.write(json.dumps({"apply": json.loads(sys.stdin.read())}) + "\\n")
    elif args[:3] == ["get", "volumesnapshotclass", "longhorn-snapshot"]:
        say({})
    elif args[:3] == ["get", "pvc", "--all-namespaces"]:
        say({"items": json.loads(os.environ["CLAIMS"])})
    elif "application" in args:
        say({"spec": {"source": {"targetRevision": "main", "repoURL": "https://gitlab.example/p.git"}},
             "status": {"sync": {"revision": "0123456789abcdef"}}})
    elif "volumesnapshot" in args and "get" in args:
        say({"status": {"readyToUse": True}})
    else:
        sys.exit("unexpected kubectl " + " ".join(args))
    """)
MOCK_API = textwrap.dedent("""\
    import { writeFileSync } from "node:fs";
    const deleted = [];
    const item = (kind, namespace, name, expires) => ({ kind, metadata: { namespace, name,
      labels: { "swirl-cloud.io/checkpoint": "cp-1" }, annotations: expires ? { "swirl-cloud.io/checkpoint-expires-at": expires } : {} } });
    globalThis.fetch = async (url, options = {}) => {
      const path = new URL(url).pathname;
      if ((options.method || "GET") === "DELETE") { deleted.push(path); return new Response(null, { status: 200 }); }
      if (path.endsWith("/volumesnapshots")) return Response.json({ items: [
        item("VolumeSnapshot", "infra", "old", "2020-01-01T00:00:00+00:00"),
        item("VolumeSnapshot", "apps", "new", "2999-01-01T00:00:00+00:00"),
        item("VolumeSnapshot", "apps", "unmarked", "")] });
      if (path.endsWith("/configmaps")) return Response.json({ items: [item("ConfigMap", "infra", "checkpoint-cp-1", "2020-01-01T00:00:00+00:00")] });
      return new Response("", { status: 404 });
    };
    process.on("exit", () => writeFileSync(process.env.RESULT, JSON.stringify(deleted)));
    """)


class Checkpoint(unittest.TestCase):
    def test_snapshot_names_fit_a_dns_label(self):
        self.assertEqual(checkpoint.snapshot_name("cp-20261115-101500", "data"), "cp-20261115-101500-data")
        long = checkpoint.snapshot_name("cp-20261115-101500", "x" * 80)
        self.assertLessEqual(len(long), 63)
        self.assertNotEqual(long, checkpoint.snapshot_name("cp-20261115-101500", "x" * 79))

    def test_create_snapshots_bound_longhorn_claims_after_recording(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            fake = directory / "kubectl"
            fake.write_text(FAKE_KUBECTL)
            fake.chmod(0o755)
            log = directory / "log"
            env = dict(os.environ, PATH=f"{directory}:/usr/bin:/bin", KUBECTL_LOG=str(log), CLAIMS=json.dumps(CLAIMS))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/checkpoint.py"), "create", "--label", "INC-1 upgrade", "--hours", "24"],
                                    env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            applied = [json.loads(line)["apply"] for line in log.read_text().splitlines()]
            self.assertEqual(applied[0]["kind"], "ConfigMap")
            data = json.loads(applied[0]["data"]["checkpoint.json"])
            self.assertEqual(data["argocd"]["revision"], "0123456789abcdef")
            self.assertEqual(data["label"], "INC-1 upgrade")
            snapshots = applied[1:]
            self.assertEqual({(s["metadata"]["namespace"], s["spec"]["source"]["persistentVolumeClaimName"]) for s in snapshots},
                             {("infra", "postgres-v18-pvc"), ("apps-indezy-prod", "data")})
            self.assertTrue(all(s["spec"]["volumeSnapshotClassName"] == "longhorn-snapshot" for s in snapshots))
            self.assertEqual({s["metadata"]["annotations"][checkpoint.EXPIRES] for s in snapshots}, {data["expires"]})
            self.assertIn("is ready", result.stdout)
            limited = subprocess.run([sys.executable, str(ROOT / "scripts/checkpoint.py"), "create", "--hours", "400"],
                                     env=env, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(limited.returncode, 0)

    @unittest.skipUnless(shutil.which("node"), "node is required")
    def test_expiry_deletes_only_expired_checkpoint_objects(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / "mock.mjs").write_text(MOCK_API)
            (directory / "token").write_text("token\n")
            env = dict(os.environ, TOKEN_FILE=str(directory / "token"), KUBERNETES_API="https://api.test", RESULT=str(directory / "result"))
            subprocess.run(["node", "--import", str(directory / "mock.mjs"), str(ROOT / "k8s/files/checkpoint-expiry.mjs")],
                           env=env, check=True, capture_output=True, text=True, timeout=60)
            self.assertEqual(sorted(json.loads((directory / "result").read_text())),
                             ["/api/v1/namespaces/infra/configmaps/checkpoint-cp-1",
                              "/apis/snapshot.storage.k8s.io/v1/namespaces/infra/volumesnapshots/old"])


if __name__ == "__main__":
    unittest.main()
