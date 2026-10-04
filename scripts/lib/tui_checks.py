"""Host preflight and credential checks for the installer TUI.

Every check is read-only. Credentials are sent only to their own provider API,
in request headers, and never appear in results.
"""
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
GIB = 1024 ** 3


def command(*args, timeout=10):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 127, ""


def unit_exists(name):
    return command("systemctl", "cat", name)[0] == 0


def os_release():
    try:
        return dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    except OSError:
        return {}


def reachable(url):
    try:
        with urlopen(Request(url, method="HEAD", headers={"User-Agent": "swirl-cloud-preflight"}), timeout=8):
            return True
    except HTTPError:
        return True  # Any HTTP answer proves egress and TLS.
    except (URLError, OSError, ValueError):
        return False


def host_checks(root, values):
    results = []
    info = os_release()
    name = info.get("PRETTY_NAME", "unknown").strip('"')
    results.append((PASS if info.get("ID") == "ubuntu" else WARN, "Operating system",
                    name + ("" if info.get("ID") == "ubuntu" else " (the platform is tested on Ubuntu)")))
    results.append((PASS if platform.machine() in ("x86_64", "aarch64") else FAIL, "Architecture", platform.machine()))
    code, _ = command("sudo", "-n", "true")
    results.append((PASS, "sudo", "passwordless") if code == 0 else
                   (WARN, "sudo", "will ask for your password when the installation starts"))
    cpus = os.cpu_count() or 0
    results.append((PASS if cpus >= 8 else WARN if cpus >= 4 else FAIL, "CPU", f"{cpus} cores (8+ recommended)"))
    memory = next((int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                   if line.startswith("MemTotal:")), 0)
    results.append((PASS if memory >= 24 * GIB else WARN if memory >= 12 * GIB else FAIL, "Memory",
                    f"{memory / GIB:.0f} GiB (24+ GiB recommended for every platform service)"))
    free = shutil.disk_usage("/var/lib" if Path("/var/lib").exists() else "/").free
    results.append((PASS if free >= 100 * GIB else WARN if free >= 40 * GIB else FAIL, "Free disk",
                    f"{free / GIB:.0f} GiB under /var/lib (100+ GiB recommended)"))
    if unit_exists("k3s-agent.service"):
        results.append((FAIL, "K3s", "this host is a K3s worker; run the installer on the first control plane"))
    elif unit_exists("k3s.service"):
        results.append((INFO, "K3s", "control plane already installed; reruns keep it unless you choose a reinstall"))
    else:
        code, listeners = command("ss", "-ltnH")
        busy = sorted({port for line in listeners.splitlines() for port in ("80", "443", "6443")
                       if line.split()[3].rsplit(":", 1)[-1] == port}) if code == 0 else []
        results.append((WARN, "Ports", "already in use: " + ", ".join(busy)) if busy else
                       (PASS, "Ports", "80, 443 and 6443 are free"))
    code, synced = command("timedatectl", "show", "-p", "NTPSynchronized", "--value")
    results.append((PASS if synced == "yes" else WARN, "Clock", "synchronized" if synced == "yes" else
                    "not NTP-synchronized; certificates and etcd need accurate time"))
    targets = {"K3s releases": "https://github.com", "container images": "https://registry-1.docker.io/v2/",
               "Helm releases": "https://get.helm.sh"}
    unreachable = [label for label, url in targets.items() if not reachable(url)]
    results.append((FAIL if unreachable else PASS, "Internet egress",
                    "cannot reach " + ", ".join(unreachable) if unreachable else "downloads are reachable"))
    for tool in ("python3", "git", "curl"):
        if not shutil.which(tool):
            results.append((FAIL, tool, "missing; install it with apt-get"))
    code, revision = command("git", "-C", str(root), "describe", "--always", "--dirty")
    results.append((INFO, "Checkout", revision if code == 0 else "not a Git checkout"))
    domain = values.get("PLATFORM_DOMAIN")
    if domain:
        try:
            socket.getaddrinfo(domain, 443)
            results.append((INFO, "Domain", f"{domain} resolves"))
        except OSError:
            results.append((INFO, "Domain", f"{domain} does not resolve yet; Cloudflare setup can create the zone"))
    return results


