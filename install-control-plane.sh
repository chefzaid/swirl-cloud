#!/bin/bash
# Installs the K3s control plane and deploys the platform components.
set -euo pipefail
set +x
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_CONFIG="$SCRIPT_DIR/config/platform.env"
if [[ ! -r "$PLATFORM_CONFIG" ]]; then
    echo "[ERROR] Shared platform contract not found: $PLATFORM_CONFIG" >&2
    exit 1
fi
# shellcheck source=config/platform.env
source "$PLATFORM_CONFIG"
NETWORK_LIBRARY="$SCRIPT_DIR/scripts/lib/network.sh"
if [[ ! -r "$NETWORK_LIBRARY" ]]; then
    echo "[ERROR] Shared network library not found: $NETWORK_LIBRARY" >&2
    exit 1
fi
# shellcheck source=scripts/lib/network.sh
source "$NETWORK_LIBRARY"
PROMPT_LIBRARY="$SCRIPT_DIR/scripts/lib/installer-prompts.sh"
if [[ ! -r "$PROMPT_LIBRARY" ]]; then
    echo "[ERROR] Shared installer prompt library not found: $PROMPT_LIBRARY" >&2
    exit 1
fi
# shellcheck source=scripts/lib/installer-prompts.sh
source "$PROMPT_LIBRARY"
# shellcheck source=scripts/lib/cluster-plan.sh
source "$SCRIPT_DIR/scripts/lib/cluster-plan.sh"
TRANSPORT_GUIDE_LIBRARY="$SCRIPT_DIR/scripts/lib/transport-guide.sh"
if [[ ! -r "$TRANSPORT_GUIDE_LIBRARY" ]]; then
    echo "[ERROR] Shared transport guide not found: $TRANSPORT_GUIDE_LIBRARY" >&2
    exit 1
fi
# shellcheck source=scripts/lib/transport-guide.sh
source "$TRANSPORT_GUIDE_LIBRARY"
SSO_ADMIN_LIBRARY="$SCRIPT_DIR/scripts/lib/sso-admin.sh"
if [[ ! -r "$SSO_ADMIN_LIBRARY" ]]; then
    echo "[ERROR] Shared SSO administrator library not found: $SSO_ADMIN_LIBRARY" >&2
    exit 1
fi
# shellcheck source=scripts/lib/sso-admin.sh
source "$SSO_ADMIN_LIBRARY"

usage() {
    cat <<USAGE
Usage: $0 --yes | --env-file FILE [--secrets-stdin] [--validate-only] | --prompts

The installer engine. Start guided setup with ./install.sh instead.

  --yes             Run unattended from environment variables and defaults.
  --env-file FILE   Run unattended from an installation input file (mode 0600),
                    such as one written by scripts/install-tui.py. Its values
                    replace any installation inputs in the calling environment.
  --secrets-stdin   Read NAME=value lines for secret inputs from stdin and merge
                    them with --env-file FILE, so credentials need not be on disk.
  --validate-only   Report every problem in --env-file FILE, then exit.
  --prompts         Ask the installation questions as plain terminal prompts.
USAGE
}

AUTO_APPROVE=false
INSTALL_ENV_FILE=""
VALIDATE_ONLY=false
SECRETS_STDIN=false
if (( $# == 0 )) && [[ -t 0 && -t 1 ]]; then
    # One interactive entry point: the setup hub collects inputs and runs this engine.
    echo "Starting the setup hub (./install.sh); use --prompts for plain terminal questions." >&2
    exec "$SCRIPT_DIR/install.sh"
fi
while (( $# > 0 )); do
    case "$1" in
        -y|--yes|--auto-approve) AUTO_APPROVE=true ;;
        --env-file=*) INSTALL_ENV_FILE="${1#*=}" ;;
        --env-file)
            (( $# >= 2 )) || { echo "[ERROR] --env-file requires a path" >&2; exit 1; }
            INSTALL_ENV_FILE="$2"
            shift
            ;;
        --validate-only) VALIDATE_ONLY=true ;;
        --secrets-stdin) SECRETS_STDIN=true ;;
        --prompts) ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
    shift
done
if [[ ( "$VALIDATE_ONLY" == true || "$SECRETS_STDIN" == true ) && -z "$INSTALL_ENV_FILE" ]]; then
    echo "[ERROR] --validate-only and --secrets-stdin require --env-file FILE" >&2
    exit 1
fi
if [[ -n "$INSTALL_ENV_FILE" ]]; then
    # shellcheck source=scripts/lib/install-env.sh
    source "$SCRIPT_DIR/scripts/lib/install-env.sh"
    # Import before the defaults below so file values behave like environment inputs.
    install_env_import "$INSTALL_ENV_FILE" "$SECRETS_STDIN" || exit 1
    if [[ "$SECRETS_STDIN" == true ]]; then
        # Stdin is consumed; later sudo and SSH prompts use the terminal when there is one.
        if { : </dev/tty; } 2>/dev/null; then exec </dev/tty; else exec </dev/null; fi
    fi
    AUTO_APPROVE=true
fi

K8S_TEMPLATE_DIR="$SCRIPT_DIR/k8s"
K8S_DIR="$K8S_TEMPLATE_DIR"
ARGOCD_VALUES_FILE="$SCRIPT_DIR/config/argocd-values.yaml"
RENDER_CONFIG_SCRIPT="$SCRIPT_DIR/scripts/render-cluster-config.sh"
VAULT_VALUES_FILE="$SCRIPT_DIR/config/vault-values.yaml"
INGRESS_VALUES_FILE="$SCRIPT_DIR/config/traefik-values.yaml"
VAULT_BOOTSTRAP_SCRIPT="$SCRIPT_DIR/scripts/configure-vault.sh"
SECURITY_HARDEN_SCRIPT="$SCRIPT_DIR/scripts/configure-node-security.sh"
CLOUDFLARE_SCRIPT="$SCRIPT_DIR/scripts/configure-cloudflare.sh"
NODE_ENROLLMENT_SCRIPT="$SCRIPT_DIR/add-node.sh"
K3S_HA_SCRIPT="$SCRIPT_DIR/scripts/configure-k3s-ha.sh"
K3S_BACKUP_SCRIPT="$SCRIPT_DIR/scripts/configure-k3s-backups.sh"
GITLAB_CI_SCRIPT="$SCRIPT_DIR/scripts/configure-gitlab-ci.sh"
PLATFORM_RELEASE_SCRIPT="$SCRIPT_DIR/scripts/reconcile-platform-release.sh"
REPLICATE_REPO_SCRIPT="$SCRIPT_DIR/add-repos.sh"
GITLAB_TOKEN_LIBRARY="$SCRIPT_DIR/scripts/lib/gitlab-admin-token.sh"
LOCAL_ADMIN_PASSWORD_ROTATION_SCRIPT="$SCRIPT_DIR/scripts/rotate-local-admin-passwords.sh"
K3S_REGISTRY_MIRROR_SCRIPT="$SCRIPT_DIR/scripts/configure-k3s-registry-mirror.sh"
K3S_APPARMOR_SCRIPT="$SCRIPT_DIR/scripts/configure-k3s-apparmor.sh"
LONGHORN_HOST_SCRIPT="$SCRIPT_DIR/scripts/configure-longhorn-host.sh"
CLUSTER_TOPOLOGY_SCRIPT="$SCRIPT_DIR/scripts/reconcile-cluster-topology.sh"
K3S_NETWORK_SCRIPT="$SCRIPT_DIR/scripts/configure-k3s-control-plane-network.sh"
TAILSCALE_SCRIPT="$SCRIPT_DIR/scripts/configure-tailscale.sh"
OVH_VRACK_SCRIPT="$SCRIPT_DIR/scripts/configure-ovh-vrack.sh"
INSTALL_SCOPE="${INSTALL_SCOPE:-apps}"
SERVER_EXPOSURE="${SERVER_EXPOSURE:-internet}"
K3S_NODE_TRANSPORT="${K3S_NODE_TRANSPORT:-}"
TAILSCALE_API_TOKEN="${TAILSCALE_API_TOKEN:-}"
TAILSCALE_TAILNET="${TAILSCALE_TAILNET:-$DEFAULT_TAILSCALE_TAILNET}"
TAILSCALE_MESH_NAME="${TAILSCALE_MESH_NAME:-$DEFAULT_TAILSCALE_MESH_NAME}"
TAILSCALE_NODE_HOSTNAME="${TAILSCALE_NODE_HOSTNAME:-}"
TAILSCALE_AUTH_KEY_EXPIRY_SECONDS="${TAILSCALE_AUTH_KEY_EXPIRY_SECONDS:-$DEFAULT_TAILSCALE_AUTH_KEY_EXPIRY_SECONDS}"
OVH_VRACK_AUTOMATE_ACCOUNT="${OVH_VRACK_AUTOMATE_ACCOUNT:-}"
OVH_API_ENDPOINT="${OVH_API_ENDPOINT:-ovh-eu}"
OVH_APPLICATION_KEY="${OVH_APPLICATION_KEY:-}"
OVH_APPLICATION_SECRET="${OVH_APPLICATION_SECRET:-}"
OVH_CONSUMER_KEY="${OVH_CONSUMER_KEY:-}"
OVH_VRACK_SERVICE_NAME="${OVH_VRACK_SERVICE_NAME:-}"
OVH_CONTROL_PLANE_SERVICE_NAME="${OVH_CONTROL_PLANE_SERVICE_NAME:-}"
KEYCLOAK_SSO_BOOTSTRAP_USERNAME="${KEYCLOAK_SSO_BOOTSTRAP_USERNAME:-}"
KEYCLOAK_SSO_BOOTSTRAP_PASSWORD="${KEYCLOAK_SSO_BOOTSTRAP_PASSWORD:-}"
ROTATE_LOCAL_ADMIN_PASSWORDS="${ROTATE_LOCAL_ADMIN_PASSWORDS:-}"
LOCAL_ADMIN_PASSWORD="${LOCAL_ADMIN_PASSWORD:-}"
CONFIGURE_OFFSITE_BACKUPS="${CONFIGURE_OFFSITE_BACKUPS:-}"
SERVER_MONTHLY_COST="${SERVER_MONTHLY_COST:-}"
COST_CURRENCY="${COST_CURRENCY:-EUR}"
BACKUP_S3_ENDPOINT="${BACKUP_S3_ENDPOINT:-}"
BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-}"
BACKUP_S3_REGION="${BACKUP_S3_REGION:-}"
BACKUP_S3_ACCESS_KEY="${BACKUP_S3_ACCESS_KEY:-}"
BACKUP_S3_SECRET_KEY="${BACKUP_S3_SECRET_KEY:-}"
BACKUP_REPOSITORY_PASSWORD="${BACKUP_REPOSITORY_PASSWORD:-}"
CONFIGURE_PLATFORM_MAIL="${CONFIGURE_PLATFORM_MAIL:-}"
PLATFORM_MAIL_HOST="${PLATFORM_MAIL_HOST:-}"
PLATFORM_MAIL_PORT="${PLATFORM_MAIL_PORT:-}"
PLATFORM_MAIL_TLS="${PLATFORM_MAIL_TLS:-ssl}"
PLATFORM_MAIL_USERNAME="${PLATFORM_MAIL_USERNAME:-}"
PLATFORM_MAIL_PASSWORD="${PLATFORM_MAIL_PASSWORD:-}"
PLATFORM_MAIL_FROM="${PLATFORM_MAIL_FROM:-}"
PLATFORM_MAIL_FROM_NAME="${PLATFORM_MAIL_FROM_NAME:-}"
PLATFORM_MAIL_ALERT_TO="${PLATFORM_MAIL_ALERT_TO:-}"
PLATFORM_DOMAIN="${PLATFORM_DOMAIN:-${DEFAULT_PLATFORM_DOMAIN:-}}"
INTERNAL_DNS_ZONE="${INTERNAL_DNS_ZONE:-}"
CONTROL_PLANE_NODE_NAME="${CONTROL_PLANE_NODE_NAME:-}"
CONFIGURE_REPOSITORY_SYNC="${CONFIGURE_REPOSITORY_SYNC:-}"
REPOSITORY_SOURCE="${REPOSITORY_SOURCE:-github}"
GITLAB_PROJECTS="${GITLAB_PROJECTS:-}"
DEPLOYMENT_ENVIRONMENTS_FILE="${DEPLOYMENT_ENVIRONMENTS_FILE:-}"
GITLAB_GROUP_PATH="${GITLAB_GROUP_PATH:-}"
GITLAB_GROUP_NAME="${GITLAB_GROUP_NAME:-}"
GITLAB_PROJECT_PATH="${GITLAB_PROJECT_PATH:-}"
GITOPS_REPOSITORY_URL="${GITOPS_REPOSITORY_URL:-}"
RENDERED_CONFIG_DIR=""
INSTALLER_TEMP_DIR=""
INSTALL_DEVELOPER_TOOLS="${INSTALL_DEVELOPER_TOOLS:-}"
k3s_installer=""
helm_archive=""
K3S_INSTALL_VERSION="${K3S_INSTALL_VERSION:-$DEFAULT_K3S_INSTALL_VERSION}"
K3S_REGISTRY_HOST="${K3S_REGISTRY_HOST:-${DEFAULT_K3S_REGISTRY_HOST:-}}"
K3S_REGISTRY_ENDPOINT="${K3S_REGISTRY_ENDPOINT:-$DEFAULT_K3S_REGISTRY_ENDPOINT}"
TRAEFIK_CHART_VERSION="${TRAEFIK_CHART_VERSION:-$DEFAULT_TRAEFIK_CHART_VERSION}"
LONGHORN_CHART_VERSION="${LONGHORN_CHART_VERSION:-$DEFAULT_LONGHORN_CHART_VERSION}"
VAULT_CHART_VERSION="${VAULT_CHART_VERSION:-$DEFAULT_VAULT_CHART_VERSION}"
EXTERNAL_SECRETS_CHART_VERSION="${EXTERNAL_SECRETS_CHART_VERSION:-$DEFAULT_EXTERNAL_SECRETS_CHART_VERSION}"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-$DEFAULT_ARGOCD_CHART_VERSION}"
ARGOCD_IMAGE_TAG="${ARGOCD_IMAGE_TAG:-$DEFAULT_ARGOCD_IMAGE_TAG}"
LONGHORN_HELM_TIMEOUT="${LONGHORN_HELM_TIMEOUT:-$DEFAULT_LONGHORN_HELM_TIMEOUT}"
LONGHORN_POD_WAIT_TIMEOUT="${LONGHORN_POD_WAIT_TIMEOUT:-$DEFAULT_LONGHORN_POD_WAIT_TIMEOUT}"
INGRESS_HELM_TIMEOUT="${INGRESS_HELM_TIMEOUT:-$DEFAULT_INGRESS_HELM_TIMEOUT}"
VAULT_WAIT_TIMEOUT="${VAULT_WAIT_TIMEOUT:-$DEFAULT_VAULT_WAIT_TIMEOUT}"
EXTERNAL_SECRETS_HELM_TIMEOUT="${EXTERNAL_SECRETS_HELM_TIMEOUT:-$DEFAULT_EXTERNAL_SECRETS_HELM_TIMEOUT}"
EXTERNAL_SECRET_WAIT_TIMEOUT="${EXTERNAL_SECRET_WAIT_TIMEOUT:-$DEFAULT_EXTERNAL_SECRET_WAIT_TIMEOUT}"
ARGOCD_HELM_TIMEOUT="${ARGOCD_HELM_TIMEOUT:-$DEFAULT_ARGOCD_HELM_TIMEOUT}"
DATASTORE_WAIT_TIMEOUT="${DATASTORE_WAIT_TIMEOUT:-$DEFAULT_DATASTORE_WAIT_TIMEOUT}"
PLATFORM_WAIT_TIMEOUT="${PLATFORM_WAIT_TIMEOUT:-$DEFAULT_PLATFORM_WAIT_TIMEOUT}"
CLOUDFLARE_ZONE="${CLOUDFLARE_ZONE:-${DEFAULT_CLOUDFLARE_ZONE:-}}"
CLOUDFLARE_NODE_DNS_LABEL="${CLOUDFLARE_NODE_DNS_LABEL:-$DEFAULT_CLOUDFLARE_NODE_DNS_LABEL}"
CLOUDFLARE_ACCESS_TEAM_NAME="${CLOUDFLARE_ACCESS_TEAM_NAME:-${DEFAULT_CLOUDFLARE_ACCESS_TEAM_NAME:-}}"
CLUSTER_NODE_COUNT="${CLUSTER_NODE_COUNT:-}"
CONTROL_PLANE_COUNT="${CONTROL_PLANE_COUNT:-}"
CONTROL_PLANE_SCHEDULABLE="${CONTROL_PLANE_SCHEDULABLE:-}"
HIGH_AVAILABILITY_ENABLED="${HIGH_AVAILABILITY_ENABLED:-}"
export HIGH_AVAILABILITY_ENABLED
PLANNED_WORKER_COUNT=0
WORKERS_TO_ADD=0
CONTROL_PLANES_TO_ADD=0

