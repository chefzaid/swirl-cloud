#!/usr/bin/env python3
"""Platform releases: component inventory, SBOM, changelog and signed bundles.

  release.py inventory                      pinned images, charts and tools
  release.py sbom [--output FILE]           CycloneDX 1.5 JSON software bill of materials
  release.py changelog [--since REF]        commit subjects since a tag (default: the latest v* tag)
  release.py prepare VERSION                set the chart version and prepend CHANGELOG.md
  release.py bundle [--ref REF] [--output-dir DIR] [--sign-key KEY]
  release.py verify BUNDLE [--allowed-signers FILE --signer ID]

The platform version is the chart version in k8s/Chart.yaml; a release is the
annotated tag v<version> on that commit. A bundle holds the source archive of
the release, the SBOM, the image list, release notes and a SHA256SUMS manifest,
optionally signed with an SSH key (ssh-keygen -Y sign), like evidence packs.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
SIGNATURE_NAMESPACE = "swirl-cloud-release"
IMAGE = re.compile(r"""image:\s*["']?([a-z0-9.-]+(?::[0-9]+)?/[^\s"'@]+@sha256:[a-f0-9]{64})""")
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")


def git(*args, **kwargs):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True, **kwargs).stdout


def files_at(ref):
    """Text of tracked files at a ref, or of the working tree when ref is None."""
    paths = [p for p in git("ls-tree", "-r", "--name-only", ref or "HEAD").splitlines()
             if p.endswith((".yaml", ".yml", ".env")) and not p.startswith("k8s/charts/")]
    for path in paths:
        yield path, (git("show", f"{ref}:{path}") if ref else (ROOT / path).read_text())


def split_image(reference):
    name, digest = reference.split("@", 1)
    tag = ""
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
    return name, tag, digest


def values_images(data, path, source, found):
    """Images in Helm values written as registry/repository/tag/digest fields."""
    if isinstance(data, dict):
        tag = str(data.get("tag", ""))
        digest = str(data.get("digest", ""))
        if "@sha256:" in tag:
            tag, digest = tag.split("@", 1)
        if digest.startswith("sha256:") and ("repository" in data or tag):
            repository = str(data.get("repository", ""))
            registry = str(data.get("registry", ""))
            chart = Path(source).stem.removesuffix("-values")
            name = "/".join(part for part in (registry, repository) if part) or f"{chart} chart {'.'.join(path) or 'image'} (chart default image)"
            found[(name, digest)] = {"name": name, "version": tag, "digest": digest, "source": source}
        for key, value in data.items():
            values_images(value, path + [str(key)], source, found)
    elif isinstance(data, list):
        for item in data:
            values_images(item, path, source, found)


def inventory(ref=None):
    images = {}
    platform = {}
    for path, text in files_at(ref):
        for reference in IMAGE.findall(text):
            name, tag, digest = split_image(reference)
            images.setdefault((name, digest), {"name": name, "version": tag, "digest": digest, "source": path})
        if path.endswith((".yaml", ".yml")) and (path.startswith("config/") or path == "k8s/values.yaml"):
            try:
                for document in yaml.safe_load_all(text):
                    values_images(document, [], path, images)
            except yaml.YAMLError:
                pass
        if path == "config/platform.env":
            platform = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#"))
    chart = yaml.safe_load(git("show", f"{ref}:k8s/Chart.yaml") if ref else (ROOT / "k8s/Chart.yaml").read_text())
    charts = [{"name": d["name"], "version": d["version"], "source": d.get("repository", "")} for d in chart.get("dependencies", [])]
    charts += [{"name": key.removeprefix("DEFAULT_").removesuffix("_CHART_VERSION").lower().replace("_", "-"),
                "version": value, "source": "config/platform.env"}
               for key, value in platform.items() if key.endswith("_CHART_VERSION")]
    tools = [{"name": "k3s", "version": platform.get("DEFAULT_K3S_INSTALL_VERSION", "")},
             {"name": "helm", "version": platform.get("DEFAULT_HELM_VERSION", ""),
              "hashes": {"linux-amd64": platform.get("DEFAULT_HELM_LINUX_AMD64_SHA256", ""),
                         "linux-arm64": platform.get("DEFAULT_HELM_LINUX_ARM64_SHA256", "")}}]
    return {"version": chart["version"], "images": sorted(images.values(), key=lambda i: (i["name"], i["version"])),
            "charts": sorted(charts, key=lambda c: c["name"]), "tools": tools}