def api(url, headers, timeout=15):
    request = Request(url, headers={"User-Agent": "swirl-cloud-installer", "Accept": "application/json", **headers})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"null")
        except ValueError:
            return error.code, None
    except (URLError, OSError, ValueError) as error:
        return 0, str(getattr(error, "reason", error))


def cloudflare(token, domain):
    headers = {"Authorization": "Bearer " + token}
    status, body = api("https://api.cloudflare.com/client/v4/user/tokens/verify", headers)
    if status != 200 or not isinstance(body, dict) or body.get("result", {}).get("status") != "active":
        return [(FAIL, "Cloudflare token", f"not accepted (HTTP {status})")]
    results = [(PASS, "Cloudflare token", "active")]
    if domain:
        status, body = api(f"https://api.cloudflare.com/client/v4/zones?name={quote(domain)}", headers)
        zones = body.get("result", []) if isinstance(body, dict) else []
        if zones:
            zone = zones[0]
            results.append((PASS if zone.get("status") == "active" else WARN, "Cloudflare zone",
                            f"{domain}: {zone.get('status')} in account {zone.get('account', {}).get('name', '?')}"))
        else:
            results.append((INFO, "Cloudflare zone", f"{domain} is not in this account yet; the installer can add it"))
    return results


def github(username, token, repositories):
    headers = {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}
    status, body = api("https://api.github.com/user", headers)
    if status != 200 or not isinstance(body, dict):
        return [(FAIL, "GitHub token", f"not accepted (HTTP {status})")]
    login = body.get("login", "")
    results = [(PASS if login.lower() == (username or login).lower() else WARN, "GitHub token",
                f"authenticates as {login}" + ("" if login.lower() == (username or login).lower() else f", not {username}"))]
    for repository in filter(None, (repositories or "").split(",")):
        path = repository if "/" in repository else f"{username or login}/{repository}"
        status, body = api(f"https://api.github.com/repos/{quote(path)}", headers)
        admin = isinstance(body, dict) and body.get("permissions", {}).get("admin")
        results.append((PASS if status == 200 and admin else WARN if status == 200 else FAIL, f"Repository {path}",
                        "accessible with admin rights" if status == 200 and admin else
                        "readable, but sync setup needs Administration access" if status == 200 else f"HTTP {status}"))
    return results


def tailscale(token, tailnet):
    status, _ = api(f"https://api.tailscale.com/api/v2/tailnet/{quote(tailnet or '-')}/devices?fields=default",
                    {"Authorization": "Bearer " + token})
    return [(PASS, "Tailscale token", "can list tailnet devices") if status == 200 else
            (FAIL, "Tailscale token", f"not accepted (HTTP {status})")]


def credential_checks(values):
    results = []
    if values.get("CLOUDFLARE_API_TOKEN"):
        results += cloudflare(values["CLOUDFLARE_API_TOKEN"], values.get("PLATFORM_DOMAIN"))
    if values.get("GITHUB_ADMIN_TOKEN"):
        results += github(values.get("GITHUB_USERNAME"), values["GITHUB_ADMIN_TOKEN"], values.get("GITHUB_REPOSITORIES"))
    if values.get("TAILSCALE_API_TOKEN"):
        results += tailscale(values["TAILSCALE_API_TOKEN"], values.get("TAILSCALE_TAILNET"))
    for name, label in (("OVH_APPLICATION_SECRET", "OVHcloud API keys"), ("BACKUP_S3_SECRET_KEY", "S3 backup keys")):
        if values.get(name):
            results.append((INFO, label, "checked by the installer when it runs (requests must be signed)"))
    return results or [(INFO, "Credentials", "no Cloudflare, GitHub or Tailscale credentials entered yet")]


def render(results):
    return "\n".join(f"[{status}] {title}: {detail}" for status, title, detail in results)