IFS=',' read -r -a FOUNDATION_MANIFEST_ARRAY <<< "$FOUNDATION_MANIFESTS"
IFS=',' read -r -a DATASTORE_MANIFEST_ARRAY <<< "$DATASTORE_MANIFESTS"
IFS=',' read -r -a PLATFORM_MANIFEST_ARRAY <<< "$PLATFORM_MANIFESTS"
IFS=',' read -r -a POST_ARGOCD_MANIFEST_ARRAY <<< "$POST_ARGOCD_MANIFESTS"
IFS=',' read -r -a EXTERNAL_SECRET_NAME_ARRAY <<< "$EXTERNAL_SECRET_NAMES"
IFS=',' read -r -a HOST_AGENTS_EXTERNAL_SECRET_NAME_ARRAY <<< "$HOST_AGENTS_EXTERNAL_SECRET_NAMES"
IFS=',' read -r -a DATASTORE_WAIT_APP_ARRAY <<< "$DATASTORE_WAIT_APPS"
IFS=',' read -r -a PLATFORM_WAIT_APP_ARRAY <<< "$PLATFORM_WAIT_APPS"
IFS=',' read -r -a PLATFORM_WAIT_DAEMONSET_ARRAY <<< "$PLATFORM_WAIT_DAEMONSETS"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
fail()  { error "$@"; }
step()  { echo -e "${BLUE}[STEP]${NC}  $*"; install_record_step "$@"; }

ask_with_default() {
    installer_prompt_yes_no "$1" "${2:-N}" "$AUTO_APPROVE"
}

# decide VARIABLE QUESTION DEFAULT: a preset true/false value skips the question;
# otherwise ask (or take DEFAULT when unattended).
decide() {
    local name="$1" question="$2" default_choice="$3" value="${!1:-}"
    if [[ -n "$value" ]]; then
        case "${value,,}" in
            1|true|yes|y) printf -v "$name" true ;;
            0|false|no|n) printf -v "$name" false ;;
            *) error "$name must be true or false." ;;
        esac
        return
    fi
    if ask_with_default "$question" "$default_choice"; then
        printf -v "$name" true
    else
        printf -v "$name" false
    fi
}

# shellcheck source=scripts/lib/platform-identity.sh
source "$SCRIPT_DIR/scripts/lib/platform-identity.sh"

# shellcheck source=scripts/lib/installer-settings.sh
source "$SCRIPT_DIR/scripts/lib/installer-settings.sh"

# shellcheck source=scripts/lib/installer-selection.sh
source "$SCRIPT_DIR/scripts/lib/installer-selection.sh"

# shellcheck source=scripts/lib/install-record.sh
source "$SCRIPT_DIR/scripts/lib/install-record.sh"

cleanup_private_credentials() {
    TAILSCALE_API_TOKEN=""
    OVH_APPLICATION_KEY=""
    OVH_APPLICATION_SECRET=""
    OVH_CONSUMER_KEY=""
    KEYCLOAK_SSO_BOOTSTRAP_PASSWORD=""
    LOCAL_ADMIN_PASSWORD=""
    GITHUB_ADMIN_TOKEN=""
    CLOUDFLARE_API_TOKEN=""
    BACKUP_S3_ACCESS_KEY=""
    BACKUP_S3_SECRET_KEY=""
    BACKUP_REPOSITORY_PASSWORD=""
    PLATFORM_MAIL_PASSWORD=""
    unset TAILSCALE_API_TOKEN OVH_APPLICATION_KEY OVH_APPLICATION_SECRET OVH_CONSUMER_KEY 2>/dev/null || true
    unset KEYCLOAK_SSO_BOOTSTRAP_USERNAME KEYCLOAK_SSO_BOOTSTRAP_PASSWORD 2>/dev/null || true
    unset LOCAL_ADMIN_PASSWORD LOCAL_ADMIN_PASSWORD_ERROR 2>/dev/null || true
    unset GITHUB_ADMIN_TOKEN 2>/dev/null || true
    unset CLOUDFLARE_API_TOKEN 2>/dev/null || true
    unset BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY BACKUP_REPOSITORY_PASSWORD 2>/dev/null || true
    unset PLATFORM_MAIL_PASSWORD 2>/dev/null || true
    if declare -F gitlab_revoke_ephemeral_admin_token >/dev/null 2>&1; then
        gitlab_revoke_ephemeral_admin_token
    fi
    if [[ -n "$INSTALLER_TEMP_DIR" && "$INSTALLER_TEMP_DIR" == /tmp/swirl-cloud-installers.* && -d "$INSTALLER_TEMP_DIR" ]]; then
        rm -r -- "$INSTALLER_TEMP_DIR"
    fi
    if [[ -n "$RENDERED_CONFIG_DIR" && "$RENDERED_CONFIG_DIR" == /tmp/swirl-cloud-rendered.* && -d "$RENDERED_CONFIG_DIR" ]]; then
        rm -r -- "$RENDERED_CONFIG_DIR"
    fi
}
# Credentials are cleared first, so a recording problem can never skip it.
finish_installer() {
    local status=$?
    cleanup_private_credentials
    install_record_finish "$status"
}
trap finish_installer EXIT
trap cleanup_private_credentials HUP INT TERM

