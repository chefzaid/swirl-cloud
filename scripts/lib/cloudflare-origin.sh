#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2154 # Status and API responses are shared with configure-cloudflare.sh.
# Origin lock, ingress proxy trust, origin certificate and registrar/DNSSEC
# checks for configure-cloudflare.sh, which provides logging and the zone and
# ingress globals.

append_unique_namespace() {
    local candidate="$1"
    local existing

    [[ -n "$candidate" ]] || return 0
    for existing in "${TLS_NAMESPACES[@]}"; do
        [[ "$existing" == "$candidate" ]] && return 0
    done
    TLS_NAMESPACES+=("$candidate")
}

verify_ingress_proxy_trust() {
    local cidr_csv="$1" workloads
    workloads="$(kubectl get deployment,daemonset -n "$INGRESS_NAMESPACE" -l app.kubernetes.io/name=traefik -o json)"
    jq -e 'type == "object" and (.items | type) == "array"' <<< "$workloads" >/dev/null || \
        error "Kubernetes returned an invalid Traefik workload inventory."
    if [[ "$(jq '.items | length' <<< "$workloads")" == 0 ]]; then
        warn "Traefik is not installed yet; configure-ingress.sh must configure edge trust before serving traffic."
        return 0
    fi
    if ! jq -e --arg cidrs "$cidr_csv" '
      ($cidrs | split(",") | map(ascii_downcase) | sort) as $expected |
      all(.items[];
        (.spec.template.spec.containers | type) == "array" and
        ([.spec.template.spec.containers[] | select(.name == "traefik")] | length) == 1 and
        all(.spec.template.spec.containers[] | select(.name == "traefik");
          (.args | type) == "array" and all(.args[]; type == "string") and
          ((.args | map(ascii_downcase)) as $args |
            all(["web", "websecure"][];
              . as $entry |
              [$args[] | select(startswith("--entrypoints." + $entry + ".forwardedheaders.trustedips="))] as $trust |
              ($trust | length) == 1 and ($trust[0] | split("=")[1] | split(",") | sort) == $expected) and
            all($args[];
              (test("^--entrypoints\\.[^.]+\\.forwardedheaders\\.insecure(=|$)") | not) or endswith("=false")))))
      ' <<< "$workloads" >/dev/null; then
        error "Traefik edge trust differs from the current Cloudflare networks; reconcile ingress before publishing DNS."
    fi
    info "Verified ingress forwarded-header trust against the current edge inventory."
}

lock_origin_firewall() {
    local cidr
    local previous_cidr
    local sudo_command=()

    if [[ "$INGRESS_MODE" == tunnel ]]; then
        ORIGIN_LOCK_STATUS="outbound tunnel; no public ingress listener"
        return 0
    fi

    if [[ "$LOCK_ORIGIN" != "true" ]]; then
        ORIGIN_LOCK_STATUS="left unchanged"
        warn "Origin firewall locking is disabled by CLOUDFLARE_LOCK_ORIGIN=false."
        return 0
    fi
    if ! command -v ufw >/dev/null 2>&1; then
        ORIGIN_LOCK_STATUS="provider firewall required"
        warn "UFW is not installed; restrict origin ports 80/443 to Cloudflare networks in the provider firewall."
        return 0
    fi
    if [[ $EUID -ne 0 ]]; then
        if ! command -v sudo >/dev/null 2>&1; then
            ORIGIN_LOCK_STATUS="provider firewall required"
            warn "sudo is unavailable; restrict origin ports 80/443 to Cloudflare networks in the provider firewall."
            return 0
        fi
        sudo_command=(sudo)
    fi
    if ! "${sudo_command[@]}" ufw status | grep -q '^Status: active'; then
        ORIGIN_LOCK_STATUS="provider firewall required"
        warn "UFW is inactive; restrict origin ports 80/443 to Cloudflare networks in the provider firewall."
        return 0
    fi

    step "Restricting the origin web ports to Cloudflare proxies"
    for cidr in "${CLOUDFLARE_PROXY_CIDRS[@]}"; do
        "${sudo_command[@]}" ufw allow from "$cidr" to any port 80 proto tcp comment 'Cloudflare proxy' >/dev/null
        "${sudo_command[@]}" ufw allow from "$cidr" to any port 443 proto tcp comment 'Cloudflare proxy' >/dev/null
    done
    if "${sudo_command[@]}" test -f /etc/swirl-cloud/cloudflare-proxy-only; then
        while IFS= read -r previous_cidr; do
            [[ "$previous_cidr" =~ ^[0-9A-Fa-f:.]+/[0-9]{1,3}$ ]] || continue
            if [[ ",${cloudflare_proxy_cidr_csv}," != *",${previous_cidr},"* ]]; then
                "${sudo_command[@]}" ufw --force delete allow from "$previous_cidr" to any port 80 proto tcp >/dev/null 2>&1 || true
                "${sudo_command[@]}" ufw --force delete allow from "$previous_cidr" to any port 443 proto tcp >/dev/null 2>&1 || true
            fi
        done < <("${sudo_command[@]}" cat /etc/swirl-cloud/cloudflare-proxy-only)
    fi
    "${sudo_command[@]}" ufw --force delete allow 80/tcp >/dev/null 2>&1 || true
    "${sudo_command[@]}" ufw --force delete allow 443/tcp >/dev/null 2>&1 || true
    "${sudo_command[@]}" ufw --force delete allow 'Nginx Full' >/dev/null 2>&1 || true
    "${sudo_command[@]}" install -d -m 0755 /etc/swirl-cloud
    printf '%s\n' '# Managed by swirl-cloud: web ingress is restricted to these Cloudflare proxy networks.' "${CLOUDFLARE_PROXY_CIDRS[@]}" | \
        "${sudo_command[@]}" tee /etc/swirl-cloud/cloudflare-proxy-only >/dev/null
    ORIGIN_LOCK_STATUS="Cloudflare proxy networks only"
    info "Direct Internet access to origin ports 80/443 is now blocked."
}

