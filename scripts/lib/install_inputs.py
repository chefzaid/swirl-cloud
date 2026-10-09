"""Versioned installation-input schema and the per-install `.env` data format.

Every operator-facing input of install-control-plane.sh is declared here once.
The TUI walks this schema, and the installer engine (`--env-file`) validates a
file against it before importing it. Files are parsed as data and never sourced
or evaluated by a shell. Version pins and service inventories stay in the
checked-in config/platform.env contract and are not installation inputs.
"""

from dataclasses import dataclass, field
import ipaddress
import os
from pathlib import Path
import re
import stat
import tempfile

SCHEMA_VERSION = 1
VERSION_KEY = "SWIRL_CLOUD_INSTALL_INPUTS"

TRUE, FALSE = ("true", "1", "yes", "y"), ("false", "0", "no", "n")
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}\Z")
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*\Z")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
GIT_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9_.~%+-]+)+\.git\Z")
KEY = re.compile(r"[A-Z][A-Z0-9_]*\Z")


@dataclass(frozen=True)
class Input:
    name: str
    section: str
    prompt: str
    kind: str = "text"            # text, bool, int, choice, secret, list, emails, path
    default: str = ""             # empty means the installer derives or detects it
    choices: tuple = ()
    pattern: re.Pattern = None
    express: bool = False         # asked on the TUI express screen
    when: object = None           # values -> bool; the input applies only then
    required: object = None       # values -> bool; must be set when it applies
    help: str = ""
    extra: tuple = field(default=())

    @property
    def secret(self):
        return self.kind == "secret"


def flag(values, name, default="false"):
    return (values.get(name) or default).lower() in TRUE


def nodes_added(values):
    return any(values.get(name) for name in ("K3S_CONTROL_PLANE_HOSTS", "K3S_CONTROL_PLANE_IPS",
                                             "K3S_WORKER_HOSTS", "K3S_WORKER_IPS"))


def github_source(values):
    """True for GitHub import, False for existing GitLab projects, None when onboarding is off."""
    if not flag(values, "CONFIGURE_REPOSITORY_SYNC"):
        return None
    return (values.get("REPOSITORY_SOURCE") or "github") == "github"


def transport(values):
    return values.get("K3S_NODE_TRANSPORT", "")


def control_planes(values):
    count = values.get("CONTROL_PLANE_COUNT", "")
    return int(count) if re.fullmatch(r"[0-9]{1,9}", count) else 1


def cloudflare(values):
    exposure = values.get("SERVER_EXPOSURE") or "internet"
    return flag(values, "CONFIGURE_CLOUDFLARE", "true" if exposure == "internet" else "false") \
        or flag(values, "HIGH_AVAILABILITY_ENABLED")


def platform_services(values):
    return flag(values, "DEPLOY_PLATFORM_SERVICES", "true") or (values.get("INSTALL_SCOPE") or "apps") == "apps"


def platform_mail(values):
    return flag(values, "CONFIGURE_PLATFORM_MAIL")


SECTIONS = ("Identity", "Repositories", "Exposure and scope", "Nodes", "Components",
            "Backups", "Email", "Public access", "Administrators")

