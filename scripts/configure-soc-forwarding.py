#!/usr/bin/env python3
"""Forward audit and detection events to a SOC over syslog (RFC 5424 over TLS).

Checks that the receiver accepts a verified TLS connection, then records the
settings in the swirl-cloud Argo CD Application's Helm parameters and in the
infra/swirl-cloud-identity ConfigMap, so Argo CD renders the Fluent Bit output
and installer reruns keep it. --remove turns forwarding off; --show prints the
recorded settings. See docs/guides/soc-forwarding.md.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys

SOURCES = ("identity", "kubernetes-audit", "vault-audit", "detection", "host-journal")
DEFAULT_SOURCES = "identity,kubernetes-audit,vault-audit,detection"
HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
FIELDS = {"SOC_SYSLOG_HOST": "socSyslogHost", "SOC_SYSLOG_PORT": "socSyslogPort",
          "SOC_SYSLOG_SOURCES": "socSyslogSources", "SOC_SYSLOG_CA": "socSyslogCa"}
NAMESPACE = "infra"


class SocError(Exception):
    pass


def kubectl(*args, stdin=None):
    result = subprocess.run(["kubectl", "--request-timeout=30s", *args], input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise SocError(f"kubectl {' '.join(args[:3])}: {result.stderr.strip()[:300]}")
    return result.stdout


def settings(args):
    """Validated settings, as the installer records them (the CA base64-encoded)."""
    if not HOST.match(args.host):
        raise SocError("--host must be a host name or IPv4 address")
    if not 0 < args.port < 65536:
        raise SocError("--port must be a TCP port")
    sources = args.sources.split(",")
    if not sources or any(source not in SOURCES for source in sources):
        raise SocError(f"--sources must list {', '.join(SOURCES)}, separated by commas")
    ca = ""
    if args.ca_file:
        pem = Path(args.ca_file).read_text()
        if "-----BEGIN CERTIFICATE-----" not in pem:
            raise SocError(f"{args.ca_file} holds no PEM certificate")
        ca = base64.b64encode(pem.encode()).decode()
    return {"SOC_SYSLOG_HOST": args.host, "SOC_SYSLOG_PORT": str(args.port),
            "SOC_SYSLOG_SOURCES": ",".join(dict.fromkeys(sources)), "SOC_SYSLOG_CA": ca}


def check_receiver(values, timeout=10):
    """Open a TLS connection the way Fluent Bit will: certificate and host name verified."""
    context = ssl.create_default_context(cadata=base64.b64decode(values["SOC_SYSLOG_CA"]).decode()
                                         if values["SOC_SYSLOG_CA"] else None)
    try:
        with socket.create_connection((values["SOC_SYSLOG_HOST"], int(values["SOC_SYSLOG_PORT"])), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=values["SOC_SYSLOG_HOST"]):
                pass
    except (OSError, ssl.SSLError) as error:
        raise SocError(f"{values['SOC_SYSLOG_HOST']}:{values['SOC_SYSLOG_PORT']} refused a verified TLS connection: {error}")


def record(values):
    """Upsert the Helm parameters, then mirror them into the identity ConfigMap."""
    application = json.loads(kubectl("get", "application.argoproj.io", "swirl-cloud", "-n", NAMESPACE, "-o", "json"))
    parameters = application["spec"]["source"].setdefault("helm", {}).setdefault("parameters", [])
    by_name = {parameter["name"]: parameter for parameter in parameters}
    for key, name in FIELDS.items():
        if name in by_name:
            by_name[name].update(value=values[key], forceString=True)
        else:
            parameters.append({"name": name, "value": values[key], "forceString": True})
    kubectl("patch", "application.argoproj.io", "swirl-cloud", "-n", NAMESPACE, "--type", "merge",
            "-p", json.dumps({"spec": {"source": {"helm": {"parameters": parameters}}}}))
    kubectl("patch", "configmap", "swirl-cloud-identity", "-n", NAMESPACE, "--type", "merge",
            "-p", json.dumps({"data": values}))


def show():
    data = json.loads(kubectl("get", "configmap", "swirl-cloud-identity", "-n", NAMESPACE, "-o", "json")).get("data", {})
    if not data.get("SOC_SYSLOG_HOST"):
        print("SOC forwarding: off")
        return
    ca = data.get("SOC_SYSLOG_CA", "")
    fingerprint = hashlib.sha256(base64.b64decode(ca)).hexdigest()[:16] if ca else ""
    print(f"SOC forwarding: {data['SOC_SYSLOG_HOST']}:{data.get('SOC_SYSLOG_PORT', '')}")
    print(f"Sources: {data.get('SOC_SYSLOG_SOURCES', '')}")
    print(f"CA: {'recorded bundle, SHA-256 ' + fingerprint if ca else 'system CAs'}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--host", help="SOC syslog receiver host name or IPv4 address")
    action.add_argument("--remove", action="store_true", help="stop forwarding")
    action.add_argument("--show", action="store_true", help="print the recorded settings")
    parser.add_argument("--port", type=int, default=6514, help="TLS syslog port (default 6514)")
    parser.add_argument("--sources", default=DEFAULT_SOURCES, help=f"comma-separated, from {', '.join(SOURCES)}"
                        f" (default {DEFAULT_SOURCES})")
    parser.add_argument("--ca-file", help="PEM CA bundle that signed the receiver's certificate (default: system CAs)")
    parser.add_argument("--skip-check", action="store_true", help="record the settings without connecting first")
    args = parser.parse_args(argv)
    try:
        if args.show:
            show()
            return 0
        values = dict.fromkeys(FIELDS, "") if args.remove else settings(args)
        if not args.remove and not args.skip_check:
            check_receiver(values)
        record(values)
    except (SocError, OSError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        return 1
    print("SOC forwarding turned off." if args.remove else
          f"SOC forwarding to {values['SOC_SYSLOG_HOST']}:{values['SOC_SYSLOG_PORT']} recorded "
          f"({values['SOC_SYSLOG_SOURCES']}).")
    print("Argo CD applies it at its next sync and Fluent Bit restarts on every node.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
