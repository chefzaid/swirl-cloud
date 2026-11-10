#!/usr/bin/env python3
"""Render validated public identity into a private installation staging directory."""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import re

import yaml

spec = importlib.util.spec_from_file_location("platform_identity", Path(__file__).with_name("platform-identity.py"))
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


PORTAL_FILES = ("k8s/platform/homepage.yaml", "k8s/files/homepage-custom.js")


def portal_in_french(content, root):
    """The French portal text templates/resources.yaml applies for portalLanguage fr."""
    content = content.replace("\n    language: en\n", "\n    language: fr\n")
    content = content.replace('const language = "en";', 'const language = "fr";')
    for english, french in yaml.safe_load((root / "k8s/files/homepage-fr.yaml").read_text()).items():
        content = content.replace(f"description: {english}\n", f"description: {french}\n")
    return content


SOC_SOURCES = ("identity", "kubernetes-audit", "vault-audit", "detection", "host-journal")


def indent(text, width):
    """Helm's indent: every line, empty ones too."""
    return "\n".join(" " * width + line for line in text.split("\n"))


def soc_forwarding(content, root, settings):
    """The Fluent Bit SOC output templates/resources.yaml adds when SOC_SYSLOG_HOST is set."""
    files = root / "k8s/files/soc-forwarding"
    chosen = settings["SOC_SYSLOG_SOURCES"].split(",")
    pipeline = "".join((files / f"{source}.conf").read_text() + "\n" for source in SOC_SOURCES if source in chosen)
    output = (files / "output.conf").read_text()
    data = "  soc-forwarding.lua: |\n" + indent((files / "soc-forwarding.lua").read_text().removesuffix("\n"), 4)
    mounts = ("            - name: config\n              mountPath: /fluent-bit/etc/soc-forwarding.lua\n"
              "              subPath: soc-forwarding.lua\n              readOnly: true")
    if settings["SOC_SYSLOG_CA"]:
        output = output.replace("    tls.verify_hostname       On\n", "    tls.verify_hostname       On\n"
                                "    tls.ca_file               /fluent-bit/etc/soc-forwarding-ca.crt\n")
        certificate = base64.b64decode(settings["SOC_SYSLOG_CA"]).decode()
        data += "\n  soc-forwarding-ca.crt: |\n" + indent(certificate.removesuffix("\n"), 4)
        mounts += ("\n            - name: config\n              mountPath: /fluent-bit/etc/soc-forwarding-ca.crt\n"
                   "              subPath: soc-forwarding-ca.crt\n              readOnly: true")
    content = content.replace("    # swirl-cloud:soc-forwarding-pipeline\n", indent((pipeline + output).removesuffix("\n"), 4) + "\n")
    content = content.replace("  # swirl-cloud:soc-forwarding-files\n", data + "\n")
    return content.replace("            # swirl-cloud:soc-forwarding-mounts\n", mounts + "\n")


def render(root):
    settings = {key: os.environ[key] for key in identity.FIELDS}
    replacements = {"__" + key + "__": value for key, value in settings.items()}
    replacements.update({
        "__PUBLIC_DOMAIN__": settings["PLATFORM_DOMAIN"],
        "__ORGANIZATION_NAME_JSON__": json.dumps(settings["ORGANIZATION_NAME"]),
        "__GITLAB_GROUP_NAME_JSON__": json.dumps(settings["GITLAB_GROUP_NAME"]),
        "__CLOUDFLARE_ACCESS_IDP_NAME_JSON__": json.dumps(settings["CLOUDFLARE_ACCESS_IDP_NAME"]),
        "__PLATFORM_TITLE_JSON__": json.dumps(settings["ORGANIZATION_NAME"] + " Cloud"),
        "__APPS_ENABLED__": os.environ["INSTALL_APPS"],
        "__DESCHEDULER_ENABLED__": os.environ["INSTALL_DESCHEDULER"],
        "__HA_VALUES_OBJECT__": '{"highAvailabilityEnabled":false}',
    })
    pattern = re.compile("|".join(map(re.escape, replacements)))
    for base in (root / "k8s", root / "config"):
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix == ".tgz" or "templates" in path.relative_to(root).parts:
                continue
            try:
                source = path.read_text()
            except UnicodeDecodeError:
                continue
            if path == root / "k8s/platform/logging-agent.yaml" and settings["SOC_SYSLOG_HOST"]:
                source = soc_forwarding(source, root, settings)
            content = pattern.sub(lambda match: replacements[match[0]], source)
            if path.relative_to(root).as_posix() in PORTAL_FILES and settings["PORTAL_LANGUAGE"] == "fr":
                content = portal_in_french(content, root)
            # configure-ingress.sh resolves the origin HSTS max-age from the edge inputs.
            unresolved = set(re.findall(r"__[A-Z][A-Z0-9_]+__", content)) - {"__RUNNER_TOKEN__", "__HSTS_MAX_AGE__"}
            if unresolved:
                raise ValueError(f"Unresolved placeholders in {path}: {sorted(set(unresolved))}")
            path.write_text(content)
    values_path = root / "k8s/values.yaml"
    values = yaml.safe_load(values_path.read_text())
    values.update({identity.FIELDS[key]: value for key, value in settings.items()})
    values.update(appsEnabled=os.environ["INSTALL_APPS"] == "true",
                  deschedulerEnabled=os.environ["INSTALL_DESCHEDULER"] == "true")
    values_path.write_text(yaml.safe_dump(values, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    render(parser.parse_args().root)