download_installer() {
    local url="$1" filename="$2" destination_variable="$3" destination
    [[ "$url" == https://* ]] || error "Installer URL must use HTTPS: $url"
    [[ "$filename" =~ ^[A-Za-z0-9._-]+$ ]] || error "Invalid installer filename: $filename"
    if [[ -z "$INSTALLER_TEMP_DIR" ]]; then
        INSTALLER_TEMP_DIR="$(mktemp -d /tmp/swirl-cloud-installers.XXXXXX)"
        chmod 700 "$INSTALLER_TEMP_DIR"
    fi
    destination="$INSTALLER_TEMP_DIR/$filename"
    curl --fail --location --silent --show-error \
        --proto '=https' --tlsv1.2 --retry 3 --retry-all-errors \
        --connect-timeout 15 --max-time 180 \
        --output "$destination" "$url"
    chmod 700 "$destination"
    printf -v "$destination_variable" '%s' "$destination"
}

# Install the pinned Helm release only after its archive matches the reviewed
# checksum; the mutable upstream get-helm script is never executed.
install_pinned_helm() {
    local arch expected_sha256
    case "$(uname -m)" in
        x86_64|amd64) arch=amd64; expected_sha256="${DEFAULT_HELM_LINUX_AMD64_SHA256:-}" ;;
        aarch64|arm64) arch=arm64; expected_sha256="${DEFAULT_HELM_LINUX_ARM64_SHA256:-}" ;;
        *) error "No pinned Helm release for architecture $(uname -m); install Helm manually." ;;
    esac
    [[ "${DEFAULT_HELM_VERSION:-}" =~ ^v3\.[0-9]+\.[0-9]+$ ]] || error "DEFAULT_HELM_VERSION must be a Helm 3 release such as v3.22.0."
    [[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]] || error "The pinned Helm $arch checksum is missing or invalid."
    download_installer "https://get.helm.sh/helm-${DEFAULT_HELM_VERSION}-linux-${arch}.tar.gz" helm.tar.gz helm_archive
    printf '%s  %s\n' "$expected_sha256" "$helm_archive" | sha256sum --check --status || \
        error "Helm ${DEFAULT_HELM_VERSION} archive checksum mismatch; refusing to install it."
    tar -xzf "$helm_archive" -C "$INSTALLER_TEMP_DIR" "linux-$arch/helm"
    sudo install -o root -g root -m 0755 "$INSTALLER_TEMP_DIR/linux-$arch/helm" /usr/local/bin/helm
}

# shellcheck source=scripts/lib/tls.sh
source "$SCRIPT_DIR/scripts/lib/tls.sh"

