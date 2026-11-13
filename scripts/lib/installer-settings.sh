#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2153 # Inputs and rendered paths are shared with install-control-plane.sh.
# Cluster identity, repository delivery, backup, mail and rendered-configuration
# inputs for install-control-plane.sh, which provides logging, prompts and globals.

prompt_cluster_identity() {
    local default_node
    platform_identity_load
    platform_identity_new_kafka_cluster_id
    if [[ "$AUTO_APPROVE" != true ]]; then
        installer_prompt_value ORGANIZATION_NAME "Company or organization display name" "${ORGANIZATION_NAME:-}"
    fi
    [[ -n "${ORGANIZATION_NAME:-}" ]] || error "Set ORGANIZATION_NAME to the company or organization display name."

    if [[ "$AUTO_APPROVE" != "true" ]]; then
        installer_prompt_value PLATFORM_DOMAIN "Public base domain for this cluster" "$PLATFORM_DOMAIN"
    fi
    PLATFORM_DOMAIN="${PLATFORM_DOMAIN,,}"
    [[ "$PLATFORM_DOMAIN" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || \
        error "Set PLATFORM_DOMAIN to a valid base domain such as example.com."

    ORGANIZATION_SLUG="${ORGANIZATION_SLUG:-${PLATFORM_DOMAIN%%.*}}"
    if [[ "$AUTO_APPROVE" != true ]]; then
        installer_prompt_value ORGANIZATION_SLUG "Organization identifier (lowercase letters, digits, hyphens)" "$ORGANIZATION_SLUG"
        installer_prompt_value GITLAB_GROUP_PATH "GitLab group path" "${GITLAB_GROUP_PATH:-$ORGANIZATION_SLUG}"
        installer_prompt_value GITLAB_GROUP_NAME "GitLab group display name" "${GITLAB_GROUP_NAME:-$ORGANIZATION_NAME}"
        installer_prompt_value GITLAB_PROJECT_NAME "Platform GitLab project name" "${GITLAB_PROJECT_NAME:-swirl-cloud}"
        installer_prompt_value KEYCLOAK_REALM "Application SSO realm" "${KEYCLOAK_REALM:-$ORGANIZATION_SLUG}"
        installer_prompt_value TLS_SECRET_NAME "Shared TLS secret name" "${TLS_SECRET_NAME:-${CLOUDFLARE_TLS_SECRET_NAME:-${PLATFORM_DOMAIN//./-}-tls}}"
        installer_prompt_value INTERNAL_DNS_ZONE "Private service DNS zone" "${INTERNAL_DNS_ZONE:-internal.$PLATFORM_DOMAIN}"
    fi
    platform_identity_defaults
    platform_identity_validate || error "Invalid organization configuration."
    INTERNAL_DNS_ZONE="${INTERNAL_DNS_ZONE:-internal.$PLATFORM_DOMAIN}"
    INTERNAL_DNS_ZONE="${INTERNAL_DNS_ZONE,,}"
    [[ "$INTERNAL_DNS_ZONE" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] || \
        error "INTERNAL_DNS_ZONE is invalid."
    [[ "$INTERNAL_DNS_ZONE" != "$PLATFORM_DOMAIN" ]] || \
        error "The private service domain must differ from the public domain."

    default_node="${CONTROL_PLANE_NODE_NAME:-$(hostname -s | tr '[:upper:]' '[:lower:]')}"
    if [[ "$AUTO_APPROVE" != "true" ]]; then
        installer_prompt_value CONTROL_PLANE_NODE_NAME "K3s control-plane node name" "$default_node"
    else
        CONTROL_PLANE_NODE_NAME="$default_node"
    fi
    CONTROL_PLANE_NODE_NAME="${CONTROL_PLANE_NODE_NAME,,}"
    [[ "$CONTROL_PLANE_NODE_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#CONTROL_PLANE_NODE_NAME} -le 63 ]] || \
        error "CONTROL_PLANE_NODE_NAME must be a single valid lowercase DNS label."

    CLOUDFLARE_ZONE="${CLOUDFLARE_ZONE:-$PLATFORM_DOMAIN}"
    [[ "$CLOUDFLARE_ZONE" == "$PLATFORM_DOMAIN" ]] || \
        error "CLOUDFLARE_ZONE must match PLATFORM_DOMAIN for this deployment."
    CLOUDFLARE_NODE_DNS_LABEL="${CLOUDFLARE_NODE_DNS_LABEL,,}"
    [[ "$CLOUDFLARE_NODE_DNS_LABEL" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#CLOUDFLARE_NODE_DNS_LABEL} -le 63 ]] || \
        error "CLOUDFLARE_NODE_DNS_LABEL must be a single valid lowercase DNS label."
    K3S_REGISTRY_HOST="${K3S_REGISTRY_HOST:-registry.$PLATFORM_DOMAIN}"
    CLOUDFLARE_ACCESS_TEAM_NAME="${CLOUDFLARE_ACCESS_TEAM_NAME:-swirl-cloud-${PLATFORM_DOMAIN//./-}}"
    [[ "$CLOUDFLARE_ACCESS_TEAM_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#CLOUDFLARE_ACCESS_TEAM_NAME} -le 63 ]] || \
        error "CLOUDFLARE_ACCESS_TEAM_NAME must be a single lowercase DNS label."
    TAILSCALE_NODE_HOSTNAME="${TAILSCALE_NODE_HOSTNAME:-$CONTROL_PLANE_NODE_NAME}"
    export PLATFORM_DOMAIN INTERNAL_DNS_ZONE CONTROL_PLANE_NODE_NAME CLOUDFLARE_ZONE CLOUDFLARE_NODE_DNS_LABEL CLOUDFLARE_ACCESS_TEAM_NAME
    export K3S_REGISTRY_HOST TAILSCALE_NODE_HOSTNAME
    info "Cluster identity: node $CONTROL_PLANE_NODE_NAME, public domain $PLATFORM_DOMAIN, private domain $INTERNAL_DNS_ZONE"
}

github_repository_url() {
    local remote_url
    remote_url="$(git -C "$SCRIPT_DIR" remote get-url github 2>/dev/null || \
        git -C "$SCRIPT_DIR" remote get-url origin 2>/dev/null || true)"
    case "$remote_url" in
        git@github.com:*) remote_url="https://github.com/${remote_url#git@github.com:}" ;;
        ssh://git@github.com/*) remote_url="https://github.com/${remote_url#ssh://git@github.com/}" ;;
    esac
    [[ "$remote_url" == https://github.com/*.git ]] && printf '%s\n' "$remote_url"
}

configure_repository_delivery() {
    local default_gitops github_url
    github_url="$(github_repository_url || true)"

    if [[ -z "$CONFIGURE_REPOSITORY_SYNC" ]]; then
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            CONFIGURE_REPOSITORY_SYNC=false
        elif ask_with_default "Duplicate GitHub repositories to GitLab and configure two-way sync?" "N"; then
            CONFIGURE_REPOSITORY_SYNC=true
        else
            CONFIGURE_REPOSITORY_SYNC=false
        fi
    fi
    case "${CONFIGURE_REPOSITORY_SYNC,,}" in
        1|true|yes|y) CONFIGURE_REPOSITORY_SYNC=true ;;
        0|false|no|n) CONFIGURE_REPOSITORY_SYNC=false ;;
        *) error "CONFIGURE_REPOSITORY_SYNC must be true or false." ;;
    esac

    if [[ -n "${REPOSITORY_SYNC_MAPPINGS:-}" ]]; then
        error "REPOSITORY_SYNC_MAPPINGS was replaced by GITHUB_USERNAME, GITHUB_REPOSITORIES, and GITLAB_GROUP_PATH; see docs/guides/repository-onboarding.md."
    fi
    [[ "$GITLAB_GROUP_PATH" =~ ^[A-Za-z0-9_.-]+$ ]] || error "The platform GitLab group must be a top-level group."
    GITLAB_PROJECT_PATH="${GITLAB_PROJECT_PATH:-$GITLAB_GROUP_PATH/$GITLAB_PROJECT_NAME}"
    default_gitops="${GITOPS_REPOSITORY_URL:-$github_url}"
    if [[ "$AUTO_APPROVE" != "true" ]]; then
        installer_prompt_value GITOPS_REPOSITORY_URL "Cluster GitOps repository URL (must already be accessible)" "$default_gitops"
    else
        GITOPS_REPOSITORY_URL="$default_gitops"
    fi
    [[ "$GITOPS_REPOSITORY_URL" =~ ^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9_.~%+-]+)+\.git$ ]] || \
        error "Set GITOPS_REPOSITORY_URL to an HTTP(S) .git repository accessible by Argo CD."
    export CONFIGURE_REPOSITORY_SYNC GITLAB_GROUP_PATH
    export GITLAB_GROUP_NAME GITLAB_PROJECT_PATH GITOPS_REPOSITORY_URL
}

configure_backup_destination() {
    if [[ -z "$CONFIGURE_OFFSITE_BACKUPS" ]]; then
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            [[ -n "$BACKUP_S3_ENDPOINT$BACKUP_S3_BUCKET$BACKUP_S3_ACCESS_KEY$BACKUP_S3_SECRET_KEY" ]] && \
                CONFIGURE_OFFSITE_BACKUPS=true || CONFIGURE_OFFSITE_BACKUPS=false
        elif ask_with_default "Configure encrypted off-node backups in S3-compatible object storage?" "Y"; then
            CONFIGURE_OFFSITE_BACKUPS=true
        else
            CONFIGURE_OFFSITE_BACKUPS=false
        fi
    fi
    case "${CONFIGURE_OFFSITE_BACKUPS,,}" in
        1|true|yes|y) CONFIGURE_OFFSITE_BACKUPS=true ;;
        0|false|no|n) CONFIGURE_OFFSITE_BACKUPS=false ;;
        *) error "CONFIGURE_OFFSITE_BACKUPS must be true or false." ;;
    esac
    if [[ "$CONFIGURE_OFFSITE_BACKUPS" != "true" ]]; then
        warn "Backups will remain local and will not survive loss of this server."
        export CONFIGURE_OFFSITE_BACKUPS
        return
    fi

    if [[ "$AUTO_APPROVE" != "true" ]]; then
        cat >&2 <<'EOF'

Prepare S3-compatible backup storage:
  1. Create a private bucket at your object-storage provider.
  2. Enable object versioning and provider-side retention if available.
  3. Create an access key/API token limited to list, read, write, and delete
     objects in that bucket. Do not use an account-wide administrator key.
  4. Copy the S3 endpoint, region, bucket name, access-key ID, and secret key.

Examples of endpoints are https://s3.REGION.amazonaws.com and an R2/MinIO S3
endpoint supplied by that provider.
EOF
        installer_prompt_value BACKUP_S3_ENDPOINT "S3-compatible HTTPS endpoint" "$BACKUP_S3_ENDPOINT"
        installer_prompt_value BACKUP_S3_REGION "S3 region" "$BACKUP_S3_REGION"
        installer_prompt_value BACKUP_S3_BUCKET "Private backup bucket" "$BACKUP_S3_BUCKET"
        [[ -n "$BACKUP_S3_ACCESS_KEY" ]] || installer_prompt_secret BACKUP_S3_ACCESS_KEY "S3 access-key ID (input hidden)"
        [[ -n "$BACKUP_S3_SECRET_KEY" ]] || installer_prompt_secret BACKUP_S3_SECRET_KEY "S3 secret access key/API token (input hidden)"
        if [[ -z "$BACKUP_REPOSITORY_PASSWORD" ]]; then
            installer_prompt_confirmed_secret BACKUP_REPOSITORY_PASSWORD \
                "Encryption password for the off-node recovery archive" \
                "Confirm recovery-archive encryption password" || \
                error "Backup encryption passwords do not match."
        fi
    fi
    [[ "$BACKUP_S3_ENDPOINT" == https://* ]] || error "BACKUP_S3_ENDPOINT must use HTTPS."
    [[ "$BACKUP_S3_REGION" =~ ^[A-Za-z0-9._-]+$ ]] || error "BACKUP_S3_REGION is invalid."
    [[ "$BACKUP_S3_BUCKET" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{1,61}[A-Za-z0-9]$ ]] || error "BACKUP_S3_BUCKET is invalid."
    [[ -n "$BACKUP_S3_ACCESS_KEY" && -n "$BACKUP_S3_SECRET_KEY" ]] || error "S3 backup credentials are required."
    (( ${#BACKUP_REPOSITORY_PASSWORD} >= 16 )) || error "The backup encryption password must contain at least 16 characters."
    export CONFIGURE_OFFSITE_BACKUPS BACKUP_S3_ENDPOINT BACKUP_S3_REGION BACKUP_S3_BUCKET
    export BACKUP_S3_ACCESS_KEY BACKUP_S3_SECRET_KEY BACKUP_REPOSITORY_PASSWORD
}

# Platform mail relay (ADR 0029): alerts, password resets, GitLab and Odoo.
# configure-platform-mail.py validates the values and checks the login.
configure_platform_mail() {
    if [[ -z "$CONFIGURE_PLATFORM_MAIL" ]]; then
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            [[ -n "$PLATFORM_MAIL_HOST" ]] && CONFIGURE_PLATFORM_MAIL=true || CONFIGURE_PLATFORM_MAIL=false
        elif ask_with_default "Send platform email (alerts, password resets) through an SMTP relay?" "N"; then
            CONFIGURE_PLATFORM_MAIL=true
        else
            CONFIGURE_PLATFORM_MAIL=false
        fi
    fi
    case "${CONFIGURE_PLATFORM_MAIL,,}" in
        1|true|yes|y) CONFIGURE_PLATFORM_MAIL=true ;;
        0|false|no|n) CONFIGURE_PLATFORM_MAIL=false ;;
        *) error "CONFIGURE_PLATFORM_MAIL must be true or false." ;;
    esac
    if [[ "$CONFIGURE_PLATFORM_MAIL" != "true" ]]; then
        info "Platform mail is left as it is; see docs/guides/platform-mail.md to set it up later."
        return
    fi
    if [[ "$AUTO_APPROVE" != "true" ]]; then
        installer_prompt_value PLATFORM_MAIL_HOST "SMTP relay host" "$PLATFORM_MAIL_HOST"
        installer_prompt_value PLATFORM_MAIL_TLS "TLS mode: ssl (implicit TLS) or starttls" "$PLATFORM_MAIL_TLS"
        installer_prompt_value PLATFORM_MAIL_PORT "SMTP relay port (blank: 465 for ssl, 587 for starttls)" "$PLATFORM_MAIL_PORT"
        installer_prompt_value PLATFORM_MAIL_USERNAME "SMTP username" "$PLATFORM_MAIL_USERNAME"
        [[ -n "$PLATFORM_MAIL_PASSWORD" ]] || installer_prompt_secret PLATFORM_MAIL_PASSWORD "SMTP password or API key (input hidden)"
        installer_prompt_value PLATFORM_MAIL_FROM "Sender address (blank: cloud@$PLATFORM_DOMAIN)" "$PLATFORM_MAIL_FROM"
        installer_prompt_value PLATFORM_MAIL_ALERT_TO "Alert recipients, comma-separated (blank: the administrator)" "$PLATFORM_MAIL_ALERT_TO"
    fi
    [[ -n "$PLATFORM_MAIL_HOST" && -n "$PLATFORM_MAIL_USERNAME" && -n "$PLATFORM_MAIL_PASSWORD" ]] || \
        error "Platform mail needs PLATFORM_MAIL_HOST, PLATFORM_MAIL_USERNAME and PLATFORM_MAIL_PASSWORD."
    [[ "$PLATFORM_MAIL_TLS" == ssl || "$PLATFORM_MAIL_TLS" == starttls ]] || error "PLATFORM_MAIL_TLS must be ssl or starttls."
    export CONFIGURE_PLATFORM_MAIL
}

prepare_rendered_configuration() {
    local sizing sizing_reason
    [[ -x "$RENDER_CONFIG_SCRIPT" ]] || error "Configuration renderer is not executable: $RENDER_CONFIG_SCRIPT"
    # Sizing (TODO §26): the operator's choice, else the installed profile, else a
    # suggestion from this host. The renderer and the Argo CD release read it.
    sizing="$(python3 "$SCRIPT_DIR/scripts/lib/sizing.py" resolve --explicit "${SIZING_PROFILE:-}")" || \
        error "Choose a valid SIZING_PROFILE."
    SIZING_PROFILE="${sizing%%$'\t'*}"
    sizing_reason="${sizing#*$'\t'}"
    export SIZING_PROFILE
    info "Sizing profile: $SIZING_PROFILE ($sizing_reason)."
    RENDERED_CONFIG_DIR="$(mktemp -d /tmp/swirl-cloud-rendered.XXXXXX)"
    chmod 700 "$RENDERED_CONFIG_DIR"
    "$RENDER_CONFIG_SCRIPT" \
        --output "$RENDERED_CONFIG_DIR" \
        --domain "$PLATFORM_DOMAIN" \
        --internal-domain "$INTERNAL_DNS_ZONE" \
        --gitops-repository "$GITOPS_REPOSITORY_URL" \
        --gitlab-group "$GITLAB_GROUP_PATH" \
        --gitlab-project "${GITLAB_PROJECT_PATH##*/}" \
        --cloudflare-access-team "$CLOUDFLARE_ACCESS_TEAM_NAME" \
        --apps-enabled "$INSTALL_APPS" \
        --descheduler-enabled "$INSTALL_DESCHEDULER" >/dev/null
    python3 "$SCRIPT_DIR/scripts/lib/sizing.py" check-volumes --root "$RENDERED_CONFIG_DIR" || \
        error "The sizing profile would shrink volumes; keep the larger profile."
    # Argo CD renders the platform from the root Application's values: record the
    # profile there before sized manifests are applied, or its self-sync reverts them.
    sizing="$(python3 "$SCRIPT_DIR/scripts/lib/sizing.py" record --root "$RENDERED_CONFIG_DIR")" || \
        error "Could not record the sizing profile on the platform Argo CD Application."
    [[ -z "$sizing" ]] || info "Recorded sizing profile $SIZING_PROFILE on the platform Argo CD Application."
    K8S_DIR="$RENDERED_CONFIG_DIR/k8s"
    ARGOCD_VALUES_FILE="$RENDERED_CONFIG_DIR/config/argocd-values.yaml"
    VAULT_VALUES_FILE="$RENDERED_CONFIG_DIR/config/vault-values.yaml"
    INGRESS_VALUES_FILE="$RENDERED_CONFIG_DIR/config/traefik-values.yaml"
}