certificate_is_usable() {
    local cert_file="$1"
    local key_file="$2"
    local cert_public_key key_public_key san issuer

    [[ -s "$cert_file" && -s "$key_file" ]] || return 1
    openssl x509 -in "$cert_file" -noout -checkend 2592000 >/dev/null 2>&1 || return 1
    issuer="$(openssl x509 -in "$cert_file" -noout -issuer 2>/dev/null)"
    [[ "$issuer" == *"CloudFlare Origin SSL Certificate Authority"* ]] || return 1
    san="$(openssl x509 -in "$cert_file" -noout -ext subjectAltName 2>/dev/null)"
    grep -Fq "DNS:*.$ZONE_NAME" <<< "$san" || return 1
    grep -Fq "DNS:$ZONE_NAME" <<< "$san" || return 1
    cert_public_key="$(openssl x509 -in "$cert_file" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | openssl sha256)"
    key_public_key="$(openssl pkey -in "$key_file" -pubout -outform DER 2>/dev/null | openssl sha256)"
    [[ -n "$cert_public_key" && "$cert_public_key" == "$key_public_key" ]]
}

verify_registrar_and_dnssec() {
    local rdap_response registrar_name desired_nameservers public_nameservers
    local expected_ds_record expected_ds public_ds

    rdap_response="$(curl --fail --location --silent --show-error \
        --connect-timeout 10 --max-time 30 "https://rdap.org/domain/$ZONE_NAME" 2>/dev/null || true)"
    registrar_name="$(jq -r '
        .entities[]?
        | select(any(.roles[]?; . == "registrar"))
        | .vcardArray[1][]?
        | select(.[0] == "fn")
        | .[3]
    ' <<< "$rdap_response" 2>/dev/null | head -n 1)"
    desired_nameservers="$(printf '%s\n' "${ZONE_NAMESERVERS[@]}" | sed 's/\.$//; s/.*/\L&/' | sort -u)"
    public_nameservers="$(dig +short NS "$ZONE_NAME" | sed 's/\.$//; s/.*/\L&/' | sort -u)"

    if [[ "$public_nameservers" != "$desired_nameservers" ]]; then
        warn "Registrar delegation is not yet reconciled${registrar_name:+ ($registrar_name)}."
        warn "Replace the registrar's current nameservers with exactly:"
        printf '  - %s\n' "${ZONE_NAMESERVERS[@]}" >&2
        if [[ "${registrar_name,,}" == *cloudflare* ]]; then
            error "A Cloudflare Registrar domain should use these nameservers automatically; resolve the account/zone ownership mismatch and rerun."
        fi
        [[ -t 0 ]] || error "Registrar nameserver delegation requires interactive completion."
        read -rp "Press Enter after saving the nameservers at the registrar, or Ctrl-C to stop: "
        public_nameservers="$(dig +short NS "$ZONE_NAME" | sed 's/\.$//; s/.*/\L&/' | sort -u)"
        [[ "$public_nameservers" == "$desired_nameservers" ]] || \
            error "Public nameserver delegation has not propagated yet. Rerun this script after it does."
    fi
    info "Registrar nameserver delegation is correct${registrar_name:+ ($registrar_name)}."

    [[ "$ENABLE_DNSSEC" == "true" ]] || return 0
    expected_ds_record="$(jq -r '.result.ds // empty' <<< "$dnssec_response")"
    expected_ds="$(awk '
        {
            start = 1
            for (field = 1; field <= NF; field++) {
                if (toupper($field) == "DS") {
                    start = field + 1
                    break
                }
            }
            digest = ""
            for (field = start + 3; field <= NF; field++) digest = digest $field
            print $(start) " " $(start + 1) " " $(start + 2) " " toupper(digest)
        }
    ' <<< "$expected_ds_record")"
    [[ -n "$expected_ds" ]] || error "Cloudflare did not return the DNSSEC DS record."
    public_ds="$(dig +short DS "$ZONE_NAME" | awk '
        {
            digest = ""
            for (field = 4; field <= NF; field++) digest = digest $field
            print $1 " " $2 " " $3 " " toupper(digest)
        }
    ' | sort -u)"
    if ! grep -Fxq "$expected_ds" <<< "$public_ds"; then
        warn "Publish this DS record in the registrar's DNSSEC/DS section:"
        printf '  %s\n' "$expected_ds_record" >&2
        if [[ "${registrar_name,,}" == *cloudflare* ]]; then
            error "Cloudflare Registrar has not published the DNSSEC record yet; rerun after propagation."
        fi
        [[ -t 0 ]] || error "Registrar DNSSEC publication requires interactive completion."
        read -rp "Press Enter after saving the DS record at the registrar, or Ctrl-C to stop: "
        public_ds="$(dig +short DS "$ZONE_NAME" | awk '
            {
                digest = ""
                for (field = 4; field <= NF; field++) digest = digest $field
                print $1 " " $2 " " $3 " " toupper(digest)
            }
        ' | sort -u)"
        grep -Fxq "$expected_ds" <<< "$public_ds" || \
            error "The public DS record has not propagated yet. Rerun this script after it does."
    fi
    info "Public DNSSEC delegation matches Cloudflare."
}
