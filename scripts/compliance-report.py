#!/usr/bin/env python3
"""Compliance report and evidence pack for an installed Swirl Cloud platform.

Evaluates the control catalog (k8s/files/compliance-controls.yaml) against live
Prometheus data and prints each control's status: PASS (evidence present, nothing
failing), FAIL, NO EVIDENCE, or MANUAL (proven by documents an operator supplies).
Controls map to NIS2, ISO/IEC 27001:2022 Annex A, DORA, GDPR and CIS requirements.

--evidence-pack writes a private archive with the report (JSON and Markdown), the
catalog, every query and its raw result, recent status history, the platform
verification report and Trivy compliance summaries (plus the access review with
--access-review), and a SHA256SUMS manifest.
--sign-key signs the manifest with an SSH key (ssh-keygen -Y sign); an auditor
checks an archive with --verify ARCHIVE [--allowed-signers FILE --signer ID].
Everything is read-only; Kubernetes Secrets are never read.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "k8s/files/compliance-controls.yaml"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "swirl-cloud"
PROXY = "/api/v1/namespaces/infra/services/prometheus:9090/proxy"
SIGNATURE_NAMESPACE = "swirl-cloud-evidence"
PASS, FAIL, NO_EVIDENCE, MANUAL = "PASS", "FAIL", "NO EVIDENCE", "MANUAL"
STATUS_VALUES = {0: FAIL, 1: NO_EVIDENCE, 2: PASS}


class Prometheus:
    def __init__(self, url=None):
        self.url = url.rstrip("/") if url else None

    def get(self, path, params):
        query = urlencode(params, quote_via=quote)
        if self.url:
            with urlopen(f"{self.url}{path}?{query}", timeout=30) as response:
                body = response.read()
        else:
            result = subprocess.run(["kubectl", "--request-timeout=30s", "get", "--raw", f"{PROXY}{path}?{query}"],
                                    capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            if result.returncode:
                raise RuntimeError(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "kubectl failed")
            body = result.stdout
        response = json.loads(body)
        if response.get("status") != "success":
            raise RuntimeError(response.get("error", "Prometheus query failed"))
        return response["data"]

    def query(self, expr):
        return self.get("/api/v1/query", {"query": expr})

    def query_range(self, expr, hours, step="15m"):
        end = time.time()
        return self.get("/api/v1/query_range", {"query": expr, "start": f"{end - hours * 3600:.0f}", "end": f"{end:.0f}",
                                                "step": step})


def series_text(series):
    labels = {k: v for k, v in series["metric"].items()
              if k not in ("__name__", "instance", "job", "pod", "container", "alertstate", "severity", "description", "status")}
    shown = ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{shown or 'value'} ({series['value'][1]})"


def evaluate(catalog, prometheus):
    """Return one result per control, with the raw query responses for the evidence pack."""
    results = []
    for control in catalog["controls"]:
        checks = []
        for check in control.get("checks", []):
            entry = {"id": check["id"], "description": check["description"],
                     "present_query": check["present"], "failing_query": check["failing"]}
            try:
                present, failing = prometheus.query(check["present"]), prometheus.query(check["failing"])
                # Keep every failing series but only a sample of the presence proof.
                entry.update(present={"series": len(present["result"]), "sample": present["result"][:5]}, failing=failing,
                             status=FAIL if failing["result"] else PASS if present["result"] else NO_EVIDENCE,
                             details=[series_text(s) for s in failing["result"]][:10])
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
                entry.update(status=NO_EVIDENCE, details=[f"query failed: {error}"])
            checks.append(entry)
        order = [FAIL, NO_EVIDENCE, PASS]
        status = min((c["status"] for c in checks), key=order.index) if checks else MANUAL
        results.append({"id": control["id"], "title": control["title"], "objective": control["objective"],
                        "frameworks": control.get("frameworks", {}), "status": status, "checks": checks,
                        "manual": control.get("manual", [])})
    return results


def framework_summary(catalog, results, selected=None):
    summary = {}
    for key, name in catalog["frameworks"].items():
        if selected and key != selected:
            continue
        mapped = [r for r in results if key in r["frameworks"]]
        automated = [r for r in mapped if r["status"] != MANUAL]
        summary[key] = {"name": name, "controls": len(mapped), "automated": len(automated),
                        "passing": sum(r["status"] == PASS for r in automated),
                        "failing": sum(r["status"] == FAIL for r in automated),
                        "no_evidence": sum(r["status"] == NO_EVIDENCE for r in automated),
                        "manual": len(mapped) - len(automated)}
    return summary


def counts(results):
    return {status: sum(r["status"] == status for r in results) for status in (PASS, FAIL, NO_EVIDENCE, MANUAL)}


def render_text(results, summary, generated):
    lines = [f"Swirl Cloud compliance report - {generated:%Y-%m-%d %H:%M} UTC", "",
             "Automated controls passing by framework:"]
    for item in summary.values():
        lines.append(f"  {item['name']}: {item['passing']}/{item['automated']} passing"
                     f" ({item['failing']} failing, {item['no_evidence']} without evidence, {item['manual']} manual)")
    lines.append("")
    for result in results:
        lines.append(f"[{result['status']}] {result['id']}: {result['title']}")
        for check in result["checks"]:
            if check["status"] != PASS:
                lines.append(f"       {check['id']} {check['status'].lower()}: {check['description']}")
                lines += [f"         {detail}" for detail in check["details"]]
        lines += [f"       manual evidence: {item}" for item in result["manual"]]
    total = counts(results)
    lines += ["", f"Summary: {total[PASS]} passing, {total[FAIL]} failing, {total[NO_EVIDENCE]} without evidence, "
                  f"{total[MANUAL]} manual-only controls"]
    return "\n".join(lines)


def natural_key(text):
    """Order A.5.9 before A.5.15 and 21(2)(b) before 23."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", str(text))]


