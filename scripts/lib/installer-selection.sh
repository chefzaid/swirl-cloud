#!/usr/bin/env bash
# Local administrator passwords, exposure, install scope, node transport,
# component selection and platform administrator credentials for
# install-control-plane.sh, which provides logging, prompts and globals.

validate_local_admin_password() {
    LOCAL_ADMIN_PASSWORD_ERROR=""
    if (( ${#LOCAL_ADMIN_PASSWORD} < 12 )); then
        LOCAL_ADMIN_PASSWORD_ERROR="The local administrator password must contain at least 12 characters."
        return 1
    fi
    if [[ "$LOCAL_ADMIN_PASSWORD" =~ [[:cntrl:]] ]]; then
        LOCAL_ADMIN_PASSWORD_ERROR="The local administrator password cannot contain control characters."
        return 1
    fi
    if [[ ! "$LOCAL_ADMIN_PASSWORD" =~ [[:lower:]] ||
          ! "$LOCAL_ADMIN_PASSWORD" =~ [[:upper:]] ||
          ! "$LOCAL_ADMIN_PASSWORD" =~ [[:digit:]] ||
          ! "$LOCAL_ADMIN_PASSWORD" =~ [^[:alnum:]] ]]; then
        LOCAL_ADMIN_PASSWORD_ERROR="The local administrator password must include lowercase, uppercase, numeric, and special characters."
        return 1
    fi
}

configure_local_admin_password_rotation() {
    if [[ -z "$ROTATE_LOCAL_ADMIN_PASSWORDS" ]]; then
        if ask_with_default "Use one password for all local infrastructure superusers?" "N"; then
            ROTATE_LOCAL_ADMIN_PASSWORDS=true
        else
            ROTATE_LOCAL_ADMIN_PASSWORDS=false
        fi
    else
        case "${ROTATE_LOCAL_ADMIN_PASSWORDS,,}" in
            1|true|yes|y) ROTATE_LOCAL_ADMIN_PASSWORDS=true ;;
            0|false|no|n) ROTATE_LOCAL_ADMIN_PASSWORDS=false ;;
            *) error "ROTATE_LOCAL_ADMIN_PASSWORDS must be true or false." ;;
        esac
    fi

    if [[ "$ROTATE_LOCAL_ADMIN_PASSWORDS" != "true" ]]; then
        LOCAL_ADMIN_PASSWORD=""
        unset LOCAL_ADMIN_PASSWORD
        return
    fi

    while ! validate_local_admin_password; do
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            error "Set LOCAL_ADMIN_PASSWORD when ROTATE_LOCAL_ADMIN_PASSWORDS=true. $LOCAL_ADMIN_PASSWORD_ERROR"
        fi
        [[ -z "$LOCAL_ADMIN_PASSWORD" ]] || warn "$LOCAL_ADMIN_PASSWORD_ERROR"
        LOCAL_ADMIN_PASSWORD=""
        if ! installer_prompt_confirmed_secret LOCAL_ADMIN_PASSWORD \
            "Local infrastructure administrator password (input hidden)" \
            "Confirm local infrastructure administrator password"; then
            warn "The passwords do not match."
            LOCAL_ADMIN_PASSWORD=""
        fi
    done

    unset LOCAL_ADMIN_PASSWORD_ERROR
    info "The installer will align supported local infrastructure superusers after platform deployment."
}

ask_server_exposure() {
    local default_exposure raw_answer normalized
    default_exposure="$(normalize_server_exposure "$1")" || error "Invalid SERVER_EXPOSURE value: $1"

    if [[ "$AUTO_APPROVE" == "true" ]]; then
        echo "$default_exposure"
        return 0
    fi

    while true; do
        read -rp "$(echo -e "${YELLOW}Will this server be internet-exposed or local-only? [internet/local]${NC} ")" raw_answer
        raw_answer="${raw_answer:-$default_exposure}"
        if normalized="$(normalize_server_exposure "$raw_answer")"; then
            echo "$normalized"
            return 0
        fi
        warn "Please answer 'internet' or 'local'."
    done
}

normalize_install_scope() {
    case "${1,,}" in
        1|infra|infrastructure|infra-only)
            printf 'infra\n'
            ;;
        2|apps|all|infra+apps)
            printf 'apps\n'
            ;;
        *)
            return 1
            ;;
    esac
}

select_install_scope() {
    local default_scope raw_answer normalized
    default_scope="$(normalize_install_scope "$1")" || error "INSTALL_SCOPE must be infra or apps."

    if [[ "$AUTO_APPROVE" == "true" ]]; then
        printf '%s\n' "$default_scope"
        return 0
    fi

    printf '%s\n' \
        "Select the deployment scope:" \
        "  1) infra only" \
        "  2) infra + apps" \
        "     - Odoo (ERP/CRM)" >&2
    while true; do
        read -rp "Select 1 or 2 [$([[ "$default_scope" == "infra" ]] && printf 1 || printf 2)]: " raw_answer
        raw_answer="${raw_answer:-$default_scope}"
        if normalized="$(normalize_install_scope "$raw_answer")"; then
            printf '%s\n' "$normalized"
            return 0
        fi
        warn "Enter 1 for infra only or 2 for infra + apps." >&2
    done
}