def purl(image):
    name = image["name"]
    registry, _, repository = name.partition("/") if "." in name.split("/")[0] else ("docker.io", "", name)
    return f"pkg:oci/{repository.rsplit('/', 1)[-1]}@{image['digest'].replace(':', '%3A')}?repository_url={registry}/{repository}" + (
        f"&tag={image['version']}" if image["version"] else "")


def sbom(items, timestamp):
    components = [{"type": "container", "name": i["name"], "version": i["version"] or i["digest"],
                   "hashes": [{"alg": "SHA-256", "content": i["digest"].split(":", 1)[1]}], "purl": purl(i),
                   "properties": [{"name": "swirl-cloud:source", "value": i["source"]}]} for i in items["images"]
                  if not i["name"].endswith("(chart default image)")]
    components += [{"type": "application", "name": i["name"], "version": i["version"] or i["digest"],
                    "hashes": [{"alg": "SHA-256", "content": i["digest"].split(":", 1)[1]}],
                    "properties": [{"name": "swirl-cloud:source", "value": i["source"]}]} for i in items["images"]
                   if i["name"].endswith("(chart default image)")]
    components += [{"type": "application", "name": f"helm-chart:{c['name']}", "version": c["version"],
                    "properties": [{"name": "swirl-cloud:source", "value": c["source"]}]} for c in items["charts"]]
    components += [{"type": "application", "name": t["name"], "version": t["version"],
                    **({"hashes": [{"alg": "SHA-256", "content": h} for h in t["hashes"].values() if h]} if t.get("hashes") else {})}
                   for t in items["tools"]]
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
            "metadata": {"timestamp": timestamp, "component": {"type": "application", "name": "swirl-cloud",
                                                               "version": items["version"]}},
            "components": components}


def latest_tag(before=None):
    tags = git("tag", "--list", "v*", "--sort=-v:refname", *(["--merged", before] if before else [])).split()
    return tags[0] if tags else None


def changelog(since, until="HEAD"):
    revisions = f"{since}..{until}" if since else until
    subjects = git("log", "--no-merges", "--format=%s", revisions).splitlines()
    return [s for s in subjects if not s.startswith(("Record ", "Merge "))] or ["No changes."]


def write_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(descriptor, "wb" if isinstance(content, bytes) else "w") as handle:
        handle.write(content)


def bundle(ref, output_dir, sign_key):
    commit = git("rev-parse", ref).strip()
    items = inventory(ref)
    version = items["version"]
    timestamp = datetime.fromtimestamp(int(git("show", "-s", "--format=%ct", commit)), timezone.utc).isoformat()
    previous = latest_tag(f"{commit}^") if git("rev-list", "--count", commit).strip() != "1" else None
    name = f"swirl-cloud-{version}"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / name
    if target.exists():
        raise SystemExit(f"{target} already exists")
    target.mkdir()
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar.gz", f"--prefix={name}/", commit],
                             capture_output=True, check=True).stdout
    write_file(target / f"{name}-source.tar.gz", archive)
    write_file(target / "sbom.cdx.json", json.dumps(sbom(items, timestamp), indent=2))
    write_file(target / "images.txt", "".join(f"{i['name']}{':' + i['version'] if i['version'] else ''}@{i['digest']}\n"
                                                 for i in items["images"]))
    notes = [f"# Swirl Cloud {version}", "", f"Commit `{commit}`, {timestamp}.", "",
             f"## Changes since {previous or 'the first commit'}", ""] + [f"- {s}" for s in changelog(previous, commit)]
    notes += ["", "## Components", "", f"{len(items['images'])} pinned images, {len(items['charts'])} Helm charts, "
              + ", ".join(f"{t['name']} {t['version']}" for t in items["tools"]) + "; see `sbom.cdx.json` and `images.txt`."]
    write_file(target / "RELEASE-NOTES.md", "\n".join(notes) + "\n")
    files = sorted(p.name for p in target.iterdir())
    manifest = "".join(f"{hashlib.sha256((target / f).read_bytes()).hexdigest()}  {f}\n" for f in files)
    write_file(target / "SHA256SUMS", manifest)
    if sign_key:
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(sign_key), "-n", SIGNATURE_NAMESPACE,
                        str(target / "SHA256SUMS")], check=True)
    return target, hashlib.sha256(manifest.encode()).hexdigest()