def render_markdown(results, summary, generated, platform):
    lines = ["# Compliance evidence report", "",
             f"Generated {generated:%Y-%m-%d %H:%M:%S} UTC from the live platform"
             f" (revision `{platform.get('revision', 'unknown')}`).", "",
             "Status: **PASS** evidence present and nothing failing; **FAIL** a check is failing;"
             " **NO EVIDENCE** the signal is missing; **MANUAL** proven by documents listed below.", "",
             "## Frameworks", "", "| Framework | Automated passing | Failing | No evidence | Manual |", "|---|---|---|---|---|"]
    for item in summary.values():
        lines.append(f"| {item['name']} | {item['passing']}/{item['automated']} | {item['failing']} | "
                     f"{item['no_evidence']} | {item['manual']} |")
    for key, item in summary.items():
        lines += ["", f"### {item['name']}", "", "| Requirement | Control | Status |", "|---|---|---|"]
        rows = sorted(((requirement, r) for r in results for requirement in r["frameworks"].get(key, [])),
                      key=lambda row: (natural_key(row[0]), row[1]["id"]))
        lines += [f"| {requirement} | {r['title']} (`{r['id']}`) | {r['status']} |" for requirement, r in rows]
    lines += ["", "## Controls"]
    for result in results:
        lines += ["", f"### {result['title']} (`{result['id']}`): {result['status']}", "", result["objective"], ""]
        for check in result["checks"]:
            lines.append(f"- **{check['status']}** `{check['id']}`: {check['description']}")
            lines += [f"  - {detail}" for detail in check["details"]]
        if result["manual"]:
            lines.append("- Manual evidence to attach:")
            lines += [f"  - [ ] {item}" for item in result["manual"]]
    return "\n".join(lines) + "\n"


def command_output(*command, timeout=300):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=ROOT)
        return result.stdout if result.returncode in (0, 1) else result.stdout + result.stderr
    except (OSError, subprocess.SubprocessError) as error:
        return str(error)


