#!/usr/bin/env bash
# Public installation identity shared by entrypoints and provisioning helpers.

platform_identity_defaults() {
    local identity_domain="${PLATFORM_DOMAIN:-platform}" identity_project="${GITLAB_PROJECT_PATH:-}"
    if [[ -n "${TLS_SECRET_NAME:-}" && -n "${CLOUDFLARE_TLS_SECRET_NAME:-}" && "$TLS_SECRET_NAME" != "$CLOUDFLARE_TLS_SECRET_NAME" ]]; then
        printf '[ERROR] TLS_SECRET_NAME and CLOUDFLARE_TLS_SECRET_NAME must agree.\n' >&2
        return 1
    fi
    ORGANIZATION_SLUG="${ORGANIZATION_SLUG:-${identity_domain%%.*}}"
    ORGANIZATION_SLUG="${ORGANIZATION_SLUG:-platform}"
    ORGANIZATION_NAME="${ORGANIZATION_NAME:-$ORGANIZATION_SLUG}"
    GITLAB_GROUP_PATH="${GITLAB_GROUP_PATH:-${identity_project%/*}}"
    GITLAB_GROUP_PATH="${GITLAB_GROUP_PATH:-$ORGANIZATION_SLUG}"
    GITLAB_GROUP_NAME="${GITLAB_GROUP_NAME:-$ORGANIZATION_NAME}"
    GITLAB_PROJECT_NAME="${GITLAB_PROJECT_NAME:-${identity_project##*/}}"
    GITLAB_PROJECT_NAME="${GITLAB_PROJECT_NAME:-swirl-cloud}"
    GITLAB_PROJECT_PATH="${GITLAB_PROJECT_PATH:-$GITLAB_GROUP_PATH/$GITLAB_PROJECT_NAME}"
    KEYCLOAK_REALM="${KEYCLOAK_REALM:-$ORGANIZATION_SLUG}"
    TLS_SECRET_NAME="${TLS_SECRET_NAME:-${CLOUDFLARE_TLS_SECRET_NAME:-${identity_domain//./-}-tls}}"
    CLOUDFLARE_TLS_SECRET_NAME="$TLS_SECRET_NAME"
    SONAR_ALM_SETTING="${SONAR_ALM_SETTING:-$ORGANIZATION_SLUG-gitlab}"
    CLOUDFLARE_ACCESS_IDP_NAME="${CLOUDFLARE_ACCESS_IDP_NAME:-$ORGANIZATION_NAME Keycloak}"
    # Installations formatted Kafka with this ID before it was recorded; new ones get
    # their own from platform_identity_new_kafka_cluster_id.
    KAFKA_CLUSTER_ID="${KAFKA_CLUSTER_ID:-8ipNY9RxQtWkattTais5yQ}"
    # The portal follows the installer's language unless a language is recorded or set.
    [[ "${SWIRL_CLOUD_LANG:-}" == fr* ]] && PORTAL_LANGUAGE="${PORTAL_LANGUAGE:-fr}"
    PORTAL_LANGUAGE="${PORTAL_LANGUAGE:-en}"
    # Optional SOC syslog forwarding (docs/guides/soc-forwarding.md); the CA comes
    # from a PEM file and is recorded base64-encoded.
    SOC_SYSLOG_HOST="${SOC_SYSLOG_HOST:-}"
    if [[ -n "$SOC_SYSLOG_HOST" ]]; then
        SOC_SYSLOG_PORT="${SOC_SYSLOG_PORT:-6514}"
        SOC_SYSLOG_SOURCES="${SOC_SYSLOG_SOURCES:-identity,kubernetes-audit,vault-audit,detection}"
    fi
    SOC_SYSLOG_PORT="${SOC_SYSLOG_PORT:-}" SOC_SYSLOG_SOURCES="${SOC_SYSLOG_SOURCES:-}"
    if [[ -n "${SOC_SYSLOG_CA_FILE:-}" ]]; then
        SOC_SYSLOG_CA="$(base64 -w0 < "$SOC_SYSLOG_CA_FILE")" || {
            printf '[ERROR] Cannot read SOC_SYSLOG_CA_FILE %s.\n' "$SOC_SYSLOG_CA_FILE" >&2; return 1;
        }
    fi
    SOC_SYSLOG_CA="${SOC_SYSLOG_CA:-}"
    export ORGANIZATION_NAME ORGANIZATION_SLUG GITLAB_GROUP_PATH GITLAB_GROUP_NAME
    export GITLAB_PROJECT_NAME GITLAB_PROJECT_PATH KEYCLOAK_REALM TLS_SECRET_NAME
    export CLOUDFLARE_TLS_SECRET_NAME SONAR_ALM_SETTING CLOUDFLARE_ACCESS_IDP_NAME KAFKA_CLUSTER_ID
    export PORTAL_LANGUAGE SOC_SYSLOG_HOST SOC_SYSLOG_PORT SOC_SYSLOG_SOURCES SOC_SYSLOG_CA
}