def verify(path, allowed_signers, signer):
    with tempfile.TemporaryDirectory() as directory:
        if path.is_file():
            with tarfile.open(path) as archive:
                archive.extractall(directory, filter="data")
            path = next(p for p in Path(directory).iterdir() if p.is_dir())
        listed = dict(reversed(line.split("  ", 1)) for line in (path / "SHA256SUMS").read_text().splitlines())
        present = {p.name for p in path.iterdir() if p.is_file()} - {"SHA256SUMS", "SHA256SUMS.sig"}
        problems = [f"unlisted: {p}" for p in sorted(present - set(listed))] + [f"missing: {p}" for p in sorted(set(listed) - present)]
        problems += [f"altered: {p}" for p in sorted(present & set(listed))
                     if hashlib.sha256((path / p).read_bytes()).hexdigest() != listed[p]]
        if problems:
            print("FAIL: " + "; ".join(problems))
            return 1
        print(f"PASS: {len(listed)} files match SHA256SUMS ({path.name})")
        signature = path / "SHA256SUMS.sig"
        if not signature.exists():
            print("UNSIGNED: no SHA256SUMS.sig")
            return 1 if allowed_signers else 0
        if not allowed_signers:
            print("SIGNED: pass --allowed-signers FILE --signer ID to verify the signature")
            return 0
        with (path / "SHA256SUMS").open("rb") as manifest:
            result = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers), "-I", signer, "-n",
                                     SIGNATURE_NAMESPACE, "-s", str(signature)], stdin=manifest, capture_output=True, text=True)
        print(("PASS: " if result.returncode == 0 else "FAIL: ") + (result.stdout + result.stderr).strip())
        return result.returncode


def prepare(version):
    if not SEMVER.match(version):
        raise SystemExit("VERSION must be semantic, for example 2.1.0")
    chart_path = ROOT / "k8s/Chart.yaml"
    text = chart_path.read_text()
    text = re.sub(r"(?m)^version: .*$", f"version: {version}", text)
    text = re.sub(r'(?m)^appVersion: .*$', f'appVersion: "{version}"', text)
    chart_path.write_text(text)
    previous = latest_tag()
    entry = [f"## {version} ({datetime.now(timezone.utc):%Y-%m-%d})", ""] + [f"- {s}" for s in changelog(previous)] + [""]
    changelog_path = ROOT / "CHANGELOG.md"
    existing = changelog_path.read_text() if changelog_path.exists() else "# Changelog\n\nPlatform releases; see docs/guides/releases.md.\n\n"
    head, _, rest = existing.partition("\n## ")
    changelog_path.write_text(head.rstrip("\n") + "\n\n" + "\n".join(entry) + ("\n## " + rest if rest else ""))
    print(f"Set version {version} in k8s/Chart.yaml and added it to CHANGELOG.md (changes since {previous or 'the first commit'}).\n"
          f"Review, then: git add k8s/Chart.yaml CHANGELOG.md && git commit -m 'Release {version}' && git tag -a v{version} -m 'Swirl Cloud {version}'")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inventory")
    sbom_parser = commands.add_parser("sbom")
    sbom_parser.add_argument("--output", type=Path)
    log_parser = commands.add_parser("changelog")
    log_parser.add_argument("--since")
    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("version")
    bundle_parser = commands.add_parser("bundle")
    bundle_parser.add_argument("--ref", default="HEAD")
    bundle_parser.add_argument("--output-dir", type=Path, default=Path.cwd() / "dist")
    bundle_parser.add_argument("--sign-key", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("bundle", type=Path)
    verify_parser.add_argument("--allowed-signers", type=Path)
    verify_parser.add_argument("--signer", default="release")
    args = parser.parse_args()
    if args.command == "inventory":
        items = inventory()
        print(f"Swirl Cloud {items['version']}: {len(items['images'])} images, {len(items['charts'])} charts")
        for image in items["images"]:
            print(f"  image  {image['name']}:{image['version'] or '-'}@{image['digest'][:19]}...  ({image['source']})")
        for chart in items["charts"]:
            print(f"  chart  {chart['name']} {chart['version']}")
        for tool in items["tools"]:
            print(f"  tool   {tool['name']} {tool['version']}")
    elif args.command == "sbom":
        document = json.dumps(sbom(inventory(), datetime.now(timezone.utc).replace(microsecond=0).isoformat()), indent=2)
        if args.output:
            args.output.write_text(document + "\n")
        else:
            print(document)
    elif args.command == "changelog":
        print("\n".join(f"- {s}" for s in changelog(args.since or latest_tag())))
    elif args.command == "prepare":
        prepare(args.version)
    elif args.command == "bundle":
        target, digest = bundle(args.ref, args.output_dir, args.sign_key)
        print(f"{target}\nManifest SHA-256: {digest}{' (signed)' if args.sign_key else ''}")
    elif args.command == "verify":
        return verify(args.bundle, args.allowed_signers, args.signer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