def platform_context():
    revision = command_output("git", "-C", str(ROOT), "describe", "--always", "--dirty").strip()
    nodes = command_output("kubectl", "--request-timeout=20s", "get", "nodes", "-o",
                           "jsonpath={range .items[*]}{.metadata.name} {.status.nodeInfo.kubeletVersion} "
                           "{.status.nodeInfo.osImage}{'\\n'}{end}")
    return {"revision": revision, "nodes": [line for line in nodes.splitlines() if line.strip()]}


def trivy_compliance():
    raw = command_output("kubectl", "--request-timeout=30s", "get", "clustercompliancereports.aquasecurity.github.io", "-o", "json")
    try:
        reports = json.loads(raw)["items"]
    except (ValueError, KeyError):
        return {"error": raw.strip()[:500]}
    summary = []
    for report in reports:
        status = report.get("status", {})
        failed = [f"{c.get('id')} {c.get('name')}" for c in status.get("detailReport", {}).get("results", [])
                  if any(check.get("success") is False for check in c.get("checks", []))]
        summary.append({"name": report["metadata"]["name"], "title": report.get("spec", {}).get("compliance", {}).get("title"),
                        "summary": status.get("summary"), "updated": status.get("updateTimestamp"), "failing_checks": failed})
    return summary


def write_pack(output_dir, catalog_text, results, summary, generated, platform, history, sign_key, access_review=False):
    stamp = f"{generated:%Y%m%dT%H%M%SZ}"
    name = f"swirl-cloud-evidence-{stamp}"
    files = {
        "report.json": json.dumps({"generated_at": generated.isoformat(), "platform": platform, "frameworks": summary,
                                   "controls": [{k: v for k, v in r.items()} for r in results]}, indent=2),
        "report.md": render_markdown(results, summary, generated, platform),
        "controls.yaml": catalog_text,
        "history.json": json.dumps(history, indent=2),
        "context/verification.json": command_output(sys.executable, str(ROOT / "scripts/verify-platform.py"), "--json",
                                                    "--skip-endpoints"),
        "context/trivy-compliance.json": json.dumps(trivy_compliance(), indent=2),
    }
    if access_review:
        files["context/access-review.json"] = command_output(sys.executable, str(ROOT / "scripts/access-review.py"),
                                                             "--format", "json", timeout=1200)
    with tempfile.TemporaryDirectory(prefix="swirl-cloud-evidence.") as directory:
        base = Path(directory) / name
        for path, text in files.items():
            target = base / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        manifest = "".join(f"{hashlib.sha256((base / p).read_bytes()).hexdigest()}  {p}\n" for p in sorted(files))
        (base / "SHA256SUMS").write_text(manifest)
        if sign_key:
            subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(sign_key), "-n", SIGNATURE_NAMESPACE,
                            str(base / "SHA256SUMS")], check=True, stdin=subprocess.DEVNULL)
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive_path = output_dir / f"{name}.tar.gz"
        descriptor = os.open(archive_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle, tarfile.open(fileobj=handle, mode="w:gz") as archive:
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    info = archive.gettarinfo(str(path), f"{name}/{path.relative_to(base)}")
                    info.mode, info.uid, info.gid, info.uname, info.gname = 0o600, 0, 0, "", ""
                    info.mtime = int(generated.timestamp())
                    with path.open("rb") as content:
                        archive.addfile(info, content)
    return archive_path, hashlib.sha256(manifest.encode()).hexdigest()


def verify_pack(archive_path, allowed_signers, signer):
    with tempfile.TemporaryDirectory(prefix="swirl-cloud-evidence-verify.") as directory, \
            tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(directory, filter="data")
        roots = [p for p in Path(directory).iterdir() if p.is_dir()]
        if len(roots) != 1:
            print("FAIL: the archive must contain exactly one evidence directory")
            return 1
        base = roots[0]
        listed = {}
        for line in (base / "SHA256SUMS").read_text().splitlines():
            digest, _, path = line.partition("  ")
            listed[path] = digest
        present = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()} - {"SHA256SUMS", "SHA256SUMS.sig"}
        problems = [f"not in manifest: {p}" for p in sorted(present - set(listed))]
        problems += [f"missing: {p}" for p in sorted(set(listed) - present)]
        problems += [f"altered: {p}" for p in sorted(set(listed) & present)
                     if hashlib.sha256((base / p).read_bytes()).hexdigest() != listed[p]]
        if problems:
            print("FAIL: " + "; ".join(problems))
            return 1
        print(f"PASS: {len(listed)} files match SHA256SUMS ({base.name})")
        signature = base / "SHA256SUMS.sig"
        if not signature.exists():
            print("UNSIGNED: no SHA256SUMS.sig in the archive")
            return 1 if allowed_signers else 0
        if not allowed_signers:
            print("SIGNED: pass --allowed-signers FILE --signer ID to verify the signature")
            return 0
        with (base / "SHA256SUMS").open("rb") as manifest:
            result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", signer,
                                     "-n", SIGNATURE_NAMESPACE, "-s", str(signature)], stdin=manifest,
                                    capture_output=True, text=True)
        print(("PASS: " if result.returncode == 0 else "FAIL: ") + (result.stdout + result.stderr).strip())
        return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("--framework", help="limit the summary to one framework key (nis2, iso27001, dora, gdpr, cis)")
    parser.add_argument("--evidence-pack", action="store_true", help="also write a private evidence archive")
    parser.add_argument("--output-dir", type=Path, default=STATE / "compliance")
    parser.add_argument("--sign-key", type=Path, help="SSH private key that signs the evidence manifest")
    parser.add_argument("--history-hours", type=int, default=24, help="status history included in the pack")
    parser.add_argument("--access-review", action="store_true",
                        help="include the access review (scripts/access-review.py, about two minutes) in the pack")
    parser.add_argument("--prometheus-url", help="query Prometheus directly instead of through the Kubernetes API")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a control fails or lacks evidence")
    parser.add_argument("--verify", type=Path, metavar="ARCHIVE", help="check an evidence archive and its signature")
    parser.add_argument("--allowed-signers", type=Path, help="ssh-keygen allowed signers file for --verify")
    parser.add_argument("--signer", default="evidence", help="signer identity in the allowed signers file")
    args = parser.parse_args()
    if args.verify:
        return verify_pack(args.verify, args.allowed_signers, args.signer)
    catalog_text = CATALOG.read_text()
    catalog = yaml.safe_load(catalog_text)
    if args.framework and args.framework not in catalog["frameworks"]:
        parser.error(f"unknown framework {args.framework}; choose from {', '.join(catalog['frameworks'])}")
    if args.sign_key and not shutil.which("ssh-keygen"):
        parser.error("--sign-key needs ssh-keygen (OpenSSH)")
    prometheus = Prometheus(args.prometheus_url)
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    results = evaluate(catalog, prometheus)
    if args.framework:
        results = [r for r in results if args.framework in r["frameworks"]]
    summary = framework_summary(catalog, results, args.framework)
    platform = platform_context()
    if args.format == "json":
        print(json.dumps({"generated_at": generated.isoformat(), "platform": platform, "frameworks": summary,
                          "controls": [{k: v for k, v in r.items() if k != "checks"} |
                                       {"checks": [{k: v for k, v in c.items() if k not in ("present", "failing")}
                                                   for c in r["checks"]]} for r in results]}, indent=2))
    elif args.format == "markdown":
        print(render_markdown(results, summary, generated, platform), end="")
    else:
        print(render_text(results, summary, generated))
    if args.evidence_pack:
        try:
            history = prometheus.query_range("swirl_cloud_compliance_control_status", args.history_hours)
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            history = {"error": str(error)}
        path, digest = write_pack(args.output_dir, catalog_text, results, summary, generated, platform, history,
                                  args.sign_key, args.access_review)
        print(f"\nEvidence pack: {path}\nManifest SHA-256: {digest}"
              f"{' (signed)' if args.sign_key else ''}. Verify with: {Path(sys.argv[0]).name} --verify {path}",
              file=sys.stderr)
    total = counts(results)
    return 1 if args.strict and (total[FAIL] or total[NO_EVIDENCE]) else 0


if __name__ == "__main__":
    sys.exit(main())