select_node_transport() {
    local requested_transport="$K3S_NODE_TRANSPORT" selected_transport=""

    if [[ "$AUTO_APPROVE" == "true" && -z "$requested_transport" ]]; then
        if [[ -n "${K3S_WORKER_HOSTS:-}${K3S_CONTROL_PLANE_HOSTS:-}" ]]; then
            requested_transport=tailscale
        elif [[ -n "${K3S_WORKER_IPS:-}${K3S_CONTROL_PLANE_IPS:-}" ]]; then
            requested_transport=vrack
        else
            error "Set K3S_NODE_TRANSPORT=vrack|tailscale|lan when adding nodes non-interactively."
        fi
    fi

    installer_select_node_transport selected_transport "$requested_transport" \
        "${DEFAULT_K3S_NODE_TRANSPORT:-vrack}" "$AUTO_APPROVE" || \
        error "K3S_NODE_TRANSPORT must be vrack, tailscale or lan."
    printf '%s\n' "$selected_transport"
}

configure_platform_component_selection() {
    installer_prompt_section "Platform components" \
        "The recommended bundle installs storage, ingress, secrets, data stores," \
        "platform services, Descheduler resources, and Argo CD."

    if [[ -n "${INSTALL_LONGHORN:-}${INSTALL_INGRESS:-}${INSTALL_VAULT_STACK:-}${DEPLOY_DATA_STORES:-}${DEPLOY_PLATFORM_SERVICES:-}${INSTALL_DESCHEDULER:-}${INSTALL_ARGOCD:-}" ]]; then
        info "Using the supplied component selection; unset components default to installed."
    elif ask_with_default "Use the recommended platform component bundle?" "Y"; then
        INSTALL_LONGHORN=true
        INSTALL_INGRESS=true
        INSTALL_VAULT_STACK=true
        DEPLOY_DATA_STORES=true
        DEPLOY_PLATFORM_SERVICES=true
        INSTALL_DESCHEDULER=true
        INSTALL_ARGOCD=true
        return
    fi

    info "Custom component selection enabled."
    decide INSTALL_LONGHORN "Install/upgrade Longhorn and make it the default storage class?" "Y"
    decide INSTALL_INGRESS "Install/upgrade Traefik ingress controller?" "Y"
    decide INSTALL_VAULT_STACK "Install/upgrade Vault + External Secrets and bootstrap secrets?" "Y"
    decide DEPLOY_DATA_STORES "Deploy/upgrade core data stores (Postgres, Kafka, Redis, MongoDB)?" "Y"
    decide DEPLOY_PLATFORM_SERVICES "Deploy/upgrade platform services from the shared inventory?" "Y"
    decide INSTALL_DESCHEDULER "Install/upgrade Descheduler addon resources (manual trigger only)?" "Y"
    decide INSTALL_ARGOCD "Install/upgrade Argo CD?" "Y"
}

prompt_platform_admin_credentials() {
    while ! sso_admin_validate_username "$KEYCLOAK_SSO_BOOTSTRAP_USERNAME"; do
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            error "Set a valid KEYCLOAK_SSO_BOOTSTRAP_USERNAME for a non-interactive platform installation. $SSO_ADMIN_VALIDATION_ERROR"
        fi
        [[ -z "$KEYCLOAK_SSO_BOOTSTRAP_USERNAME" ]] || warn "$SSO_ADMIN_VALIDATION_ERROR"
        installer_prompt_value KEYCLOAK_SSO_BOOTSTRAP_USERNAME \
            "Platform administrator login (username or email)"
    done

    while ! sso_admin_validate_password "$KEYCLOAK_SSO_BOOTSTRAP_PASSWORD" \
        "$KEYCLOAK_SSO_BOOTSTRAP_USERNAME"; do
        if [[ "$AUTO_APPROVE" == "true" ]]; then
            error "Set a valid KEYCLOAK_SSO_BOOTSTRAP_PASSWORD for a non-interactive platform installation. $SSO_ADMIN_VALIDATION_ERROR"
        fi
        [[ -z "$KEYCLOAK_SSO_BOOTSTRAP_PASSWORD" ]] || warn "$SSO_ADMIN_VALIDATION_ERROR"
        KEYCLOAK_SSO_BOOTSTRAP_PASSWORD=""
        if ! installer_prompt_confirmed_secret KEYCLOAK_SSO_BOOTSTRAP_PASSWORD \
            "Platform administrator password (input hidden)" \
            "Confirm platform administrator password"; then
            warn "The passwords do not match."
            KEYCLOAK_SSO_BOOTSTRAP_PASSWORD=""
        fi
    done

    export KEYCLOAK_SSO_BOOTSTRAP_USERNAME KEYCLOAK_SSO_BOOTSTRAP_PASSWORD
    info "Keycloak will provision '$KEYCLOAK_SSO_BOOTSTRAP_USERNAME' as the shared platform administrator."
}