# ---------- Installation input file ----------------------------------------------
if [[ -n "$INSTALL_ENV_FILE" ]]; then
    # Report schema and credential-policy problems together, before any change.
    if [[ -n "${KEYCLOAK_SSO_BOOTSTRAP_USERNAME:-}" ]] && ! sso_admin_validate_username "$KEYCLOAK_SSO_BOOTSTRAP_USERNAME"; then
        INSTALL_ENV_PROBLEMS+=("KEYCLOAK_SSO_BOOTSTRAP_USERNAME: $SSO_ADMIN_VALIDATION_ERROR")
    fi
    if [[ -n "${KEYCLOAK_SSO_BOOTSTRAP_PASSWORD:-}" ]] && \
        ! sso_admin_validate_password "$KEYCLOAK_SSO_BOOTSTRAP_PASSWORD" "${KEYCLOAK_SSO_BOOTSTRAP_USERNAME:-}"; then
        INSTALL_ENV_PROBLEMS+=("KEYCLOAK_SSO_BOOTSTRAP_PASSWORD: $SSO_ADMIN_VALIDATION_ERROR")
    fi
    if [[ -n "${LOCAL_ADMIN_PASSWORD:-}" ]] && ! validate_local_admin_password; then
        INSTALL_ENV_PROBLEMS+=("LOCAL_ADMIN_PASSWORD: $LOCAL_ADMIN_PASSWORD_ERROR")
    fi
    if (( ${#INSTALL_ENV_PROBLEMS[@]} > 0 )); then
        printf '%b[ERROR]%b %s has %d problem(s):\n' "$RED" "$NC" "$INSTALL_ENV_FILE" "${#INSTALL_ENV_PROBLEMS[@]}" >&2
        printf '  - %s\n' "${INSTALL_ENV_PROBLEMS[@]}" >&2
        exit 1
    fi
    info "Installation inputs in $INSTALL_ENV_FILE are valid."
    [[ "$VALIDATE_ONLY" != true ]] || exit 0
fi

# ---------- Combined pre-flight checks -----------------------------------------
[[ $EUID -eq 0 ]] && error "Do not run as root. The script uses sudo when needed."
if systemctl cat k3s-agent.service >/dev/null 2>&1; then
    error "This host is a K3s worker. Run this installer on the bootstrap control-plane host."
fi

# ---------- Installation record --------------------------------------------------
# Runs that stop before this point have changed nothing on the host.
if [[ -n "$INSTALL_ENV_FILE" ]]; then install_record_start env-file
elif [[ "$AUTO_APPROVE" == true ]]; then install_record_start yes
else install_record_start prompts
fi

info "============================================="
info " Control Plane Installer"
info "============================================="
echo ""
echo "This script will:"
echo "  - Group related questions and offer a recommended platform bundle"
echo "  - Provision the login you choose as administrator through Keycloak SSO"
echo "  - Install only selected components"
echo "  - Apply UFW everywhere and add intrusion prevention only when internet-exposed"
echo ""

ask_with_default "Proceed with installation and deployment?" "Y" || { info "Aborted."; exit 0; }

installer_prompt_section "Cluster identity and deployment scope" \
    "Choose where this cluster runs and whether it includes application workloads."
prompt_cluster_identity
installer_prompt_section "GitOps and repository delivery" \
    "Choose the Argo CD source and optionally configure GitHub/GitLab synchronization."
configure_repository_delivery

enrolled_exposure=""
if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
    enrolled_exposure="$(kubectl get node "$CONTROL_PLANE_NODE_NAME" --ignore-not-found \
        -o jsonpath='{.metadata.labels.node\.swirl-cloud\.io/exposure}')"
fi
if [[ "$enrolled_exposure" == local ]]; then
    SERVER_EXPOSURE=local
    info "Preserving this enrolled control plane's private exposure policy."
else
    SERVER_EXPOSURE="$(ask_server_exposure "$SERVER_EXPOSURE")"
fi
if [[ "$AUTO_APPROVE" == true ]]; then
    CONFIGURE_CLOUDFLARE="${CONFIGURE_CLOUDFLARE:-$([[ "$SERVER_EXPOSURE" == internet ]] && printf true || printf false)}"
    [[ "$CONFIGURE_CLOUDFLARE" =~ ^(true|false)$ ]] || error "CONFIGURE_CLOUDFLARE must be true or false."
fi
if [[ "$SERVER_EXPOSURE" == "internet" ]]; then
    info "Internet-exposed mode selected: enabling UFW, Fail2ban, CrowdSec, and control-plane Lynis."
else
    info "Local-only mode selected: enabling UFW and control-plane Lynis; skipping Fail2ban and CrowdSec."
fi
INSTALL_SCOPE="$(select_install_scope "$INSTALL_SCOPE")"
if [[ "$INSTALL_SCOPE" == "apps" ]]; then
    INSTALL_APPS=true
    DEPLOY_ODOO=true
    info "Infrastructure and apps selected: deploying Odoo."
else
    INSTALL_APPS=false
    DEPLOY_ODOO=false
    info "Infrastructure-only installation selected."
fi

installer_prompt_section "Cluster nodes and scheduling" \
    "Choose an odd control-plane count, total nodes, and scheduling for all control planes."
configure_cluster_topology_plan
POSTGRES_HA_ACTIVE=false
if command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
    INSTALLER_TEMP_DIR="$(mktemp -d /tmp/swirl-cloud-installers.XXXXXX)"
    HIGH_AVAILABILITY_ENABLED="$(python3 "$SCRIPT_DIR/scripts/resolve-ha-profile.py" --output "$INSTALLER_TEMP_DIR/ha-values.json")"
    if [[ -f "$INSTALLER_TEMP_DIR/ha-values.json" ]]; then
        export PLATFORM_HA_VALUES_FILE="$INSTALLER_TEMP_DIR/ha-values.json"
        POSTGRES_HA_ACTIVE="$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1])).get("postgresHa", {}).get("active", False)).lower())' "$PLATFORM_HA_VALUES_FILE")"
    fi
elif [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
    error "Install and enroll the nodes first, then activate HA using docs/guides/high-availability.md."
fi
HIGH_AVAILABILITY_ENABLED="${HIGH_AVAILABILITY_ENABLED:-false}"
[[ "$HIGH_AVAILABILITY_ENABLED" =~ ^(true|false)$ ]] || error "HIGH_AVAILABILITY_ENABLED must be true or false."
if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
    (( CONTROL_PLANE_COUNT >= 3 )) || error "HA requires at least three planned control planes. Add servers before enabling it."
    if [[ "$CONTROL_PLANE_SCHEDULABLE" != true ]]; then
        (( PLANNED_WORKER_COUNT >= 3 )) || error "HA needs three workers when control planes are unschedulable."
    fi
fi
if [[ "$AUTO_APPROVE" == true ]]; then
    if (( CONTROL_PLANES_TO_ADD > 0 )) && [[ -z "${K3S_CONTROL_PLANE_HOSTS:-}${K3S_CONTROL_PLANE_IPS:-}" ]]; then
        error "Provide K3S_CONTROL_PLANE_HOSTS or K3S_CONTROL_PLANE_IPS for non-interactive server enrollment."
    fi
    if (( WORKERS_TO_ADD > 0 )) && [[ -z "${K3S_WORKER_HOSTS:-}${K3S_WORKER_IPS:-}" ]]; then
        error "Provide K3S_WORKER_HOSTS or K3S_WORKER_IPS for non-interactive worker enrollment."
    fi
fi

installer_prompt_section "Host prerequisites and K3s nodes" \
    "Choose host preparation and K3s installation; additional servers and workers join over SSH."
if command -v k3s &>/dev/null; then
    warn "K3s detected: $(k3s --version | head -1)"
    K3S_DEFAULT="N"
else
    K3S_DEFAULT="Y"
fi

decide INSTALL_PREREQS "Install/upgrade system prerequisites (Ansible, storage clients, validation tools)?" "Y"
if [[ "$INSTALL_PREREQS" == true && -z "$INSTALL_DEVELOPER_TOOLS" ]]; then
    ask_with_default "Also install developer tools (Java 21, Maven, Docker)?" "N" && \
        INSTALL_DEVELOPER_TOOLS=true || INSTALL_DEVELOPER_TOOLS=false
fi
INSTALL_DEVELOPER_TOOLS="${INSTALL_DEVELOPER_TOOLS:-false}"
[[ "$INSTALL_DEVELOPER_TOOLS" =~ ^(true|false)$ ]] || error "INSTALL_DEVELOPER_TOOLS must be true or false."
decide INSTALL_K3S "Install or reinstall K3s control plane?" "$K3S_DEFAULT"
if (( WORKERS_TO_ADD > 0 )); then
    ADD_K3S_WORKERS=true
    info "$WORKERS_TO_ADD worker node(s) will be added or reconciled over SSH."
else
    ADD_K3S_WORKERS=false
    info "The requested worker count is already present; no worker enrollment is needed."
fi
ADD_K3S_CONTROL_PLANES=false
if (( CONTROL_PLANES_TO_ADD > 0 )); then
    ADD_K3S_CONTROL_PLANES=true
    info "$CONTROL_PLANES_TO_ADD additional control-plane node(s) will be added or reconciled over SSH."
fi
ENROLL_K3S_NODES=false
if [[ "$ADD_K3S_WORKERS" == "true" || "$ADD_K3S_CONTROL_PLANES" == "true" ]]; then
    ENROLL_K3S_NODES=true
    K3S_NODE_TRANSPORT="$(select_node_transport)"
    export K3S_NODE_TRANSPORT
    if [[ "$K3S_NODE_TRANSPORT" == "tailscale" ]]; then
        [[ -x "$TAILSCALE_SCRIPT" ]] || error "Tailscale configurator not found or not executable: $TAILSCALE_SCRIPT"
        transport_guide_tailscale_account "$AUTO_APPROVE" "$TAILSCALE_SCRIPT" || \
            error "Tailscale prerequisites are incomplete or account verification failed."
        step "Reconciling the tailnet policy and control-plane role..."
        K3S_PRIVATE_ADDRESS="$(printf '%s\n' "$TAILSCALE_API_TOKEN" | \
            "$TAILSCALE_SCRIPT" --role control-plane \
                --tailnet "$TAILSCALE_TAILNET" \
                --mesh-name "$TAILSCALE_MESH_NAME" \
                --hostname "$TAILSCALE_NODE_HOSTNAME" \
                --auth-key-expiry "$TAILSCALE_AUTH_KEY_EXPIRY_SECONDS" \
                --api-token-stdin)"
        K3S_PRIVATE_INTERFACE=tailscale0
        K3S_NODE_NETWORK_CIDR=100.64.0.0/10
        export K3S_PRIVATE_ADDRESS K3S_PRIVATE_INTERFACE K3S_NODE_NETWORK_CIDR
        TAILSCALE_CONFIG_PREPARED=true
        export TAILSCALE_TAILNET TAILSCALE_MESH_NAME TAILSCALE_NODE_HOSTNAME TAILSCALE_AUTH_KEY_EXPIRY_SECONDS TAILSCALE_CONFIG_PREPARED
    elif [[ "$K3S_NODE_TRANSPORT" == "lan" ]]; then
        transport_guide_lan "$AUTO_APPROVE" || error "Local LAN prerequisites are incomplete."
        if [[ -z "${K3S_PRIVATE_ADDRESS:-}" ]]; then
            lan_default_address="$(ip -4 route get 1.1.1.1 2>/dev/null | \
                awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}')"
            default_route_is_private || lan_default_address=""
            if [[ "$AUTO_APPROVE" == "true" ]]; then
                K3S_PRIVATE_ADDRESS="$lan_default_address"
            else
                installer_prompt_value K3S_PRIVATE_ADDRESS "Control-plane LAN RFC1918 IPv4" "$lan_default_address"
            fi
        fi
        if ! trusted_private_ipv4 "${K3S_PRIVATE_ADDRESS:-}" || tailscale_ipv4 "$K3S_PRIVATE_ADDRESS"; then
            error "LAN transport requires this control plane's RFC1918 IPv4 address (K3S_PRIVATE_ADDRESS)."
        fi
        K3S_PRIVATE_INTERFACE="$(interface_owning_ip "$K3S_PRIVATE_ADDRESS")"
        [[ -n "$K3S_PRIVATE_INTERFACE" ]] || \
            error "$K3S_PRIVATE_ADDRESS is not configured on this host. Assign the fixed LAN address first; LAN transport does not change host networking."
        [[ ! "$K3S_PRIVATE_INTERFACE" =~ ^(lo|docker|br-|cni|flannel|veth|tailscale) ]] || \
            error "$K3S_PRIVATE_ADDRESS belongs to unsupported virtual interface $K3S_PRIVATE_INTERFACE."
        if [[ -z "${K3S_NODE_NETWORK_CIDR:-}" ]]; then
            [[ "$AUTO_APPROVE" != "true" ]] || error "K3S_NODE_NETWORK_CIDR is required with LAN nodes."
            installer_prompt_value K3S_NODE_NETWORK_CIDR \
                "Trusted LAN node CIDR (every address in it reaches cluster ports)" \
                "$(connected_ipv4_cidr "$K3S_PRIVATE_ADDRESS" || true)"
        fi
        trusted_private_cidr "${K3S_NODE_NETWORK_CIDR:-}" && [[ "$K3S_NODE_NETWORK_CIDR" != "100.64.0.0/10" ]] || \
            error "LAN transport requires an RFC1918 node CIDR: ${K3S_NODE_NETWORK_CIDR:-none}"
        cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$K3S_PRIVATE_ADDRESS" || \
            error "$K3S_PRIVATE_ADDRESS is outside $K3S_NODE_NETWORK_CIDR."
        info "Using LAN address $K3S_PRIVATE_ADDRESS on $K3S_PRIVATE_INTERFACE for cluster traffic."
        export K3S_PRIVATE_ADDRESS K3S_PRIVATE_INTERFACE K3S_NODE_NETWORK_CIDR
    else
        [[ -x "$OVH_VRACK_SCRIPT" ]] || error "OVHcloud vRack configurator not found or not executable: $OVH_VRACK_SCRIPT"
        transport_guide_vrack_account "$AUTO_APPROVE" "$OVH_VRACK_SCRIPT" || \
            error "OVHcloud vRack prerequisites are incomplete or account verification failed."
        if [[ "$OVH_VRACK_AUTOMATE_ACCOUNT" == "true" ]]; then
            if [[ "$AUTO_APPROVE" != "true" ]]; then
                installer_prompt_value OVH_CONTROL_PLANE_SERVICE_NAME \
                    "This control plane's OVHcloud Dedicated Server service name" \
                    "$OVH_CONTROL_PLANE_SERVICE_NAME"
            fi
            [[ -n "$OVH_VRACK_SERVICE_NAME" && -n "$OVH_CONTROL_PLANE_SERVICE_NAME" ]] || \
                error "OVH_VRACK_SERVICE_NAME and OVH_CONTROL_PLANE_SERVICE_NAME are required for API attachment."
            export OVH_API_ENDPOINT OVH_APPLICATION_KEY OVH_APPLICATION_SECRET OVH_CONSUMER_KEY OVH_VRACK_SERVICE_NAME
            step "Attaching the control-plane private interface to OVHcloud vRack $OVH_VRACK_SERVICE_NAME..."
            detected_vrack_mac="$("$OVH_VRACK_SCRIPT" --attach-server "$OVH_CONTROL_PLANE_SERVICE_NAME" \
                --vrack "$OVH_VRACK_SERVICE_NAME" --ovh-endpoint "$OVH_API_ENDPOINT" --non-interactive)"
            if [[ "${detected_vrack_mac,,}" =~ ^([0-9a-f]{2}:){5}[0-9a-f]{2}$ && -z "${K3S_PRIVATE_INTERFACE_MAC:-}" ]]; then
                K3S_PRIVATE_INTERFACE_MAC="$detected_vrack_mac"
            fi
        else
            warn "OVHcloud account attachment is manual: the existing vRack and every server must be attached in Control Panel before host configuration."
        fi
        OVH_VRACK_CONFIG_PREPARED=true
        export OVH_VRACK_AUTOMATE_ACCOUNT OVH_VRACK_CONFIG_PREPARED
        if [[ -z "${K3S_PRIVATE_ADDRESS:-}" ]]; then
            if [[ "$AUTO_APPROVE" == "true" ]]; then
                error "K3S_PRIVATE_ADDRESS is required with vRack nodes."
            fi
            installer_prompt_value K3S_PRIVATE_ADDRESS "Control-plane vRack/private RFC1918 IPv4"
            [[ -n "$K3S_PRIVATE_ADDRESS" ]] || error "A control-plane vRack/private IPv4 address is required."
            export K3S_PRIVATE_ADDRESS
        fi
        if ! trusted_private_ipv4 "$K3S_PRIVATE_ADDRESS" || tailscale_ipv4 "$K3S_PRIVATE_ADDRESS"; then
            error "vRack transport requires this control plane's RFC1918 IPv4 address."
        fi
        if [[ -z "${K3S_NODE_NETWORK_CIDR:-}" ]]; then
            if [[ "$AUTO_APPROVE" == "true" ]]; then
                error "K3S_NODE_NETWORK_CIDR is required with vRack nodes."
            fi
            installer_prompt_value K3S_NODE_NETWORK_CIDR \
                "Trusted vRack/private node CIDR (for example 10.50.0.0/24)"
            [[ -n "$K3S_NODE_NETWORK_CIDR" ]] || error "A trusted vRack/private node CIDR is required."
            export K3S_NODE_NETWORK_CIDR
        fi
        existing_vrack_interface="$(interface_owning_ip "$K3S_PRIVATE_ADDRESS")"
        if [[ -z "$existing_vrack_interface" && "$AUTO_APPROVE" != "true" ]]; then
            ip -br link show
            installer_prompt_value K3S_PRIVATE_INTERFACE "Control-plane OVHcloud private/vRack NIC name"
            installer_prompt_value K3S_PRIVATE_INTERFACE_MAC \
                "Expected private NIC MAC (recommended)" "${K3S_PRIVATE_INTERFACE_MAC:-}"
            installer_prompt_value K3S_VRACK_VLAN_ID \
                "Optional vRack VLAN ID (blank for untagged VLAN 0)"
        fi
        vrack_node_args=(
            --configure-node
            --private-ip "$K3S_PRIVATE_ADDRESS"
            --network-cidr "$K3S_NODE_NETWORK_CIDR"
        )
        [[ -z "${K3S_PRIVATE_INTERFACE:-}" ]] || vrack_node_args+=(--interface "$K3S_PRIVATE_INTERFACE")
        [[ -z "${K3S_PRIVATE_INTERFACE_MAC:-}" ]] || vrack_node_args+=(--interface-mac "$K3S_PRIVATE_INTERFACE_MAC")
        [[ -z "${K3S_VRACK_VLAN_ID:-}" ]] || vrack_node_args+=(--vlan-id "$K3S_VRACK_VLAN_ID")
        [[ "$AUTO_APPROVE" != "true" ]] || vrack_node_args+=(--non-interactive)
        step "Configuring and validating the control-plane OVHcloud vRack interface before any firewall changes..."
        K3S_PRIVATE_INTERFACE="$("$OVH_VRACK_SCRIPT" "${vrack_node_args[@]}")"
        export K3S_PRIVATE_INTERFACE K3S_PRIVATE_INTERFACE_MAC K3S_VRACK_VLAN_ID
    fi
    # vRack/LAN control planes share a keepalived VIP for the Kubernetes API;
    # an already recorded endpoint is reused by enrollment.
    if [[ "$ADD_K3S_CONTROL_PLANES" == true && "$K3S_NODE_TRANSPORT" != tailscale ]] && \
        ! kubectl -n kube-system get configmap swirl-cloud-api-endpoint >/dev/null 2>&1; then
        if [[ -z "${K3S_API_VIP:-}" && "$AUTO_APPROVE" != true ]]; then
            installer_prompt_value K3S_API_VIP \
                "Unused node-network IPv4 for the Kubernetes API VIP (moves between control planes)"
        fi
        [[ -n "${K3S_API_VIP:-}" ]] || error "K3S_API_VIP is required when adding vRack or LAN control planes."
        cidr_contains_ip "$K3S_NODE_NETWORK_CIDR" "$K3S_API_VIP" && [[ "$K3S_API_VIP" != "$K3S_PRIVATE_ADDRESS" ]] || \
            error "K3S_API_VIP must be an unused address inside $K3S_NODE_NETWORK_CIDR."
        export K3S_API_VIP
    fi
fi

configure_platform_component_selection
if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
    INSTALL_LONGHORN=true
    INSTALL_INGRESS=true
    INSTALL_VAULT_STACK=true
    DEPLOY_DATA_STORES=true
    DEPLOY_PLATFORM_SERVICES=true
    INSTALL_ARGOCD=true
    CONFIGURE_CLOUDFLARE=true
fi

installer_prompt_section "Recovery and public access" \
    "Configure off-node backups and, for internet-facing clusters, public DNS and TLS."
configure_backup_destination
configure_platform_mail
if [[ "$SERVER_EXPOSURE" == "internet" && "$AUTO_APPROVE" != true ]]; then
    decide CONFIGURE_CLOUDFLARE "Configure Cloudflare public DNS and Origin TLS?" "Y"
elif [[ "$SERVER_EXPOSURE" != internet ]]; then
    CONFIGURE_CLOUDFLARE=false
fi
if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
    CONFIGURE_CLOUDFLARE=true
    # HA ingress uses a Cloudflare Tunnel, so there is no origin address to publish;
    # configure-cloudflare.sh --origin-ip can still manage a node record.
    export CLOUDFLARE_PUBLISH_NODE_DNS=false
fi
if [[ "$AUTO_APPROVE" == true && "$CONFIGURE_CLOUDFLARE" == true ]]; then
    [[ "$SERVER_EXPOSURE" == internet || "$HIGH_AVAILABILITY_ENABLED" == true ]] || error "Direct Cloudflare ingress requires internet exposure."
    [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]] || error "Set CLOUDFLARE_API_TOKEN for Cloudflare reconciliation with --yes."
    if [[ "${CLOUDFLARE_ENABLE_ACCESS:-true}" == true ]]; then
        [[ -n "${CLOUDFLARE_ACCESS_ALLOWED_EMAILS:-}" ]] || \
            error "Set CLOUDFLARE_ACCESS_ALLOWED_EMAILS or CLOUDFLARE_ENABLE_ACCESS=false for --yes."
    fi
fi
if [[ "$CONFIGURE_CLOUDFLARE" == "true" && "$AUTO_APPROVE" != "true" ]]; then
    installer_prompt_value CLOUDFLARE_ACCESS_TEAM_NAME \
        "Cloudflare Zero Trust team label (first part of TEAM.cloudflareaccess.com)" \
        "$CLOUDFLARE_ACCESS_TEAM_NAME"
fi
[[ "$CLOUDFLARE_ACCESS_TEAM_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#CLOUDFLARE_ACCESS_TEAM_NAME} -le 63 ]] || \
    error "CLOUDFLARE_ACCESS_TEAM_NAME must be a single lowercase DNS label."
export CLOUDFLARE_ACCESS_TEAM_NAME

[[ "$REPOSITORY_SOURCE" =~ ^(github|gitlab)$ ]] || error "REPOSITORY_SOURCE must be github or gitlab."
export REPOSITORY_SOURCE GITLAB_PROJECTS
if [[ "$CONFIGURE_REPOSITORY_SYNC" == true && "$AUTO_APPROVE" == true && "$REPOSITORY_SOURCE" == gitlab ]]; then
    [[ -n "$GITLAB_PROJECTS" && -n "${DEPLOY_REPOSITORIES:-}" ]] || \
        error "Deploying GitLab projects with --yes requires GITLAB_PROJECTS and DEPLOY_REPOSITORIES (all, none, or names)."
elif [[ "$CONFIGURE_REPOSITORY_SYNC" == true && "$AUTO_APPROVE" == true ]]; then
    [[ -n "${GITHUB_USERNAME:-${GITHUB_OWNER:-}}" && -n "${GITHUB_ADMIN_TOKEN:-}" &&
       -n "${GITHUB_REPOSITORIES:-}" && -n "${DEPLOY_REPOSITORIES:-}" ]] || \
        error "Repository replication with --yes requires GITHUB_USERNAME, GITHUB_ADMIN_TOKEN, GITHUB_REPOSITORIES, and DEPLOY_REPOSITORIES (all, none, or names)."
fi

if [[ "$DEPLOY_PLATFORM_SERVICES" == "true" && "$DEPLOY_DATA_STORES" != "true" ]]; then
    warn "Platform services depend on data stores; enabling data store deployment."
    DEPLOY_DATA_STORES=true
fi

if [[ "$DEPLOY_ODOO" == "true" && "$DEPLOY_DATA_STORES" != "true" ]]; then
    warn "Odoo depends on PostgreSQL; enabling data store deployment."
    DEPLOY_DATA_STORES=true
fi

if [[ "$DEPLOY_ODOO" == "true" && "$DEPLOY_PLATFORM_SERVICES" != "true" ]]; then
    warn "Odoo SSO depends on Keycloak; enabling platform service deployment."
    DEPLOY_PLATFORM_SERVICES=true
fi

if [[ "$DEPLOY_PLATFORM_SERVICES" == "true" ]]; then
    installer_prompt_section "Administrator credentials" \
        "Configure the shared Keycloak administrator and optional local password alignment."
    prompt_platform_admin_credentials
    configure_local_admin_password_rotation
else
    ROTATE_LOCAL_ADMIN_PASSWORDS=false
    LOCAL_ADMIN_PASSWORD=""
    unset LOCAL_ADMIN_PASSWORD
fi

if [[ "$DEPLOY_DATA_STORES" == "true" && "$INSTALL_VAULT_STACK" != "true" ]]; then
    warn "Data stores require Vault-synced secrets; enabling Vault + External Secrets install."
    INSTALL_VAULT_STACK=true
fi

if [[ "$CONFIGURE_CLOUDFLARE" == "true" && "$INSTALL_INGRESS" != "true" ]]; then
    warn "Cloudflare publishing requires Traefik ingress; enabling ingress installation."
    INSTALL_INGRESS=true
fi

RUN_K8S_FEATURES=false
if [[ "$INSTALL_K3S" == "true" || "$ENROLL_K3S_NODES" == "true" || "$INSTALL_LONGHORN" == "true" || "$INSTALL_INGRESS" == "true" || "$CONFIGURE_CLOUDFLARE" == "true" || "$INSTALL_VAULT_STACK" == "true" || "$DEPLOY_DATA_STORES" == "true" || "$DEPLOY_PLATFORM_SERVICES" == "true" || "$DEPLOY_ODOO" == "true" || "$INSTALL_DESCHEDULER" == "true" || "$INSTALL_ARGOCD" == "true" ]]; then
    RUN_K8S_FEATURES=true
fi

# Native manifests and Argo use the same Helm renderer for every profile.
NEEDS_HELM=true

# ---------- Prerequisites section ----------------------------------------------
if [[ "$INSTALL_PREREQS" == "true" ]]; then
    step "Installing system prerequisites..."
    sudo apt-get update -qq
    # Runtime tools plus the validator's; the distro Node.js is enough because it
    # only syntax-checks embedded scripts.
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        ansible \
        open-iscsi \
        nfs-common \
        curl \
        dnsutils \
        jq \
        git \
        python3-yaml \
        libsodium23 \
        openssl \
        shellcheck \
        sqlite3 \
        util-linux \
        > /dev/null
    if ! command -v node &>/dev/null; then
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nodejs > /dev/null
    fi

    if [[ "$INSTALL_DEVELOPER_TOOLS" == true ]]; then
        info "Installing developer tools (Java 21, Maven, Docker)..."
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
            openjdk-21-jdk maven docker.io > /dev/null
        if ! groups "$USER" | grep -q docker; then
            info "Adding $USER to docker group (re-login required for non-sudo docker)..."
            sudo usermod -aG docker "$USER"
        fi
    fi

    sudo systemctl enable --now iscsid > /dev/null 2>&1
else
    warn "Skipping system prerequisites."
fi

# Rendering uses Python/YAML and Helm, including the default non-HA profile.
python3 -c 'import yaml' >/dev/null 2>&1 || error "Configuration rendering requires python3-yaml; enable prerequisite installation."
if [[ "$NEEDS_HELM" == "true" ]] && ! command -v helm &>/dev/null; then
    info "Installing pinned Helm ${DEFAULT_HELM_VERSION:-}..."
    install_pinned_helm
fi
if [[ "$RUN_K8S_FEATURES" == true ]] && command -v kubectl >/dev/null 2>&1 && kubectl cluster-info >/dev/null 2>&1; then
    "$SCRIPT_DIR/scripts/check-platform-migration.sh"
fi
prepare_rendered_configuration

if [[ "$RUN_K8S_FEATURES" == "true" ]]; then
    if [[ "$ENROLL_K3S_NODES" == "true" ]]; then
        [[ -x "$K3S_NETWORK_SCRIPT" ]] || error "K3s private-network configurator not found or not executable: $K3S_NETWORK_SCRIPT"
        network_args=()
        [[ -z "${K3S_PRIVATE_ADDRESS:-}" ]] || network_args+=(--private-ip "$K3S_PRIVATE_ADDRESS")
        [[ -z "${K3S_PRIVATE_INTERFACE:-}" ]] || network_args+=(--private-interface "$K3S_PRIVATE_INTERFACE")
        [[ -z "${K3S_PUBLIC_ADDRESS:-}" ]] || network_args+=(--public-ip "$K3S_PUBLIC_ADDRESS")
        if [[ "$INSTALL_K3S" != "true" ]] && systemctl cat k3s.service >/dev/null 2>&1; then
            network_args+=(--restart)
        fi
        step "Configuring private K3s control-plane networking..."
        "$K3S_NETWORK_SCRIPT" "${network_args[@]}"
    fi

    if (( CONTROL_PLANE_COUNT > 1 )); then
        step "Preparing the bootstrap server's embedded etcd datastore..."
        "$K3S_HA_SCRIPT"
    fi

    step "Configuring Kubernetes API audit logging..."
    "$SCRIPT_DIR/scripts/configure-k3s-audit.sh"

    step "Reserving node memory for the host and K3s..."
    "$SCRIPT_DIR/scripts/configure-k3s-kubelet-memory.sh"

    if [[ "$INSTALL_K3S" == "true" ]]; then
        info "Installing K3s with bundled ingress disabled; the shared Helm release owns Traefik..."
        download_installer https://get.k3s.io k3s-install.sh k3s_installer
        k3s_server_args=(
            --disable traefik
            --node-name "$CONTROL_PLANE_NODE_NAME"
            --secrets-encryption
            --write-kubeconfig-mode 600
        )
        sudo env INSTALL_K3S_VERSION="$K3S_INSTALL_VERSION" sh "$k3s_installer" \
            "${k3s_server_args[@]}"
    fi

    if [[ -f /etc/rancher/k3s/k3s.yaml ]]; then
        umask 077
        mkdir -p ~/.kube
        chmod 700 ~/.kube
        sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
        sudo chown "$USER":"$USER" ~/.kube/config
        chmod 600 ~/.kube/config

        [[ -x "$K3S_REGISTRY_MIRROR_SCRIPT" ]] || \
            error "K3s registry mirror configurator is not executable: $K3S_REGISTRY_MIRROR_SCRIPT"
        K3S_REGISTRY_HOST="$K3S_REGISTRY_HOST" K3S_REGISTRY_ENDPOINT="$K3S_REGISTRY_ENDPOINT" \
            "$K3S_REGISTRY_MIRROR_SCRIPT"
    fi
    export KUBECONFIG="$HOME/.kube/config"
    grep -q "KUBECONFIG" ~/.bashrc 2>/dev/null || \
        echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
fi

if command -v k3s >/dev/null 2>&1; then
    [[ -x "$K3S_APPARMOR_SCRIPT" ]] || error "K3s AppArmor configurator is not executable: $K3S_APPARMOR_SCRIPT"
    step "Configuring the enforced K3s AppArmor runtime profile..."
    "$K3S_APPARMOR_SCRIPT"

    [[ -x "$K3S_BACKUP_SCRIPT" ]] || error "K3s backup configurator is not executable: $K3S_BACKUP_SCRIPT"
    step "Configuring daily consistent K3s recovery archives..."
    "$K3S_BACKUP_SCRIPT"
fi

if [[ "$INSTALL_LONGHORN" == "true" ]]; then
    [[ -x "$LONGHORN_HOST_SCRIPT" ]] || error "Longhorn host configurator is not executable: $LONGHORN_HOST_SCRIPT"
    step "Configuring Longhorn host storage prerequisites..."
    "$LONGHORN_HOST_SCRIPT"
fi

if [[ -x "$SECURITY_HARDEN_SCRIPT" ]]; then
    step "Applying the $SERVER_EXPOSURE control-plane host security policy..."
    "$SECURITY_HARDEN_SCRIPT" --apply --server-exposure "$SERVER_EXPOSURE" --node-role control-plane
else
    warn "Host security script not found at $SECURITY_HARDEN_SCRIPT"
fi

if [[ -n "$SERVER_MONTHLY_COST" ]]; then
    step "Recording this server's monthly cost for cost showback..."
    "$SCRIPT_DIR/scripts/configure-node-cost.sh" --monthly-cost "$SERVER_MONTHLY_COST" --currency "$COST_CURRENCY"
fi

# ---------- Infrastructure section ---------------------------------------------
if [[ "$RUN_K8S_FEATURES" == "true" ]]; then
    command -v kubectl &>/dev/null || error "kubectl not found."
    command -v openssl &>/dev/null || error "openssl not found."
    kubectl cluster-info &>/dev/null || error "Cannot reach K8s cluster."
    [[ "$NEEDS_HELM" != "true" ]] || command -v helm &>/dev/null || error "helm not found."

    # Direct ingress uses the bootstrap host. Tunnel HA uses private listeners
    # on every control plane and leaves the public ServiceLB disabled.
    kubectl get node "$CONTROL_PLANE_NODE_NAME" -o json | jq -e \
        '.metadata.labels | has("node-role.kubernetes.io/control-plane") or has("node-role.kubernetes.io/master")' >/dev/null || \
        error "CONTROL_PLANE_NODE_NAME must identify the bootstrap control-plane node."
    kubectl label "node/$CONTROL_PLANE_NODE_NAME" \
        "svccontroller.k3s.cattle.io/enablelb=$([[ "$HIGH_AVAILABILITY_ENABLED" == true ]] && echo false || echo true)" \
        node.swirl-cloud.io/role=control-plane \
        "node.swirl-cloud.io/exposure=$SERVER_EXPOSURE" \
        --overwrite >/dev/null

    if [[ "$INSTALL_K3S" == "true" ]]; then
        kubectl wait --for=condition=Ready node --all --timeout=120s
    fi

    if [[ "$ADD_K3S_CONTROL_PLANES" == "true" ]]; then
        [[ -x "$NODE_ENROLLMENT_SCRIPT" ]] || error "Node enrollment script is not executable."
        control_plane_manager_args=(--defer-topology --control-plane-schedulable "$CONTROL_PLANE_SCHEDULABLE"
            --transport "$K3S_NODE_TRANSPORT" --server-url "https://${K3S_PRIVATE_ADDRESS}:6443")
        if [[ -n "${K3S_CONTROL_PLANE_HOSTS:-}" ]]; then
            control_plane_manager_args+=(--control-plane-hosts "$K3S_CONTROL_PLANE_HOSTS")
        elif [[ -n "${K3S_CONTROL_PLANE_IPS:-}" ]]; then
            control_plane_manager_args+=(--control-plane-ips "$K3S_CONTROL_PLANE_IPS")
        else
            control_plane_manager_args+=(--control-plane-count "$CONTROL_PLANES_TO_ADD")
        fi
        [[ -z "${K3S_CONTROL_PLANE_SSH_USER:-}" ]] || control_plane_manager_args+=(--ssh-user "$K3S_CONTROL_PLANE_SSH_USER")
        [[ -z "${K3S_CONTROL_PLANE_SSH_PORT:-}" ]] || control_plane_manager_args+=(--ssh-port "$K3S_CONTROL_PLANE_SSH_PORT")
        [[ -z "${K3S_CONTROL_PLANE_IDENTITY_FILE:-}" ]] || control_plane_manager_args+=(--identity-file "$K3S_CONTROL_PLANE_IDENTITY_FILE")
        [[ -z "${K3S_NODE_NETWORK_CIDR:-}" ]] || control_plane_manager_args+=(--node-network-cidr "$K3S_NODE_NETWORK_CIDR")
        [[ "$AUTO_APPROVE" != "true" ]] || control_plane_manager_args+=(--non-interactive)
        step "Joining additional K3s control-plane servers..."
        if [[ "$K3S_NODE_TRANSPORT" == "tailscale" ]]; then
            TAILSCALE_API_TOKEN="$TAILSCALE_API_TOKEN" \
                "$NODE_ENROLLMENT_SCRIPT" --role control-plane --mode remote "${control_plane_manager_args[@]}"
        else
            "$NODE_ENROLLMENT_SCRIPT" --role control-plane --mode remote "${control_plane_manager_args[@]}"
        fi
    fi

    if [[ "$ADD_K3S_WORKERS" == "true" ]]; then
        [[ -x "$NODE_ENROLLMENT_SCRIPT" ]] || error "Node enrollment script is not executable: $NODE_ENROLLMENT_SCRIPT"
        worker_manager_args=()
        worker_ip_list="${K3S_WORKER_IPS:-}"
        [[ -z "$worker_ip_list" ]] || worker_manager_args+=(--worker-ips "$worker_ip_list")
        worker_host_list="${K3S_WORKER_HOSTS:-}"
        [[ -z "$worker_host_list" ]] || worker_manager_args+=(--worker-hosts "$worker_host_list")
        if [[ -z "$worker_ip_list$worker_host_list" ]]; then
            worker_manager_args+=(--worker-count "$WORKERS_TO_ADD")
        fi
        worker_manager_args+=(--control-plane-schedulable "$CONTROL_PLANE_SCHEDULABLE")
        worker_manager_args+=(--transport "$K3S_NODE_TRANSPORT")
        worker_server_url="${K3S_SERVER_URL:-https://${K3S_PRIVATE_ADDRESS}:6443}"
        worker_manager_args+=(--server-url "$worker_server_url")
        [[ -z "${K3S_WORKER_SSH_USER:-}" ]] || worker_manager_args+=(--ssh-user "$K3S_WORKER_SSH_USER")
        [[ -z "${K3S_WORKER_SSH_PORT:-}" ]] || worker_manager_args+=(--ssh-port "$K3S_WORKER_SSH_PORT")
        [[ -z "${K3S_WORKER_IDENTITY_FILE:-}" ]] || worker_manager_args+=(--identity-file "$K3S_WORKER_IDENTITY_FILE")
        [[ -z "${K3S_NODE_NETWORK_CIDR:-}" ]] || worker_manager_args+=(--node-network-cidr "$K3S_NODE_NETWORK_CIDR")
        [[ -z "${K3S_WORKER_LABELS:-}" ]] || worker_manager_args+=(--labels "$K3S_WORKER_LABELS")
        [[ -z "${K3S_WORKER_TAINTS:-}" ]] || worker_manager_args+=(--taints "$K3S_WORKER_TAINTS")
        [[ "$AUTO_APPROVE" != "true" ]] || worker_manager_args+=(--non-interactive)
        step "Adding K3s worker nodes..."
        if [[ "$K3S_NODE_TRANSPORT" == "tailscale" ]]; then
            TAILSCALE_API_TOKEN="$TAILSCALE_API_TOKEN" \
                "$NODE_ENROLLMENT_SCRIPT" --role worker --mode remote "${worker_manager_args[@]}"
        else
            "$NODE_ENROLLMENT_SCRIPT" --role worker --mode remote "${worker_manager_args[@]}"
        fi
    fi

    step "Creating infra namespace..."
    kubectl create namespace infra --dry-run=client -o yaml | kubectl apply -f - >/dev/null
    [[ -x "$CLUSTER_TOPOLOGY_SCRIPT" ]] || \
        error "Cluster topology reconciler is not executable: $CLUSTER_TOPOLOGY_SCRIPT"
    step "Reconciling control-plane scheduling and topology-dependent configuration..."
    "$CLUSTER_TOPOLOGY_SCRIPT" \
        --control-plane-schedulable "$CONTROL_PLANE_SCHEDULABLE" \
        --expected-control-plane-count "$CONTROL_PLANE_COUNT" \
        --expected-node-count "$CLUSTER_NODE_COUNT"
    if (( CLUSTER_NODE_COUNT > 1 )); then
        "$SCRIPT_DIR/scripts/reconcile-control-plane-access.sh" \
            --node-network-cidr "$K3S_NODE_NETWORK_CIDR" \
            --control-plane-ip "$K3S_PRIVATE_ADDRESS"
    fi
    if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
        "$SCRIPT_DIR/scripts/configure-ha-control-planes.sh" --reconcile \
            --node-network-cidr "$K3S_NODE_NETWORK_CIDR" \
            --control-plane-ip "$K3S_PRIVATE_ADDRESS"
    fi
    step "Creating the shared application namespace..."
    kubectl apply -f "$K8S_DIR/base/apps-namespace.yaml" >/dev/null
    if [[ "$INSTALL_APPS" == "true" ]]; then
        kubectl apply -f "$K8S_DIR/base/corp-namespace.yaml" >/dev/null
    fi
    step "Configuring the cluster-only $INTERNAL_DNS_ZONE service aliases..."
    for manifest in "${FOUNDATION_MANIFEST_ARRAY[@]}"; do
        kubectl apply -f "$K8S_DIR/$manifest"
    done
    kubectl apply -f "$K8S_DIR/base/security-baseline.yaml"
    "$SCRIPT_DIR/scripts/reconcile-system-hardening.sh"

    step "Publishing host security policy record..."
    kubectl apply -f "$K8S_DIR/base/host-security-config.yaml"

    if [[ "$CONFIGURE_CLOUDFLARE" != "true" && ( "$INSTALL_INGRESS" == "true" || "$INSTALL_VAULT_STACK" == "true" || "$DEPLOY_PLATFORM_SERVICES" == "true" || "$DEPLOY_ODOO" == "true" ) ]]; then
        step "Ensuring HTTPS TLS secret..."
        tls_domains=("$CLOUDFLARE_ZONE" "*.$CLOUDFLARE_ZONE")
        ensure_tls_secret infra "$TLS_SECRET_NAME" "${tls_domains[@]}"
        ensure_tls_secret apps "$TLS_SECRET_NAME" "${tls_domains[@]}"
        if [[ "$INSTALL_APPS" == "true" ]]; then
            ensure_tls_secret corp "$TLS_SECRET_NAME" "${tls_domains[@]}"
        fi
    fi

    if [[ "$INSTALL_LONGHORN" == "true" ]]; then
        longhorn_replicas="$("$CLUSTER_TOPOLOGY_SCRIPT" --print-longhorn-replicas)"
        step "Installing/upgrading Longhorn with $longhorn_replicas default replica(s) for new volumes..."
        LONGHORN_REPLICA_COUNT="$longhorn_replicas" LONGHORN_CHART_VERSION="$LONGHORN_CHART_VERSION" \
            LONGHORN_HELM_TIMEOUT="$LONGHORN_HELM_TIMEOUT" LONGHORN_POD_WAIT_TIMEOUT="$LONGHORN_POD_WAIT_TIMEOUT" \
            "$PLATFORM_RELEASE_SCRIPT" longhorn
        "$CLUSTER_TOPOLOGY_SCRIPT" \
            --control-plane-schedulable "$CONTROL_PLANE_SCHEDULABLE" \
            --expected-control-plane-count "$CONTROL_PLANE_COUNT" \
            --expected-node-count "$CLUSTER_NODE_COUNT"
        if [[ "$CONFIGURE_OFFSITE_BACKUPS" == "true" ]]; then
            step "Configuring recurring off-node Longhorn volume backups..."
            RUN_BACKUP_NOW=false "$K3S_BACKUP_SCRIPT"
        fi
    fi

    if [[ "$INSTALL_INGRESS" == "true" ]]; then
        step "Installing Traefik ingress controller..."
        if [[ "$HIGH_AVAILABILITY_ENABLED" == true && -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
            installer_prompt_secret CLOUDFLARE_API_TOKEN "Cloudflare token with Tunnel Edit and DNS/TLS permissions (input hidden)"
            export CLOUDFLARE_API_TOKEN
        fi
        # A local installation has no Cloudflare edge whose HSTS policy the origin repeats.
        ingress_hsts="${CLOUDFLARE_ENABLE_HSTS:-}"
        [[ "$SERVER_EXPOSURE" != local || "$HIGH_AVAILABILITY_ENABLED" == true ]] || ingress_hsts=false
        INGRESS_VALUES_FILE="$INGRESS_VALUES_FILE" TRAEFIK_CHART_VERSION="$TRAEFIK_CHART_VERSION" \
            INGRESS_HELM_TIMEOUT="$INGRESS_HELM_TIMEOUT" CLOUDFLARE_ENABLE_HSTS="$ingress_hsts" \
            "$SCRIPT_DIR/scripts/configure-ingress.sh"
    fi

    if [[ "$CONFIGURE_CLOUDFLARE" == "true" ]]; then
        [[ -x "$CLOUDFLARE_SCRIPT" ]] || error "Cloudflare configurator is not executable: $CLOUDFLARE_SCRIPT"
        if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
            cat >&2 <<'EOF'

Create a Cloudflare User API Token:
  1. Open https://dash.cloudflare.com/profile/api-tokens.
  2. Create a custom user token for the selected account.
  3. Grant Zone read/edit; DNS, Zone Settings, SSL, WAF, Cache Rules and
     Transform Rules edit; Bot Management read/edit; Access Apps and
     Organizations edit; Memberships and Registrar Domains read.
  4. For a new zone, scope zone permissions to all zones in the account.
  5. Paste the cfut_ token below; it is used only for this installer run.
EOF
            installer_prompt_secret CLOUDFLARE_API_TOKEN "Cloudflare User API Token (cfut_...)"
            export CLOUDFLARE_API_TOKEN
        fi
        step "Configuring Cloudflare DNS and Origin TLS..."
        CLOUDFLARE_ENABLE_ACCESS=false "$CLOUDFLARE_SCRIPT"
    fi

    if [[ "$INSTALL_VAULT_STACK" == "true" ]]; then
        step "Installing HashiCorp Vault..."
        VAULT_VALUES_FILE="$VAULT_VALUES_FILE" VAULT_CHART_VERSION="$VAULT_CHART_VERSION" \
            "$PLATFORM_RELEASE_SCRIPT" vault

        step "Installing External Secrets Operator..."
        EXTERNAL_SECRETS_CHART_VERSION="$EXTERNAL_SECRETS_CHART_VERSION" \
            EXTERNAL_SECRETS_HELM_TIMEOUT="$EXTERNAL_SECRETS_HELM_TIMEOUT" \
            "$PLATFORM_RELEASE_SCRIPT" external-secrets

        step "Applying unified Vault manifests (ingress, RBAC, and secret sync)..."
        kubectl apply -f "$K8S_DIR/platform/vault.yaml"

        VAULT_WAIT_TIMEOUT="$VAULT_WAIT_TIMEOUT" "$PLATFORM_RELEASE_SCRIPT" wait-vault-stack

        [[ -x "$VAULT_BOOTSTRAP_SCRIPT" ]] || error "Vault configurator is not executable: $VAULT_BOOTSTRAP_SCRIPT"
        step "Bootstrapping Vault auth/policies and seeding secrets..."
        "$VAULT_BOOTSTRAP_SCRIPT" infra
        if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
            "$SCRIPT_DIR/scripts/sync-vault-recovery.sh" --all-control-planes \
                --node-network-cidr "$K3S_NODE_NETWORK_CIDR" --control-plane-ip "$K3S_PRIVATE_ADDRESS"
        fi

        for es in "${EXTERNAL_SECRET_NAME_ARRAY[@]}"; do
            kubectl wait --for=condition=Ready externalsecret/"$es" -n infra \
                --timeout="$EXTERNAL_SECRET_WAIT_TIMEOUT"
        done
        for es in "${HOST_AGENTS_EXTERNAL_SECRET_NAME_ARRAY[@]}"; do
            kubectl wait --for=condition=Ready externalsecret/"$es" -n "$HOST_AGENTS_NAMESPACE" \
                --timeout="$EXTERNAL_SECRET_WAIT_TIMEOUT"
        done
    fi

    if [[ "$CONFIGURE_PLATFORM_MAIL" == "true" ]]; then
        step "Configuring the platform mail relay..."
        platform_mail_args=(--host "$PLATFORM_MAIL_HOST" --tls "$PLATFORM_MAIL_TLS" \
            --username "$PLATFORM_MAIL_USERNAME" --password-stdin --restart-services)
        [[ -z "$PLATFORM_MAIL_PORT" ]] || platform_mail_args+=(--port "$PLATFORM_MAIL_PORT")
        [[ -z "$PLATFORM_MAIL_FROM" ]] || platform_mail_args+=(--from "$PLATFORM_MAIL_FROM")
        [[ -z "$PLATFORM_MAIL_FROM_NAME" ]] || platform_mail_args+=(--from-name "$PLATFORM_MAIL_FROM_NAME")
        [[ -z "$PLATFORM_MAIL_ALERT_TO" ]] || platform_mail_args+=(--alert-to "$PLATFORM_MAIL_ALERT_TO")
        printf '%s\n' "$PLATFORM_MAIL_PASSWORD" | \
            "$SCRIPT_DIR/scripts/configure-platform-mail.py" "${platform_mail_args[@]}"
        PLATFORM_MAIL_PASSWORD=""
    fi

    if [[ "$DEPLOY_DATA_STORES" == "true" ]]; then
        step "Deploying core data stores..."
        if [[ -n "${PLATFORM_HA_VALUES_FILE:-}" ]]; then
            kubectl apply -f "$K8S_DIR/ha/platform.yaml"
        fi
        if [[ "$HIGH_AVAILABILITY_ENABLED" == true ]]; then
            "$SCRIPT_DIR/scripts/reconcile-system-hardening.sh"
            kubectl rollout status deployment/coredns -n kube-system --timeout="$DATASTORE_WAIT_TIMEOUT"
            kubectl rollout status statefulset/shared-redis-ha-server -n infra --timeout="$DATASTORE_WAIT_TIMEOUT"
            kubectl rollout status deployment/shared-redis-ha-haproxy -n infra --timeout="$DATASTORE_WAIT_TIMEOUT"
        fi
        for manifest in "${DATASTORE_MANIFEST_ARRAY[@]}"; do
            kubectl apply -f "$K8S_DIR/$manifest"
        done

        for app in "${DATASTORE_WAIT_APP_ARRAY[@]}"; do
            if [[ "$app" == postgres && "$POSTGRES_HA_ACTIVE" == true ]]; then
                kubectl wait --for=condition=Ready cluster.postgresql.cnpg.io/postgres-ha -n infra --timeout="$DATASTORE_WAIT_TIMEOUT"
                continue
            fi
            [[ "$HIGH_AVAILABILITY_ENABLED" != true || "$app" != redis ]] || continue
            kubectl wait --for=condition=ready pod -l "app=$app" -n infra \
                --timeout="$DATASTORE_WAIT_TIMEOUT"
        done
    fi

    if [[ "$DEPLOY_PLATFORM_SERVICES" == "true" ]]; then
        step "Deploying platform services..."
        for manifest in "${PLATFORM_MANIFEST_ARRAY[@]}"; do
            kubectl apply -f "$K8S_DIR/$manifest"
        done
        "$SCRIPT_DIR/scripts/reconcile-system-hardening.sh"

        for app in "${PLATFORM_WAIT_APP_ARRAY[@]}"; do
            kubectl wait --for=condition=ready pod -l "app=$app" -n infra \
                --timeout="$PLATFORM_WAIT_TIMEOUT"
        done
        for daemonset in "${PLATFORM_WAIT_DAEMONSET_ARRAY[@]}"; do
            kubectl rollout status "daemonset/$daemonset" -n "$HOST_AGENTS_NAMESPACE" \
                --timeout="$PLATFORM_WAIT_TIMEOUT"
        done
        if [[ "$ROTATE_LOCAL_ADMIN_PASSWORDS" == "true" ]]; then
            [[ -x "$LOCAL_ADMIN_PASSWORD_ROTATION_SCRIPT" ]] || \
                error "Local administrator password rotation script is not executable: $LOCAL_ADMIN_PASSWORD_ROTATION_SCRIPT"
            step "Aligning local infrastructure superuser passwords..."
            printf '%s\n' "$LOCAL_ADMIN_PASSWORD" | \
                "$LOCAL_ADMIN_PASSWORD_ROTATION_SCRIPT" --password-stdin
            LOCAL_ADMIN_PASSWORD=""
            unset LOCAL_ADMIN_PASSWORD
        fi

        if [[ "$CONFIGURE_CLOUDFLARE" == "true" ]]; then
            step "Connecting Cloudflare Access to Keycloak..."
            "$CLOUDFLARE_SCRIPT"
        fi

        [[ -x "$GITLAB_CI_SCRIPT" ]] || error "GitLab CI configurator is not executable: $GITLAB_CI_SCRIPT"
        [[ -r "$GITLAB_TOKEN_LIBRARY" ]] || error "GitLab token helper is missing: $GITLAB_TOKEN_LIBRARY"
        # shellcheck source=scripts/lib/gitlab-admin-token.sh
        source "$GITLAB_TOKEN_LIBRARY"
        gitlab_acquire_admin_token
        GITLAB_PUBLIC_URL="https://gitlab.$PLATFORM_DOMAIN" \
        CONFIGURE_REPOSITORY_SYNC=false \
            "$GITLAB_CI_SCRIPT"
        gitlab_revoke_ephemeral_admin_token
    fi

    if [[ "$DEPLOY_PLATFORM_SERVICES" == "true" ]]; then
        kubectl apply -f "$K8S_DIR/platform/sonar-apps-discovery.yaml"
    fi

    if [[ "$DEPLOY_ODOO" == "true" ]]; then
        step "Deploying Odoo in the corp namespace..."
        kubectl apply -f "$K8S_DIR/corp/odoo.yaml"
        kubectl wait --for=condition=Ready externalsecret/odoo-secret externalsecret/platform-mail -n corp \
            --timeout="$EXTERNAL_SECRET_WAIT_TIMEOUT"
        kubectl wait --for=condition=ready pod -l app=odoo -n corp \
            --timeout="$PLATFORM_WAIT_TIMEOUT"
    fi

    if [[ "$INSTALL_DESCHEDULER" == "true" ]]; then
        step "Installing Descheduler addon resources (manual-run mode)..."
        kubectl apply -f "$K8S_DIR/addons/descheduler.yaml"
    fi

    if [[ "$INSTALL_ARGOCD" == "true" ]]; then
        step "Installing ArgoCD..."
        ARGOCD_VALUES_FILE="$ARGOCD_VALUES_FILE" ARGOCD_CHART_VERSION="$ARGOCD_CHART_VERSION" \
            ARGOCD_IMAGE_TAG="$ARGOCD_IMAGE_TAG" ARGOCD_HELM_TIMEOUT="$ARGOCD_HELM_TIMEOUT" \
            "$PLATFORM_RELEASE_SCRIPT" argocd

        step "Applying the platform Argo CD Application..."
        for manifest in "${POST_ARGOCD_MANIFEST_ARRAY[@]}"; do
            kubectl apply -f "$K8S_DIR/$manifest"
        done
    fi
else
    warn "All Kubernetes feature groups were skipped."
fi

if [[ -n "$DEPLOYMENT_ENVIRONMENTS_FILE" ]]; then
    step "Registering application environments on this cluster..."
    [[ -r "$DEPLOYMENT_ENVIRONMENTS_FILE" ]] || error "Deployment inventory is not readable: $DEPLOYMENT_ENVIRONMENTS_FILE"
    if [[ -z "$INSTALLER_TEMP_DIR" ]]; then
        INSTALLER_TEMP_DIR="$(mktemp -d /tmp/swirl-cloud-installers.XXXXXX)"
    fi
    environment_config_dir="$INSTALLER_TEMP_DIR/application-environments"
    install -d -m 0700 "$environment_config_dir"
    kubectl config view --minify --raw --flatten > "$environment_config_dir/platform.yaml"
    chmod 600 "$environment_config_dir/platform.yaml"
    if ! python3 - "$SCRIPT_DIR" "$DEPLOYMENT_ENVIRONMENTS_FILE" "$environment_config_dir/platform.yaml" <<'PY'
import os
from pathlib import Path
import subprocess
import sys

root, path, kubeconfig = sys.argv[1:]
sys.path.insert(0, str(Path(root) / "scripts/lib"))
from deployment_environments import load_inventory

inventory = load_inventory(path, allow_partial=True)
for environment, target in inventory["environments"].items():
    if target["mode"] != "local":
        print(f"Register {environment} separately with its remote administrator kubeconfig.")
        continue
    subprocess.run([sys.executable, str(Path(root) / "scripts/configure-deployment-environments.py"),
                    "--config", path, "--environment", environment,
                    "--platform-kubeconfig", kubeconfig,
                    "--vault-token-file", os.environ.get("VAULT_BOOTSTRAP_TOKEN_FILE", "/var/lib/swirl-cloud/vault-bootstrap-token")],
                   check=True)
PY
    then
        rm -rf -- "$environment_config_dir"
        error "Application environment registration failed; review its data authentication and TLS prerequisites."
    fi
    rm -rf -- "$environment_config_dir"
fi

if [[ "$CONFIGURE_REPOSITORY_SYNC" == true ]]; then
    step "Onboarding repositories ($REPOSITORY_SOURCE) and selecting deployments..."
    [[ -x "$REPLICATE_REPO_SCRIPT" ]] || error "Repository assistant is not executable: $REPLICATE_REPO_SCRIPT"
    replication_args=()
    [[ "$AUTO_APPROVE" != true ]] || replication_args+=(--yes)
    "$REPLICATE_REPO_SCRIPT" "${replication_args[@]}"
fi

# The outage watch (ADR 0030) runs on Cloudflare and mails through the platform
# relay; deploy it once both exist and the public hosts are known.
if [[ "$CONFIGURE_CLOUDFLARE" == true && "$CONFIGURE_PLATFORM_MAIL" == true ]]; then
    step "Deploying the outage watch on Cloudflare..."
    CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" python3 "$SCRIPT_DIR/scripts/configure-outage-watch.py" || \
        warn "The outage watch was not deployed; rerun scripts/configure-outage-watch.py (docs/guides/service-levels.md#outage-watch)."
fi

if [[ "$RUN_K8S_FEATURES" == true ]]; then
    step "Verifying the installed platform..."
    # Endpoint probes stay warnings while new DNS records propagate; failures stop here.
    python3 "$SCRIPT_DIR/scripts/verify-platform.py" || \
        error "Platform verification failed; fix the [FAIL] items above, then rerun the installer or ./install.sh --status."
fi

info ""
info "============================================="
info " Installation complete!"
info "============================================="

echo ""
echo "Cluster topology:"
echo "  Total nodes: $CLUSTER_NODE_COUNT"
echo "  Control-plane nodes: $CONTROL_PLANE_COUNT"
echo "  Worker nodes: $PLANNED_WORKER_COUNT"
if [[ "$CONTROL_PLANE_SCHEDULABLE" == "true" ]]; then
    echo "  Control plane: controller-worker"
else
    echo "  Control plane: controller-only (NoSchedule)"
fi
# Report the reconciled policy, which accounts for HA mode, not a local estimate.
longhorn_replicas="$(kubectl -n infra get configmap swirl-cloud-topology --ignore-not-found \
    -o jsonpath='{.data.longhornReplicaCount}' 2>/dev/null || true)"
echo "  Longhorn replicas: ${longhorn_replicas:-not recorded in infra/swirl-cloud-topology}"
echo ""
echo "Installed versions:"
command -v java >/dev/null 2>&1 && echo "  Java: $(java -version 2>&1 | head -1)"
command -v mvn >/dev/null 2>&1 && echo "  Maven: $(mvn -version 2>&1 | head -1)"
command -v node >/dev/null 2>&1 && echo "  Node: $(node -v)"
command -v docker >/dev/null 2>&1 && echo "  Docker: $(docker --version)"
if command -v helm >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
    LONGHORN_VERSION="$(helm list -n longhorn-system -o json 2>/dev/null | jq -r '.[0].app_version // "unknown"')"
    echo "  Longhorn: ${LONGHORN_VERSION}"
fi
echo ""
if [[ "$RUN_K8S_FEATURES" == "true" ]]; then
    if [[ "$DEPLOY_PLATFORM_SERVICES" == "true" ]] || kubectl get deployment homepage -n infra >/dev/null 2>&1; then
        echo "Portal: https://cloud.$PLATFORM_DOMAIN"
    fi

    echo ""
    echo "Retrieve credentials:"
    echo "  Keycloak SSO administrator: kubectl get secret -n infra keycloak-sso-credentials -o go-template='{{printf \"%s:%s\" (index .data \"SSO_BOOTSTRAP_USERNAME\" | base64decode) (index .data \"SSO_BOOTSTRAP_PASSWORD\" | base64decode)}}'"
    echo "  ArgoCD:   sign in at https://argocd.$PLATFORM_DOMAIN with the Keycloak SSO administrator (local admin is disabled)"
    echo "  GitLab:   sign in at https://gitlab.$PLATFORM_DOMAIN with the Keycloak SSO administrator (linked to root)"
    echo "            local root password, once set by scripts/rotate-local-admin-passwords.sh: Vault secret/infra/gitlab, field root_password"
    echo "  MongoDB:  kubectl get secret -n infra mongodb-secret -o jsonpath='{.data.MONGO_INITDB_ROOT_PASSWORD}' | base64 -d"
    echo "  Vault:    sudo cat /var/lib/swirl-cloud/vault-bootstrap-token"
    echo "  DBGate:   kubectl get secret -n infra dbgate-auth-secret -o go-template='{{printf \"%s\" (index .data \"LOGIN\" | base64decode)}}:{{printf \"%s\" (index .data \"PASSWORD\" | base64decode)}}'"
    echo "  Kafka UI: kubectl get secret -n infra kafka-ui-auth-secret -o go-template='{{printf \"%s\" (index .data \"SPRING_SECURITY_USER_NAME\" | base64decode)}}:{{printf \"%s\" (index .data \"SPRING_SECURITY_USER_PASSWORD\" | base64decode)}}'"
    echo "  Portainer: username admin; password: kubectl get secret -n infra portainer-auth-secret -o jsonpath='{.data.ADMIN_PASSWORD}' | base64 -d"
    if [[ "$DEPLOY_ODOO" == "true" ]]; then
        echo "  Odoo:     username admin; password: kubectl get secret -n corp odoo-secret -o jsonpath='{.data.ODOO_ADMIN_PASSWORD}' | base64 -d"
    fi
    echo "  Descheduler trigger: kubectl create -f k8s/addons/descheduler-run-job.yaml"
    echo "  Add nodes:            ./add-node.sh"
    echo "  Worker join token:    sudo cat /var/lib/rancher/k3s/server/node-token"
    echo ""

    echo "Cluster nodes:"
    kubectl get nodes -o wide
    echo ""
    echo "Pod status:"
    kubectl get pods -n infra --no-headers 2>&1 | awk '{printf "  %-50s %s\n", $1, $2}'
    if [[ "$INSTALL_APPS" == "true" ]]; then
        echo ""
        echo "Corporate pod status:"
        kubectl get pods -n corp --no-headers 2>&1 | awk '{printf "  %-50s %s\n", $1, $2}'
        echo "App pod status:"
        kubectl get pods -n apps --no-headers 2>&1 | awk '{printf "  %-50s %s\n", $1, $2}'
    fi
fi