# A cluster without Kafka storage gets a new random Kafka cluster ID. Storage that
# exists, or that cannot be checked, keeps the recorded or legacy ID: Kafka refuses
# to start on data formatted with another ID.
platform_identity_new_kafka_cluster_id() {
    local claims
    [[ -z "${KAFKA_CLUSTER_ID:-}" ]] || return 0
    if command -v kubectl >/dev/null 2>&1; then
        claims="$(kubectl --request-timeout=10s get persistentvolumeclaims -n infra -o name 2>&1)" || {
            [[ "$claims" == *"localhost:8080"* || "$claims" == *"no configuration has been provided"* ]] || return 0
            claims=""
        }
        ! grep -Eq '^persistentvolumeclaim/(kafka-data-kafka|controller-data-kafka-controller)-[0-9]+$' <<< "$claims" || return 0
    fi
    KAFKA_CLUSTER_ID="$(python3 -c 'import base64, uuid; print(base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("="))')"
    export KAFKA_CLUSTER_ID
}

platform_identity_validate() {
    local name value
    value="$ORGANIZATION_SLUG"
    [[ "$value" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ && ${#value} -le 63 ]] || {
        printf '[ERROR] ORGANIZATION_SLUG must be a lowercase DNS label (maximum 63 characters).\n' >&2; return 1;
    }
    for name in ORGANIZATION_NAME GITLAB_GROUP_NAME CLOUDFLARE_ACCESS_IDP_NAME; do
        value="${!name}"
        [[ "$value" =~ [^[:space:]] && ${#value} -le 160 && ! "$value" =~ [[:cntrl:]] ]] || {
            printf '[ERROR] %s must contain 1–160 printable characters.\n' "$name" >&2; return 1;
        }
    done
    [[ "$GITLAB_GROUP_PATH" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$ &&
       "$GITLAB_PROJECT_NAME" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*$ &&
       "$GITLAB_PROJECT_PATH" == "$GITLAB_GROUP_PATH/$GITLAB_PROJECT_NAME" ]] || {
        printf '[ERROR] GitLab group, project name and project path must identify the same repository.\n' >&2; return 1;
    }
    [[ "$KEYCLOAK_REALM" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*$ && "$KEYCLOAK_REALM" != master &&
       "$SONAR_ALM_SETTING" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*$ ]] || {
        printf '[ERROR] Set a valid application KEYCLOAK_REALM and SONAR_ALM_SETTING (master is reserved).\n' >&2; return 1;
    }
    [[ "$TLS_SECRET_NAME" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && ${#TLS_SECRET_NAME} -le 253 ]] || {
        printf '[ERROR] TLS_SECRET_NAME must be a Kubernetes DNS subdomain.\n' >&2; return 1;
    }
    [[ -z "${CLOUDFLARE_TLS_SECRET_NAME:-}" || "$CLOUDFLARE_TLS_SECRET_NAME" == "$TLS_SECRET_NAME" ]] || return 1
    [[ "$KAFKA_CLUSTER_ID" =~ ^[A-Za-z0-9_-]{22}$ ]] || {
        printf '[ERROR] KAFKA_CLUSTER_ID must be a 22-character Kafka cluster ID.\n' >&2; return 1;
    }
    [[ "$PORTAL_LANGUAGE" == en || "$PORTAL_LANGUAGE" == fr ]] || {
        printf '[ERROR] PORTAL_LANGUAGE must be en or fr.\n' >&2; return 1;
    }
    [[ -z "$SOC_SYSLOG_HOST" ]] && return 0
    [[ ${#SOC_SYSLOG_HOST} -le 253 && "$SOC_SYSLOG_HOST" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || {
        printf '[ERROR] SOC_SYSLOG_HOST must be a host name or IPv4 address.\n' >&2; return 1;
    }
    if [[ ! "$SOC_SYSLOG_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || (( SOC_SYSLOG_PORT > 65535 )); then
        printf '[ERROR] SOC_SYSLOG_PORT must be a TCP port.\n' >&2; return 1
    fi
    [[ "$SOC_SYSLOG_SOURCES" =~ ^(identity|kubernetes-audit|vault-audit|detection|host-journal)(,(identity|kubernetes-audit|vault-audit|detection|host-journal))*$ ]] || {
        printf '[ERROR] SOC_SYSLOG_SOURCES must list identity, kubernetes-audit, vault-audit, detection or host-journal, separated by commas.\n' >&2
        return 1
    }
    [[ -z "$SOC_SYSLOG_CA" ]] || base64 -d <<< "$SOC_SYSLOG_CA" 2>/dev/null | grep -q -- '-----BEGIN CERTIFICATE-----' || {
        printf '[ERROR] SOC_SYSLOG_CA_FILE must hold a PEM certificate.\n' >&2; return 1;
    }
}

platform_identity_load() {
    # Installed choices come from a public ConfigMap; explicit environment inputs
    # win. Values are decoded as data, never sourced or eval'd.
    local name value identity_script identity_json
    TLS_SECRET_NAME="${TLS_SECRET_NAME:-${CLOUDFLARE_TLS_SECRET_NAME:-}}"
    identity_script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/platform-identity.py"
    if command -v python3 >/dev/null 2>&1 && command -v kubectl >/dev/null 2>&1; then
        identity_json="$(python3 "$identity_script" --discover)" || return 1
        while IFS= read -r -d '' name && IFS= read -r -d '' value; do
            if [[ -z "${!name:-}" ]]; then
                printf -v "$name" '%s' "$value"
                export "${name?}"
            fi
        done < <(python3 "$identity_script" --json "$identity_json" --pairs)
    fi
}
