#!/usr/bin/env bash
# Cloudflare Access with Keycloak OIDC for configure-cloudflare.sh.
# Callers also source lib/cloudflare-api.sh.

access_app_name() {
    case "$1" in
        argocd) echo "Argo CD" ;;
        cloud) echo "Portal" ;;
        dbgate) echo "DBGate" ;;
        grafana) echo "Grafana" ;;
        kafka) echo "Kafka UI" ;;
        kibana) echo "Kibana" ;;
        longhorn) echo "Longhorn" ;;
        portainer) echo "Portainer" ;;
        sonarqube) echo "SonarQube" ;;
        vault) echo "Vault" ;;
        *) echo "$1" ;;
    esac
}

configure_access() {
    local organization_response organization_file organization_auth_domain callback_url
    local idp_response idp_count oidc_idp_id oidc_client_secret idp_auth_url idp_matches
    local idp_file policy_response policy_count policy_id policy_file update_response
    local access_emails_json app_response app_count app_id app_file app_name fqdn label
    local current_fingerprint desired_fingerprint
    local configured_labels=" ${HOST_LABELS[*]} "

    [[ "$ENABLE_ACCESS" == "true" ]] || return 0
    step "Reconciling Zero Trust Access for Swirl Cloud admin interfaces"

    oidc_client_secret="$ACCESS_OIDC_CLIENT_SECRET"
    if [[ -z "$oidc_client_secret" ]]; then
        oidc_client_secret="$(kubectl get secret keycloak-sso-credentials -n "$INGRESS_NAMESPACE" \
            -o jsonpath='{.data.CLOUDFLARE_ACCESS_CLIENT_SECRET}' 2>/dev/null | base64 -d)"
    fi
    [[ -n "$oidc_client_secret" ]] || \
        error "Keycloak SSO credentials are not ready; apply platform/keycloak-sso.yaml before enabling Cloudflare Access."

    organization_response="$(cf_request GET "/accounts/$ACCOUNT_ID/access/organizations")"
    if ! jq -e '.success == true' <<< "$organization_response" >/dev/null 2>&1; then
        organization_file="$WORK_DIR/access-organization.json"
        jq -n \
            --arg name "Swirl Cloud" \
            --arg auth_domain "$ACCESS_TEAM_NAME.cloudflareaccess.com" \
            '{name:$name,auth_domain:$auth_domain,auto_redirect_to_identity:true,deny_unmatched_requests:false}' \
            > "$organization_file"
        organization_response="$(cf_request POST "/accounts/$ACCOUNT_ID/access/organizations" "$organization_file")"
        require_success "$organization_response" "Creating the Cloudflare Zero Trust organization (set CLOUDFLARE_ACCESS_TEAM_NAME if its name is unavailable)"
        info "Created the Cloudflare Zero Trust organization."
    else
        info "Using the existing Cloudflare Zero Trust organization without changing its account-wide settings."
    fi

    if ! jq -e '.result.auto_redirect_to_identity == true' <<< "$organization_response" >/dev/null; then
        organization_file="$WORK_DIR/access-organization-redirect.json"
        jq '{
                auth_domain:.result.auth_domain,
                name:.result.name,
                auto_redirect_to_identity:true,
                deny_unmatched_requests:(.result.deny_unmatched_requests // false)
            }' <<< "$organization_response" > "$organization_file"
        organization_response="$(cf_request PUT "/accounts/$ACCOUNT_ID/access/organizations" "$organization_file")"
        require_success "$organization_response" "Enabling automatic redirect to the Keycloak identity provider"
        info "Enabled automatic redirect from Cloudflare Access to Keycloak."
    fi

    organization_auth_domain="$(jq -r '.result.auth_domain // empty' <<< "$organization_response")"
    [[ -n "$organization_auth_domain" ]] || error "Cloudflare returned no Zero Trust organization auth domain."
    if [[ "$organization_auth_domain" != "$ACCESS_TEAM_NAME.cloudflareaccess.com" ]]; then
        if [[ "$ACCESS_TEAM_NAME_EXPLICIT" == "true" ]]; then
            error "The live Cloudflare Access team is $organization_auth_domain; set CLOUDFLARE_ACCESS_TEAM_NAME to its first label."
        fi
        ACCESS_TEAM_NAME="${organization_auth_domain%.cloudflareaccess.com}"
        info "Discovered the existing Cloudflare Access team: $ACCESS_TEAM_NAME"
    fi
    callback_url="https://$organization_auth_domain/cdn-cgi/access/callback"
    info "Using the Keycloak callback registered for $callback_url."

    idp_response="$(cf_request GET "/accounts/$ACCOUNT_ID/access/identity_providers")"
    require_success "$idp_response" "Reading Access identity providers"
    idp_auth_url="https://keycloak.$ZONE_NAME/auth/realms/$KEYCLOAK_REALM/protocol/openid-connect/auth"
    # Match on the Keycloak client and realm too, so a renamed provider keeps
    # its ID and existing Access links.
    idp_matches="$(jq --arg name "$CLOUDFLARE_ACCESS_IDP_NAME" --arg auth_url "$idp_auth_url" '
        [.result[] | select(.name == $name or
            (.type == "oidc" and .config.client_id == "cloudflare-access" and .config.auth_url == $auth_url))]
        ' <<< "$idp_response")"
    idp_count="$(jq 'length' <<< "$idp_matches")"
    ((idp_count <= 1)) || error "More than one Access identity provider matches the managed Keycloak client or display name."
    if ((idp_count == 1)); then
        jq -e --arg auth_url "$idp_auth_url" \
            '.[0].type == "oidc" and .[0].config.client_id == "cloudflare-access" and .[0].config.auth_url == $auth_url' \
            <<< "$idp_matches" >/dev/null || \
            error "An unrelated Access identity provider already uses the requested display name."
        oidc_idp_id="$(jq -er '.[0].id' <<< "$idp_matches")"
    fi
    idp_file="$WORK_DIR/access-keycloak-idp.json"
    jq -n \
        --arg client_secret "$oidc_client_secret" \
        --arg domain "$ZONE_NAME" \
        --arg realm "$KEYCLOAK_REALM" \
        --arg name "$CLOUDFLARE_ACCESS_IDP_NAME" \
        '{
            name:$name,
            type:"oidc",
            config:{
                client_id:"cloudflare-access",
                client_secret:$client_secret,
                auth_url:("https://keycloak." + $domain + "/auth/realms/" + $realm + "/protocol/openid-connect/auth"),
                token_url:("https://keycloak." + $domain + "/auth/realms/" + $realm + "/protocol/openid-connect/token"),
                certs_url:("https://keycloak." + $domain + "/auth/realms/" + $realm + "/protocol/openid-connect/certs"),
                pkce_enabled:true,
                email_claim_name:"email",
                claims:["groups", "preferred_username"],
                scopes:["openid", "email", "profile", "groups"]
            }
        }' > "$idp_file"
    if ((idp_count == 0)); then
        update_response="$(cf_request POST "/accounts/$ACCOUNT_ID/access/identity_providers" "$idp_file")"
        require_success "$update_response" "Creating the Access Keycloak identity provider"
        oidc_idp_id="$(jq -r '.result.id' <<< "$update_response")"
        info "Created the Keycloak OIDC identity provider for Cloudflare Access."
    else
        update_response="$(cf_request PUT "/accounts/$ACCOUNT_ID/access/identity_providers/$oidc_idp_id" "$idp_file")"
        require_success "$update_response" "Updating the Access Keycloak identity provider"
        info "Reconciled the Keycloak OIDC identity provider for Cloudflare Access."
    fi
    unset oidc_client_secret

    access_emails_json="$(printf '%s\n' "${ACCESS_EMAILS[@]}" | jq -R . | jq -s .)"
    policy_file="$WORK_DIR/access-policy.json"
    jq -n \
        --arg name "Swirl Cloud administrators" \
        --arg duration "$ACCESS_SESSION_DURATION" \
        --arg idp_id "$oidc_idp_id" \
        --argjson emails "$access_emails_json" \
        '{
            name:$name,
            decision:"allow",
            session_duration:$duration,
            include:($emails | map({email:{email:.}})),
            exclude:[],
            require:[{login_method:{id:$idp_id}}]
        }' > "$policy_file"

    policy_response="$(cf_request GET "/accounts/$ACCOUNT_ID/access/policies?per_page=100")"
    require_success "$policy_response" "Reading reusable Access policies"
    policy_count="$(jq '[.result[] | select(.name == "Swirl Cloud administrators" and .reusable == true)] | length' <<< "$policy_response")"
    ((policy_count <= 1)) || error "More than one reusable Access policy is named Swirl Cloud administrators."
    if ((policy_count == 0)); then
        update_response="$(cf_request POST "/accounts/$ACCOUNT_ID/access/policies" "$policy_file")"
        require_success "$update_response" "Creating the Swirl Cloud Access policy"
        policy_id="$(jq -r '.result.id' <<< "$update_response")"
        info "Created the Swirl Cloud email allowlist policy."
    else
        policy_id="$(jq -r '.result[] | select(.name == "Swirl Cloud administrators" and .reusable == true) | .id' <<< "$policy_response")"
        current_fingerprint="$(jq -Sc \
            '.result[] | select(.name == "Swirl Cloud administrators" and .reusable == true) |
             {name,decision,session_duration,include,exclude,require}' <<< "$policy_response")"
        desired_fingerprint="$(jq -Sc \
            '{name,decision,session_duration,include,exclude,require}' "$policy_file")"
        if [[ "$current_fingerprint" == "$desired_fingerprint" ]]; then
            info "The Swirl Cloud email allowlist policy is already correct."
        else
            update_response="$(cf_request PUT "/accounts/$ACCOUNT_ID/access/policies/$policy_id" "$policy_file")"
            require_success "$update_response" "Updating the Swirl Cloud Access policy"
            info "Reconciled the Swirl Cloud email allowlist policy."
        fi
    fi

    if [[ -n "${CLOUDFLARE_ACCESS_HOST_LABELS:-}" ]]; then
        read -r -a ACCESS_HOST_LABELS <<< "$CLOUDFLARE_ACCESS_HOST_LABELS"
    else
        ACCESS_HOST_LABELS=("${DEFAULT_ACCESS_HOST_LABELS[@]}")
    fi
    (( ${#ACCESS_HOST_LABELS[@]} > 0 )) || error "At least one Access hostname label is required."

    for label in "${ACCESS_HOST_LABELS[@]}"; do
        [[ "$label" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || error "Invalid Access hostname label: $label"
        [[ "$configured_labels" == *" $label "* ]] || error "Access hostname $label is not present in CLOUDFLARE_HOST_LABELS."
        fqdn="$label.$ZONE_NAME"
        app_name="Swirl Cloud - $(access_app_name "$label")"
        app_file="$WORK_DIR/access-app-$label.json"
        jq -n \
            --arg name "$app_name" \
            --arg domain "$fqdn" \
            --arg duration "$ACCESS_SESSION_DURATION" \
            --arg idp_id "$oidc_idp_id" \
            --arg policy_id "$policy_id" \
            '{
                name:$name,
                domain:$domain,
                type:"self_hosted",
                session_duration:$duration,
                app_launcher_visible:true,
                allowed_idps:[$idp_id],
                auto_redirect_to_identity:true,
                http_only_cookie_attribute:true,
                policies:[{id:$policy_id,precedence:1}]
            }' > "$app_file"

        app_response="$(cf_request GET "/accounts/$ACCOUNT_ID/access/apps?per_page=100")"
        require_success "$app_response" "Reading Access applications"
        app_count="$(jq --arg domain "$fqdn" '[.result[] | select(.domain == $domain and .type == "self_hosted")] | length' <<< "$app_response")"
        ((app_count <= 1)) || error "More than one Access application targets $fqdn."
        if ((app_count == 0)); then
            update_response="$(cf_request POST "/accounts/$ACCOUNT_ID/access/apps" "$app_file")"
            require_success "$update_response" "Creating the Access application for $fqdn"
            info "Protected $fqdn with Keycloak-backed Access."
        else
            app_id="$(jq -r --arg domain "$fqdn" '.result[] | select(.domain == $domain and .type == "self_hosted") | .id' <<< "$app_response")"
            current_fingerprint="$(jq -Sc --arg domain "$fqdn" \
                '.result[] | select(.domain == $domain and .type == "self_hosted") |
                 {name,domain,type,session_duration,app_launcher_visible,allowed_idps,
                  auto_redirect_to_identity,http_only_cookie_attribute,
                  policies:(.policies | map({id,precedence}))}' <<< "$app_response")"
            desired_fingerprint="$(jq -Sc \
                '{name,domain,type,session_duration,app_launcher_visible,allowed_idps,
                 auto_redirect_to_identity,http_only_cookie_attribute,policies}' "$app_file")"
            if [[ "$current_fingerprint" == "$desired_fingerprint" ]]; then
                info "Access protection is already correct for $fqdn."
            else
                update_response="$(cf_request PUT "/accounts/$ACCOUNT_ID/access/apps/$app_id" "$app_file")"
                require_success "$update_response" "Updating the Access application for $fqdn"
                info "Reconciled Access protection for $fqdn."
            fi
        fi
    done
}
