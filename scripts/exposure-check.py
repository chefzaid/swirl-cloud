#!/usr/bin/env python3
"""Exposure check from outside the platform (TODO §46).

  exposure-check.py --address 203.0.113.10 [--address ...] --domain example.com \
      [--allowed-ports 22] [--output-dir DIR]

Run from a network outside the platform (the scheduled GitHub Actions workflow
exposure-check.yml). It checks:

- every TCP port of each server's public address: only --allowed-ports may
  accept a connection (the SSH port; web ports admit Cloudflare only, so they
  must refuse this network);
- every administration host behind the sign-in gate (read from the Ingress
  manifests) answers with the gate, never with the tool's own page;
- DNSSEC validates for the zone, through a validating public resolver;
- with CLOUDFLARE_API_TOKEN (Zone DNS read) and CLOUDFLARE_ZONE_ID, that no
  DNS record points at a name that no longer exists (open to takeover) or at an
  address outside --address and Cloudflare.

It writes exposure-<date>.json and .md (evidence for the questionnaire's
external-scan question) and exits 1 when anything differs. Server addresses
are inputs, never stored in the repository.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = "https://dns.google/resolve"
GATES = re.compile(r"infra-platform-sso@|infra-platform-access-[a-z0-9-]+@|corp-zammad-access@")
SIGN_IN = re.compile(r"/oauth2/(start|sign_in)|/auth/realms/|/protocol/openid-connect/auth|\.cloudflareaccess\.com/")


async def open_port(address, port, timeout):
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return None
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return port


async def scan(address, ports, timeout=2.0, concurrency=400):
    """The ports that accept a TCP connection."""
    limit = asyncio.Semaphore(concurrency)

    async def one(port):
        async with limit:
            return await open_port(address, port, timeout)
    return sorted(port for port in await asyncio.gather(*(one(port) for port in ports)) if port)


def gated_hosts(domain, root=ROOT):
    """Hosts whose Ingress carries a sign-in middleware, from the repository's manifests."""
    hosts = set()
    for path in list((root / "k8s/platform").glob("*.yaml")) + list((root / "k8s/corp").glob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text().replace("{{", "").replace("}}", "")):
            if not isinstance(document, dict) or document.get("kind") != "Ingress":
                continue
            chain = (document["metadata"].get("annotations") or {}).get("traefik.ingress.kubernetes.io/router.middlewares", "")
            if GATES.search(chain):
                hosts |= {rule["host"].replace("__PUBLIC_DOMAIN__", domain) for rule in document["spec"].get("rules", []) if rule.get("host")}
    return sorted(hosts)


def fetch(url, timeout=15):
    """(status, location, body start) without following redirects."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={"User-Agent": "swirl-cloud-exposure-check"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers.get("Location", ""), response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Location", ""), error.read(4096).decode("utf-8", "replace")


def gate_answer(status, location, body):
    """Whether an answer is the sign-in gate (a redirect to sign-in, or a refusal), not the tool itself."""
    if status in (301, 302, 303, 307, 308):
        return bool(SIGN_IN.search(location))
    return status in (401, 403)


def resolve(name, kind, fetcher=fetch):
    status, _, body = fetcher(f"{RESOLVER}?name={name}&type={kind}&do=1")
    if status != 200:
        raise RuntimeError(f"resolver answered HTTP {status}")
    return json.loads(body)


def dnssec(domain, fetcher=fetch):
    answer = resolve(domain, "SOA", fetcher)
    return answer.get("Status") == 0 and answer.get("AD") is True


def cloudflare_records(zone, token, opener=urllib.request.urlopen):
    records, page = [], 1
    while True:
        request = urllib.request.Request(f"https://api.cloudflare.com/client/v4/zones/{zone}/dns_records?per_page=100&page={page}",
                                         headers={"Authorization": f"Bearer {token}"})
        with opener(request, timeout=30) as response:
            body = json.load(response)
        records += body.get("result", [])
        if page >= body.get("result_info", {}).get("total_pages", 1):
            return records
        page += 1


def record_findings(records, addresses, fetcher=fetch):
    """Records that point at a name that no longer exists, or at an address the platform does not own."""
    findings = []
    owned = {ipaddress.ip_address(address) for address in addresses}
    for record in records:
        name, kind, content = record.get("name", ""), record.get("type", ""), record.get("content", "")
        if kind == "CNAME":
            target = resolve(content.rstrip("."), "A", fetcher)
            if target.get("Status") == 3:
                findings.append(f"{name} CNAME {content}: the target does not exist (open to takeover)")
        elif kind in ("A", "AAAA") and not record.get("proxied"):
            try:
                if ipaddress.ip_address(content) not in owned:
                    findings.append(f"{name} {kind} {content}: not one of the platform's addresses")
            except ValueError:
                findings.append(f"{name} {kind} {content}: not an address")
    return findings


def check(args, fetcher=fetch, scanner=None, records=None):
    scanner = scanner or (lambda address: asyncio.run(scan(address, range(1, 65536))))
    allowed = {int(port) for port in args.allowed_ports}
    result = {"time": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "domain": args.domain,
              "addresses": {}, "gated_hosts": {}, "dnssec": None, "dns_records": [], "findings": []}
    for address in args.address:
        open_ports = scanner(address)
        result["addresses"][address] = open_ports
        for port in sorted(set(open_ports) - allowed):
            result["findings"].append(f"{address}: TCP {port} accepts connections from outside (allowed: {sorted(allowed)})")
    for host in gated_hosts(args.domain):
        try:
            status, location, body = fetcher(f"https://{host}/")
        except (OSError, ssl.SSLError) as error:
            result["gated_hosts"][host] = f"no answer ({error.__class__.__name__})"
            continue
        gated = gate_answer(status, location, body)
        result["gated_hosts"][host] = f"HTTP {status}" + (" (sign-in gate)" if gated else "")
        if not gated:
            result["findings"].append(f"https://{host}/ answered HTTP {status} without the sign-in gate")
    try:
        result["dnssec"] = dnssec(args.domain, fetcher)
        if not result["dnssec"]:
            result["findings"].append(f"DNSSEC does not validate for {args.domain}")
    except (OSError, RuntimeError, ValueError) as error:
        result["findings"].append(f"DNSSEC could not be checked: {error}")
    if records is not None:
        result["dns_records"] = record_findings(records, args.address, fetcher)
        result["findings"] += result["dns_records"]
    return result


def markdown(result):
    lines = [f"# Exposure check of {result['domain']}", "", f"Run {result['time']} from outside the platform.", ""]
    lines += [f"- {address}: open TCP ports {', '.join(map(str, ports)) or 'none'}" for address, ports in result["addresses"].items()]
    lines += [f"- https://{host}/: {answer}" for host, answer in result["gated_hosts"].items()]
    lines.append(f"- DNSSEC: {'validates' if result['dnssec'] else 'does not validate' if result['dnssec'] is False else 'not checked'}")
    lines += ["", "## Differences", ""] + ([f"- {finding}" for finding in result["findings"]] or ["None."])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--address", action="append", default=[], help="a server's public address (repeatable)")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--allowed-ports", nargs="*", default=["22"], help="TCP ports that may answer (default 22)")
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()
    for address in args.address:
        ipaddress.ip_address(address)
    token, zone = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ZONE_ID")
    records = cloudflare_records(zone, token) if token and zone else None
    result = check(args, records=records)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = result["time"][:10]
    (out / f"exposure-{stamp}.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / f"exposure-{stamp}.md").write_text(markdown(result))
    print(markdown(result))
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