INPUTS = (
    # Identity
    Input("ORGANIZATION_NAME", "Identity", "Company or organization display name", express=True,
          required=lambda v: True, help="Shown in the portal, GitLab and Access; spaces and punctuation are allowed."),
    Input("PLATFORM_DOMAIN", "Identity", "Public base domain for this cluster", pattern=DOMAIN, express=True,
          required=lambda v: True, help="For example example.com. Services use subdomains of it."),
    Input("ORGANIZATION_SLUG", "Identity", "Organization identifier (lowercase DNS label)", pattern=LABEL,
          help="Defaults to the first label of the public domain."),
    Input("INTERNAL_DNS_ZONE", "Identity", "Private service DNS zone", pattern=DOMAIN,
          help="Defaults to internal.<public domain>."),
    Input("GITLAB_GROUP_PATH", "Identity", "GitLab group path", pattern=NAME, help="Defaults to the organization identifier."),
    Input("GITLAB_GROUP_NAME", "Identity", "GitLab group display name", help="Defaults to the organization name."),
    Input("GITLAB_PROJECT_NAME", "Identity", "Platform GitLab project name", pattern=NAME, help="Defaults to swirl-cloud."),
    Input("KEYCLOAK_REALM", "Identity", "Application SSO realm", pattern=NAME, help="Defaults to the organization identifier."),
    Input("TLS_SECRET_NAME", "Identity", "Shared TLS secret name", pattern=re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\Z"),
          help="Defaults to the public domain with dots replaced by hyphens, plus -tls."),
    Input("SONAR_ALM_SETTING", "Identity", "SonarQube GitLab integration name", pattern=NAME),
    Input("CLOUDFLARE_ACCESS_IDP_NAME", "Identity", "Cloudflare Access identity provider name"),
    Input("CLOUDFLARE_ACCESS_TEAM_NAME", "Identity", "Cloudflare Zero Trust team label", pattern=LABEL,
          help="Existing team label, or swirl-cloud-<domain-with-hyphens> for a new team."),
    Input("CONTROL_PLANE_NODE_NAME", "Identity", "Kubernetes name of this node", pattern=LABEL,
          help="Defaults to the lowercase host name."),
    # Repositories
    Input("GITOPS_REPOSITORY_URL", "Repositories", "Cluster GitOps repository URL (.git)", pattern=GIT_URL, express=True,
          help="Argo CD reconciles the platform from it. Defaults to this checkout's GitHub remote."),
    Input("CONFIGURE_REPOSITORY_SYNC", "Repositories", "Import GitHub repositories into GitLab with two-way sync?",
          kind="bool", default="false", express=True),
    Input("REPOSITORY_SOURCE", "Repositories", "Repository source", kind="choice", choices=("github", "gitlab"),
          default="github", when=lambda v: flag(v, "CONFIGURE_REPOSITORY_SYNC"),
          help="gitlab deploys projects already in the platform GitLab group, with no GitHub account (reruns)."),
    Input("GITLAB_PROJECTS", "Repositories", "GitLab projects to deploy (comma-separated)", kind="list",
          when=lambda v: github_source(v) is False, required=lambda v: True),
    Input("GITHUB_USERNAME", "Repositories", "GitHub username", express=True,
          when=github_source, required=lambda v: True),
    Input("GITHUB_ADMIN_TOKEN", "Repositories", "GitHub fine-grained personal access token", kind="secret", express=True,
          when=github_source, required=lambda v: True),
    Input("GITHUB_REPOSITORIES", "Repositories", "Repositories to import (comma-separated)", kind="list", express=True,
          when=github_source, required=lambda v: True),
    Input("DEPLOY_REPOSITORIES", "Repositories", "Repositories to deploy: all, none, or comma-separated names", express=True,
          default="none", when=lambda v: flag(v, "CONFIGURE_REPOSITORY_SYNC"), required=lambda v: True),
    Input("DEPLOYMENT_ENVIRONMENTS_FILE", "Repositories", "Deployment environments inventory file (optional)", kind="path",
          help="A YAML inventory of int/uat/prod targets; see docs/deployment.md."),
    # Exposure and scope
    Input("SERVER_EXPOSURE", "Exposure and scope", "Is this server internet-exposed or local-only?", kind="choice",
          choices=("internet", "local"), default="internet", express=True),
    Input("INSTALL_SCOPE", "Exposure and scope", "Deployment scope", kind="choice", choices=("apps", "infra"), default="apps",
          help="apps also installs Odoo in corp; infra omits it."),
    # Nodes
    Input("CONTROL_PLANE_COUNT", "Nodes", "Control-plane nodes including this host (1, 3, 5, ...)", kind="int", express=True,
          help="Desired final count, including registered nodes; must be odd."),
    Input("CLUSTER_NODE_COUNT", "Nodes", "Total cluster nodes including control planes", kind="int", express=True),
    Input("CONTROL_PLANE_SCHEDULABLE", "Nodes", "Allow control planes to run workloads?", kind="bool",
          help="Defaults to true without workers, otherwise false."),
    Input("K3S_NODE_TRANSPORT", "Nodes", "Private node transport", kind="choice", choices=("vrack", "tailscale", "lan"),
          express=True, required=nodes_added),
    Input("K3S_CONTROL_PLANE_HOSTS", "Nodes", "Additional control planes as SSH targets (user@host, comma-separated)",
          kind="list", express=True, when=lambda v: transport(v) == "tailscale"),
    Input("K3S_WORKER_HOSTS", "Nodes", "Workers as SSH targets (user@host, comma-separated)", kind="list", express=True,
          when=lambda v: transport(v) == "tailscale"),
    Input("K3S_CONTROL_PLANE_IPS", "Nodes", "Additional control-plane private IPv4 addresses (comma-separated)", kind="list",
          express=True, when=lambda v: transport(v) in ("vrack", "lan")),
    Input("K3S_WORKER_IPS", "Nodes", "Worker private IPv4 addresses (comma-separated)", kind="list", express=True,
          when=lambda v: transport(v) in ("vrack", "lan")),
    Input("K3S_CONTROL_PLANE_SSH_USER", "Nodes", "SSH user for additional control planes",
          when=lambda v: transport(v) in ("vrack", "lan")),
    Input("K3S_WORKER_SSH_USER", "Nodes", "SSH user for workers", when=lambda v: transport(v) in ("vrack", "lan")),
    Input("K3S_CONTROL_PLANE_SSH_PORT", "Nodes", "SSH port for additional control planes", kind="int", when=nodes_added),
    Input("K3S_WORKER_SSH_PORT", "Nodes", "SSH port for workers", kind="int", when=nodes_added),
    Input("K3S_CONTROL_PLANE_IDENTITY_FILE", "Nodes", "SSH private key for additional control planes", kind="path",
          when=nodes_added),
    Input("K3S_WORKER_IDENTITY_FILE", "Nodes", "SSH private key for workers", kind="path", when=nodes_added),
    Input("K3S_PRIVATE_ADDRESS", "Nodes", "This control plane's private RFC1918 IPv4 address", express=True,
          when=lambda v: transport(v) in ("vrack", "lan"), required=lambda v: transport(v) == "vrack"),
    Input("K3S_NODE_NETWORK_CIDR", "Nodes", "Trusted private node CIDR (every address in it reaches cluster ports)",
          express=True, when=lambda v: transport(v) in ("vrack", "lan"), required=lambda v: True),
    Input("K3S_API_VIP", "Nodes", "Unused node-network IPv4 for the Kubernetes API VIP", express=True,
          when=lambda v: transport(v) in ("vrack", "lan") and control_planes(v) > 1, required=lambda v: True,
          help="keepalived moves it to a control plane whose API is ready; it must not be a node address."),
    Input("K3S_PRIVATE_INTERFACE", "Nodes", "Private NIC name (vRack)", when=lambda v: transport(v) == "vrack"),
    Input("K3S_PRIVATE_INTERFACE_MAC", "Nodes", "Expected private NIC MAC address (vRack)",
          pattern=re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z"), when=lambda v: transport(v) == "vrack"),
    Input("K3S_VRACK_VLAN_ID", "Nodes", "vRack VLAN ID (blank for untagged)", kind="int", when=lambda v: transport(v) == "vrack"),
    Input("TAILSCALE_API_TOKEN", "Nodes", "Tailscale API access token (tskey-api-...)", kind="secret", express=True,
          when=lambda v: transport(v) == "tailscale", required=nodes_added),
    Input("TAILSCALE_TAILNET", "Nodes", "Tailnet name ('-' uses the token's tailnet)", when=lambda v: transport(v) == "tailscale"),
    Input("TAILSCALE_MESH_NAME", "Nodes", "Tailscale mesh/cluster name", when=lambda v: transport(v) == "tailscale"),
    Input("TAILSCALE_NODE_HOSTNAME", "Nodes", "Tailscale hostname for this node", when=lambda v: transport(v) == "tailscale"),
    Input("TAILSCALE_AUTH_KEY_EXPIRY_SECONDS", "Nodes", "One-use node auth-key lifetime in seconds", kind="int",
          when=lambda v: transport(v) == "tailscale"),
    Input("OVH_VRACK_AUTOMATE_ACCOUNT", "Nodes", "Attach servers to the vRack through the OVHcloud API?", kind="bool",
          default="false", express=True, when=lambda v: transport(v) == "vrack"),
    Input("OVH_API_ENDPOINT", "Nodes", "OVHcloud API endpoint", kind="choice", choices=("ovh-eu", "ovh-ca", "ovh-us"),
          default="ovh-eu", when=lambda v: transport(v) == "vrack" and flag(v, "OVH_VRACK_AUTOMATE_ACCOUNT")),
    *(Input(name, "Nodes", prompt, kind=kind, express=True,
            when=lambda v: transport(v) == "vrack" and flag(v, "OVH_VRACK_AUTOMATE_ACCOUNT"), required=lambda v: True)
      for name, prompt, kind in (("OVH_APPLICATION_KEY", "OVHcloud API application key", "text"),
                                 ("OVH_APPLICATION_SECRET", "OVHcloud API application secret", "secret"),
                                 ("OVH_CONSUMER_KEY", "OVHcloud API consumer key", "secret"),
                                 ("OVH_VRACK_SERVICE_NAME", "OVHcloud vRack service name (pn-...)", "text"),
                                 ("OVH_CONTROL_PLANE_SERVICE_NAME", "This server's OVHcloud service name", "text"))),
    Input("SERVER_MONTHLY_COST", "Nodes", "Monthly cost of this server for cost showback (optional)",
          pattern=re.compile(r"[0-9]{1,9}(?:[.][0-9]{1,2})?\Z"),
          help="Rent, or purchase price spread over its service life plus hosting. Blank skips cost showback."),
    Input("COST_CURRENCY", "Nodes", "Currency of server costs (ISO 4217 code)", pattern=re.compile(r"[A-Z]{3}\Z"),
          default="EUR", when=lambda v: bool(v.get("SERVER_MONTHLY_COST"))),
    Input("HIGH_AVAILABILITY_ENABLED", "Nodes", "Activate the opt-in high-availability profile?", kind="bool", default="false",
          help="Refused until three control planes exist; see docs/guides/high-availability.md."),
    # Components
    Input("INSTALL_PREREQS", "Components", "Install/upgrade system prerequisites?", kind="bool", default="true"),
    Input("INSTALL_DEVELOPER_TOOLS", "Components", "Also install developer tools (Java 21, Maven, Docker)?", kind="bool",
          default="false"),
    Input("INSTALL_K3S", "Components", "Install or reinstall the K3s control plane?", kind="bool",
          help="Defaults to true only when K3s is not installed."),
    *(Input(name, "Components", prompt, kind="bool", default="true") for name, prompt in (
        ("INSTALL_LONGHORN", "Install/upgrade Longhorn storage?"),
        ("INSTALL_INGRESS", "Install/upgrade the Traefik ingress controller?"),
        ("INSTALL_VAULT_STACK", "Install/upgrade Vault and External Secrets?"),
        ("DEPLOY_DATA_STORES", "Deploy/upgrade data stores (PostgreSQL, Kafka, Redis, MongoDB)?"),
        ("DEPLOY_PLATFORM_SERVICES", "Deploy/upgrade platform services?"),
        ("INSTALL_DESCHEDULER", "Install/upgrade Descheduler resources?"),
        ("INSTALL_ARGOCD", "Install/upgrade Argo CD?"))),
    # Backups
    Input("CONFIGURE_OFFSITE_BACKUPS", "Backups", "Configure encrypted off-node backups in S3-compatible storage?",
          kind="bool", default="false", express=True),
    Input("BACKUP_S3_ENDPOINT", "Backups", "S3-compatible HTTPS endpoint", pattern=re.compile(r"https://\S+\Z"), express=True,
          when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"), required=lambda v: True),
    Input("BACKUP_S3_REGION", "Backups", "S3 region", pattern=re.compile(r"[A-Za-z0-9._-]+\Z"), express=True,
          when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"), required=lambda v: True),
    Input("BACKUP_S3_BUCKET", "Backups", "Private backup bucket", pattern=re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]\Z"),
          express=True, when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"), required=lambda v: True),
    Input("BACKUP_S3_ACCESS_KEY", "Backups", "S3 access-key ID", kind="secret", express=True,
          when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"), required=lambda v: True),
    Input("BACKUP_S3_SECRET_KEY", "Backups", "S3 secret access key", kind="secret", express=True,
          when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"), required=lambda v: True),
    Input("BACKUP_REPOSITORY_PASSWORD", "Backups", "Recovery-archive encryption password (16+ characters)", kind="secret",
          express=True, pattern=re.compile(r".{16,}\Z", re.S), when=lambda v: flag(v, "CONFIGURE_OFFSITE_BACKUPS"),
          required=lambda v: True, help="Keep a copy outside this cluster; restores need it."),
    # Email
    Input("CONFIGURE_PLATFORM_MAIL", "Email", "Send platform email (alerts, password resets) through an SMTP relay?",
          kind="bool", default="false", express=True,
          help="Alerts, Keycloak password resets, GitLab and Odoo use it; see docs/guides/platform-mail.md."),
    Input("PLATFORM_MAIL_HOST", "Email", "SMTP relay host", pattern=re.compile(r"[A-Za-z0-9.:-]+\Z"), express=True,
          when=platform_mail, required=lambda v: True, help="For example smtp.example.com."),
    Input("PLATFORM_MAIL_TLS", "Email", "SMTP TLS mode", kind="choice", choices=("ssl", "starttls"), default="ssl",
          when=platform_mail, help="ssl is implicit TLS (usually port 465); starttls usually uses port 587."),
    Input("PLATFORM_MAIL_PORT", "Email", "SMTP relay port", kind="int", when=platform_mail,
          help="Defaults to 465 for ssl and 587 for starttls."),
    Input("PLATFORM_MAIL_USERNAME", "Email", "SMTP username", express=True, when=platform_mail, required=lambda v: True),
    Input("PLATFORM_MAIL_PASSWORD", "Email", "SMTP password or API key", kind="secret", express=True,
          when=platform_mail, required=lambda v: True),
    Input("PLATFORM_MAIL_FROM", "Email", "Sender address", pattern=EMAIL, when=platform_mail,
          help="Defaults to cloud@<public domain>; the relay must accept it as a sender."),
    Input("PLATFORM_MAIL_FROM_NAME", "Email", "Sender display name", when=platform_mail,
          help="Defaults to '<organization> Cloud'."),
    Input("PLATFORM_MAIL_ALERT_TO", "Email", "Alert recipients (comma-separated)", kind="list", when=platform_mail,
          help="Critical and warning alerts are emailed here; defaults to the platform administrator's email."),
    # Public access
    Input("CONFIGURE_CLOUDFLARE", "Public access", "Configure Cloudflare public DNS, TLS and Access?", kind="bool",
          help="Defaults to true for internet-exposed servers."),
    Input("CLOUDFLARE_API_TOKEN", "Public access", "Cloudflare User API Token (cfut_...)", kind="secret", express=True,
          pattern=re.compile(r"cfut_\S+\Z"), when=cloudflare, required=lambda v: True,
          help="Permissions are listed in docs/dns.md#cloudflare."),
    Input("CLOUDFLARE_ENABLE_ACCESS", "Public access", "Protect administration hosts with Cloudflare Access?", kind="bool",
          default="true", when=cloudflare),
    Input("CLOUDFLARE_ACCESS_ALLOWED_EMAILS", "Public access", "Emails allowed into administration UIs (space-separated)",
          kind="emails", express=True, when=lambda v: cloudflare(v) and flag(v, "CLOUDFLARE_ENABLE_ACCESS", "true"),
          required=lambda v: True),
    Input("CLOUDFLARE_ACCOUNT_ID", "Public access", "Cloudflare account ID (only if the token sees several)",
          pattern=re.compile(r"[a-f0-9]{32}\Z"), when=cloudflare),
    Input("CLOUDFLARE_NODE_DNS_LABEL", "Public access", "Public administration DNS label for this node", pattern=LABEL,
          when=cloudflare),
    Input("CLOUDFLARE_PUBLISH_APEX", "Public access", "Also manage the zone apex DNS record?", kind="bool", default="false",
          when=cloudflare),
    *(Input(name, "Public access", prompt, kind=kind, when=cloudflare) for name, prompt, kind in (
        ("CLOUDFLARE_PUBLISH_NODE_DNS", "Publish the node's unproxied administration record?", "bool"),
        ("CLOUDFLARE_MIN_TLS_VERSION", "Minimum TLS version (for example 1.2)", "text"),
        ("CLOUDFLARE_ENABLE_HSTS", "Enable HSTS?", "bool"),
        ("CLOUDFLARE_HSTS_MAX_AGE", "HSTS max-age in seconds", "int"),
        ("CLOUDFLARE_ENABLE_DNSSEC", "Enable DNSSEC?", "bool"),
        ("CLOUDFLARE_LOCK_ORIGIN", "Allow web ports only from Cloudflare?", "bool"),
        ("CLOUDFLARE_ENABLE_WAF", "Enable the managed WAF rules?", "bool"),
        ("CLOUDFLARE_ENABLE_RATE_LIMIT", "Enable the login rate limit?", "bool"),
        ("CLOUDFLARE_ENABLE_CACHE_RULES", "Enable the private-response cache bypass?", "bool"),
        ("CLOUDFLARE_ENABLE_REGISTRY_API_COMPATIBILITY", "Exempt the Registry API from browser challenges?", "bool"),
        ("CLOUDFLARE_ACCESS_SESSION_DURATION", "Access session duration (for example 24h)", "text"),
        ("CLOUDFLARE_HOST_LABELS", "Public host labels replacing the defaults (space-separated)", "text"),
        ("CLOUDFLARE_ACCESS_HOST_LABELS", "Access-protected host labels replacing the defaults (space-separated)", "text"))),
    # Administrators
    Input("KEYCLOAK_SSO_BOOTSTRAP_USERNAME", "Administrators", "Platform administrator login (username or email)",
          express=True, when=platform_services, required=lambda v: True),
    Input("KEYCLOAK_SSO_BOOTSTRAP_PASSWORD", "Administrators", "Platform administrator password", kind="secret", express=True,
          when=platform_services, required=lambda v: True,
          help="12+ characters with lowercase, uppercase, digit and symbol."),
    Input("ROTATE_LOCAL_ADMIN_PASSWORDS", "Administrators", "Use one password for all local infrastructure superusers?",
          kind="bool", default="false", when=platform_services),
    Input("LOCAL_ADMIN_PASSWORD", "Administrators", "Local infrastructure administrator password", kind="secret",
          when=lambda v: platform_services(v) and flag(v, "ROTATE_LOCAL_ADMIN_PASSWORDS"), required=lambda v: True),
)

BY_NAME = {item.name: item for item in INPUTS}
assert len(BY_NAME) == len(INPUTS) and set(item.section for item in INPUTS) <= set(SECTIONS)

# Any secret may instead name a protected file holding it (NAME_FILE=/path), so
# the input file itself can stay free of inline credentials. The file is read
# only when the engine imports the inputs; it works before Vault exists.
FILE_SUFFIX = "_FILE"
SECRET_FILES = {item.name + FILE_SUFFIX: item for item in INPUTS if item.secret}
MAX_SECRET_FILE = 64 * 1024


def read_secret_file(path):
    """Return (value, problem); the value is the file's single line."""
    path = Path(path).expanduser()
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return None, "must name a regular file"
        if info.st_mode & 0o077:
            return None, "file must not be accessible by group or others (chmod 600)"
        if info.st_uid not in (os.getuid(), 0):
            return None, "file must be owned by the installing user or root"
        if info.st_size > MAX_SECRET_FILE:
            return None, "file is too large for a single secret"
        value = path.read_text().removesuffix("\n").removesuffix("\r")
    except (OSError, UnicodeError):
        return None, "file cannot be read"
    if not value or "\n" in value or any(ord(c) < 32 for c in value):
        return None, "file must contain exactly one non-empty line"
    return value, None


def resolve(values):
    """Replace NAME_FILE references with their contents; return (values, problems)."""
    resolved, problems = dict(values), []
    for reference, item in SECRET_FILES.items():
        path = resolved.pop(reference, "")
        if not path:
            continue
        if values.get(item.name):
            problems.append(f"{item.name}: set either the value or {reference}, not both")
            continue
        value, problem = read_secret_file(path)
        if problem:
            problems.append(f"{reference}: {problem}")
        else:
            resolved[item.name] = value
    return resolved, problems


class InputError(ValueError):
    """A list of operator-facing problems; never includes secret values."""

    def __init__(self, problems):
        super().__init__("\n".join(problems))
        self.problems = list(problems)


def applies(item, values):
    return item.when is None or bool(item.when(values))


def check_value(item, value):
    if item.kind == "bool":
        return None if value.lower() in TRUE + FALSE else "must be true or false"
    if item.kind == "int":
        return None if re.fullmatch(r"[0-9]{1,9}", value) else "must be a whole number"
    if item.kind == "choice" and value not in item.choices:
        return "must be one of: " + ", ".join(item.choices)
    if item.kind == "emails" and not all(EMAIL.fullmatch(part) for part in value.split()):
        return "must be space-separated email addresses"
    if item.kind == "list" and any(not part or any(c.isspace() for c in part) for part in value.split(",")):
        return "must be a comma-separated list without spaces or empty entries"
    if item.pattern is not None and not item.pattern.fullmatch(value):
        return "has an invalid format"
    return None


def validate(values):
    """Return every problem in a parsed value mapping; an empty list means valid."""
    referenced = {item.name for reference, item in SECRET_FILES.items() if values.get(reference)}
    values, problems = resolve(values)
    for name in values:
        if name != VERSION_KEY and name not in BY_NAME:
            problems.append(f"{name}: unknown input (version pins belong to config/platform.env)")
    if values.get(VERSION_KEY) != str(SCHEMA_VERSION):
        problems.append(f"{VERSION_KEY}: must be {SCHEMA_VERSION} (this file uses schema version {values.get(VERSION_KEY) or 'none'})")
    for item in INPUTS:
        value = values.get(item.name, "")
        if value and (error := check_value(item, value)):
            problems.append(f"{item.name}: {error}")
        elif not value and item.name not in referenced and applies(item, values) and item.required and item.required(values):
            problems.append(f"{item.name}: required — {item.prompt}")
    counts = {name: int(values[name]) for name in ("CONTROL_PLANE_COUNT", "CLUSTER_NODE_COUNT")
              if re.fullmatch(r"[0-9]{1,9}", values.get(name, ""))}
    if counts.get("CONTROL_PLANE_COUNT", 1) % 2 == 0 or counts.get("CONTROL_PLANE_COUNT", 1) == 0:
        problems.append("CONTROL_PLANE_COUNT: must be odd (1, 3, 5, ...)")
    if counts.get("CLUSTER_NODE_COUNT", 10**9) < counts.get("CONTROL_PLANE_COUNT", 0):
        problems.append("CLUSTER_NODE_COUNT: must be at least CONTROL_PLANE_COUNT")
    for name in ("K3S_CONTROL_PLANE_IPS", "K3S_WORKER_IPS", "K3S_PRIVATE_ADDRESS", "K3S_API_VIP"):
        for part in filter(None, values.get(name, "").split(",")):
            try:
                ipaddress.IPv4Address(part)
            except ValueError:
                problems.append(f"{name}: {part!r} is not an IPv4 address")
    if values.get("K3S_NODE_NETWORK_CIDR"):
        try:
            ipaddress.IPv4Network(values["K3S_NODE_NETWORK_CIDR"])
        except ValueError:
            problems.append("K3S_NODE_NETWORK_CIDR: is not an IPv4 CIDR")
    problems.extend(api_vip_problems(values))
    for left, right in (("K3S_CONTROL_PLANE_HOSTS", "K3S_CONTROL_PLANE_IPS"), ("K3S_WORKER_HOSTS", "K3S_WORKER_IPS")):
        if values.get(left) and values.get(right):
            problems.append(f"{left}/{right}: use only one")
    if values.get("INTERNAL_DNS_ZONE") and values.get("INTERNAL_DNS_ZONE") == values.get("PLATFORM_DOMAIN"):
        problems.append("INTERNAL_DNS_ZONE: must differ from PLATFORM_DOMAIN")
    return problems


def api_vip_problems(values):
    """The API VIP floats between control planes, so it must be a usable host
    address of the node network that no node already holds."""
    try:
        vip = ipaddress.IPv4Address(values.get("K3S_API_VIP", ""))
        network = ipaddress.IPv4Network(values.get("K3S_NODE_NETWORK_CIDR", ""))
    except ValueError:
        return []
    if vip not in network or (network.prefixlen < 31 and vip in (network.network_address, network.broadcast_address)):
        return [f"K3S_API_VIP: must be a host address inside {network}"]
    nodes = {part for name in ("K3S_CONTROL_PLANE_IPS", "K3S_WORKER_IPS", "K3S_PRIVATE_ADDRESS")
             for part in values.get(name, "").split(",") if part}
    return [f"K3S_API_VIP: {vip} is already a node address"] if str(vip) in nodes else []


def parse(text):
    """Parse KEY=VALUE lines as data. Values may be bare, 'single-quoted' or
    "double-quoted" with \\" and \\\\ escapes; nothing is expanded or executed."""
    values, problems = {}, []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not KEY.fullmatch(key):
            problems.append(f"line {number}: expected KEY=VALUE")
            continue
        value = value.strip()
        if value[:1] == "'":
            if len(value) < 2 or not value.endswith("'") or "'" in value[1:-1]:
                problems.append(f"line {number}: unterminated single-quoted value for {key}")
                continue
            value = value[1:-1]
        elif value[:1] == '"':
            match = re.fullmatch(r'"((?:[^"\\]|\\["\\])*)"', value)
            if not match:
                problems.append(f"line {number}: malformed double-quoted value for {key}")
                continue
            value = re.sub(r'\\(["\\])', r"\1", match[1])
        if key in values:
            problems.append(f"line {number}: {key} is set more than once")
        values[key] = value
    return values, problems


def render(values):
    lines = [f"# Swirl Cloud installation inputs; schema version {SCHEMA_VERSION}.",
             "# Contains credentials: keep this file mode 0600 and outside Git.",
             f'{VERSION_KEY}="{SCHEMA_VERSION}"']
    for section in SECTIONS:
        items = [item for item in INPUTS if item.section == section and values.get(item.name, "") != ""]
        if items or any(values.get(name) for name, item in SECRET_FILES.items() if item.section == section):
            lines.append(f"\n# {section}")
            for item in items:
                escaped = values[item.name].replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{item.name}="{escaped}"')
        references = [name for name, item in SECRET_FILES.items() if item.section == section and values.get(name)]
        for name in references:
            escaped = values[name].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{name}="{escaped}"')
    return "\n".join(lines) + "\n"


def merge_secrets(values, text):
    """Merge NAME=value secret lines (for example from stdin); return (values, problems)."""
    extra, problems = parse(text)
    merged = dict(values)
    for name, value in extra.items():
        item = BY_NAME.get(name)
        if not item or not item.secret:
            problems.append(f"{name}: only secret inputs may be supplied on stdin")
        elif values.get(name) or values.get(name + FILE_SUFFIX):
            problems.append(f"{name}: set in both the input file and stdin")
        else:
            merged[name] = value
    return merged, problems


def read(path, *, check_permissions=True, secrets_text=None):
    """Read and validate a file; raise InputError listing every problem."""
    path = Path(path)
    try:
        info = path.stat()
        text = path.read_text()
    except (OSError, UnicodeError):
        raise InputError([f"{path}: cannot be read"]) from None
    problems = []
    if check_permissions:
        if not stat.S_ISREG(info.st_mode):
            problems.append(f"{path}: must be a regular file")
        if info.st_mode & 0o077:
            problems.append(f"{path}: must not be accessible by group or others (chmod 600)")
        if info.st_uid not in (os.getuid(), 0):
            problems.append(f"{path}: must be owned by the installing user")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in text):
        problems.append(f"{path}: contains control characters")
    values, parse_problems = parse(text)
    if secrets_text is not None:
        values, secret_problems = merge_secrets(values, secrets_text)
        parse_problems += secret_problems
    problems += parse_problems + validate(values)
    if problems:
        raise InputError(problems)
    values.pop(VERSION_KEY)
    return values


def write(path, values):
    """Atomically write a 0600 file; the directory is created 0700 when missing."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".install-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(render(values))
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
