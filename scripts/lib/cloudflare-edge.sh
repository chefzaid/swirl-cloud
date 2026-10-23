#!/usr/bin/env bash
# shellcheck disable=SC2034 # REGISTRY_BOT_STATUS is reported by configure-cloudflare.sh.
# Cloudflare WAF, rate-limit, cache and registry-client rules for
# configure-cloudflare.sh. Callers also source lib/cloudflare-api.sh.

configure_registry_bot_compatibility() {
    local registry_host="${DEFAULT_K3S_REGISTRY_HOST:-registry.$ZONE_NAME}"
    local registry_is_published=false
    local bot_response bot_setting_file update_response
    local fight_mode=false
    local sbfm_active=false
    local skip_rule_file skip_expression
    local fqdn

    if [[ "$ENABLE_REGISTRY_API_COMPATIBILITY" != "true" ]]; then
        REGISTRY_BOT_STATUS="left unchanged"
        warn "Registry bot compatibility reconciliation is disabled."
        return 0
    fi

    for fqdn in "${DNS_HOSTS[@]}"; do
        if [[ "$fqdn" == "$registry_host" ]]; then
            registry_is_published=true
            break
        fi
    done
    if [[ "$registry_is_published" != "true" ]]; then
        REGISTRY_BOT_STATUS="not applicable"
        return 0
    fi

    step "Reconciling Cloudflare bot protection for Registry API clients"
    bot_response="$(cf_request GET "/zones/$ZONE_ID/bot_management")"
    if ! jq -e '.success == true' <<< "$bot_response" >/dev/null 2>&1; then
        if jq -e 'any(.errors[]?; .code == 10000)' <<< "$bot_response" >/dev/null 2>&1; then
            error "Reading Bot Management failed. Add Zone -> Bot Management -> Read and Edit to the Cloudflare token; Docker clients cannot complete browser challenges."
        fi
        require_success "$bot_response" "Reading Bot Management configuration"
    fi

    fight_mode="$(jq -r '(.result.fight_mode // .result.stale_zone_configuration.fight_mode // false)' <<< "$bot_response")"
    if [[ "$fight_mode" == "true" ]]; then
        # Basic Bot Fight Mode is zone-wide with no hostname exception. DDoS
        # protection, custom WAF rules and rate limits stay on.
        bot_setting_file="$WORK_DIR/registry-bot-management.json"
        jq -n '{fight_mode:false}' > "$bot_setting_file"
        update_response="$(cf_request PUT "/zones/$ZONE_ID/bot_management" "$bot_setting_file")"
        require_success "$update_response" "Disabling basic Bot Fight Mode for Registry API compatibility"
        info "Disabled basic Bot Fight Mode; it cannot exempt Docker Registry clients by hostname."
    fi

    if jq -e '.result |
        ((.sbfm_definitely_automated? // "allow") != "allow") or
        ((.sbfm_likely_automated? // "allow") != "allow") or
        ((.sbfm_verified_bots? // "allow") != "allow") or
        (.sbfm_static_resource_protection? == true)' <<< "$bot_response" >/dev/null; then
        sbfm_active=true
    fi

    if [[ "$sbfm_active" == "true" ]]; then
        # Super Bot Fight Mode allows a per-hostname skip rule, so browser
        # services stay protected.
        skip_rule_file="$WORK_DIR/rule-registry-sbfm-skip.json"
        skip_expression="(http.host eq \"$registry_host\")"
        jq -n \
            --arg expression "$skip_expression" \
            '{
                action:"skip",
                action_parameters:{phases:["http_request_sbfm"]},
                description:"swirl-cloud: allow non-browser Registry clients",
                enabled:true,
                expression:$expression,
                logging:{enabled:true}
            }' > "$skip_rule_file"
        reconcile_ruleset_rule \
            http_request_firewall_custom \
            "Swirl Cloud custom firewall rules" \
            "swirl-cloud: allow non-browser Registry clients" \
            "$skip_rule_file"
        REGISTRY_BOT_STATUS="Super Bot Fight Mode skipped for $registry_host"
    elif [[ "$fight_mode" == "true" ]]; then
        REGISTRY_BOT_STATUS="basic Bot Fight Mode disabled"
    else
        REGISTRY_BOT_STATUS="compatible"
        info "Cloudflare bot protection already permits non-browser Registry clients."
    fi

    bot_response="$(cf_request GET "/zones/$ZONE_ID/bot_management")"
    require_success "$bot_response" "Verifying Bot Management configuration"
    jq -e '(.result.fight_mode // .result.stale_zone_configuration.fight_mode // false) == false' \
        <<< "$bot_response" >/dev/null || error "Basic Bot Fight Mode still challenges Registry API clients."
}

configure_edge_rules() {
    local host_set=""
    local fqdn host_literal waf_expression cache_expression
    local rule_file

    for fqdn in "${DNS_HOSTS[@]}"; do
        printf -v host_literal '"%s"' "$fqdn"
        host_set+="${host_set:+ }$host_literal"
    done

    step "Reconciling trusted client addresses at the Cloudflare edge"
    # Cloudflare rebuilds removed X-Forwarded-For from the visitor connection.
    rule_file="$WORK_DIR/rule-client-ip.json"
    jq -n --arg expression "(http.host in {$host_set})" '{
        action:"rewrite",
        action_parameters:{headers:{"x-forwarded-for":{operation:"remove"}}},
        description:"swirl-cloud: discard client-supplied forwarding chains",
        enabled:true,
        expression:$expression
    }' > "$rule_file"
    reconcile_ruleset_rule \
        http_request_late_transform \
        "Swirl Cloud request header rules" \
        "swirl-cloud: discard client-supplied forwarding chains" \
        "$rule_file"

    if [[ "$ENABLE_WAF" == "true" ]]; then
        step "Reconciling the Swirl Cloud custom WAF rule"
        waf_expression="(http.host in {$host_set} and ((http.request.method in {\"TRACE\" \"TRACK\"}) or (http.request.uri.path in {\"/.env\" \"/.git/config\" \"/.git/HEAD\" \"/wp-login.php\" \"/xmlrpc.php\" \"/server-status\" \"/actuator/env\" \"/actuator/heapdump\" \"/actuator/configprops\"}) or starts_with(http.request.uri.path, \"/phpmyadmin\") or starts_with(http.request.uri.path, \"/vendor/phpunit/\")))"
        rule_file="$WORK_DIR/rule-waf.json"
        jq -n \
            --arg expression "$waf_expression" \
            '{action:"block",description:"swirl-cloud: block unsafe methods and scanner targets",enabled:true,expression:$expression}' \
            > "$rule_file"
        reconcile_ruleset_rule \
            http_request_firewall_custom \
            "Swirl Cloud custom firewall rules" \
            "swirl-cloud: block unsafe methods and scanner targets" \
            "$rule_file"
    fi

    if [[ "$ENABLE_RATE_LIMIT" == "true" ]]; then
        step "Reconciling the Swirl Cloud login rate limit"
        rule_file="$WORK_DIR/rule-rate-limit.json"
        # One rule fits every Cloudflare plan; the description is its identity.
        # Odoo Live Chat session starts are public and each one writes records.
        jq -n '{
            action:"block",
            description:"swirl-cloud: rate limit interactive login endpoints",
            enabled:true,
            expression:"(http.request.uri.path in {\"/web/login\" \"/users/sign_in\" \"/im_livechat/get_session\" \"/im_livechat/cors/get_session\"})",
            ratelimit:{
                characteristics:["cf.colo.id","ip.src"],
                period:10,
                requests_per_period:20,
                mitigation_timeout:10
            }
        }' > "$rule_file"
        reconcile_ruleset_rule \
            http_ratelimit \
            "Swirl Cloud rate limiting rules" \
            "swirl-cloud: rate limit interactive login endpoints" \
            "$rule_file"
    fi

    if [[ "$ENABLE_CACHE_RULES" == "true" ]]; then
        step "Reconciling the Swirl Cloud private-response cache bypass"
        cache_expression="(http.host in {$host_set} and ((any(http.request.headers[\"authorization\"][*] ne \"\")) or starts_with(http.request.uri.path, \"/api/\") or starts_with(http.request.uri.path, \"/auth/\") or starts_with(http.request.uri.path, \"/oauth\") or starts_with(http.request.uri.path, \"/realms/\") or starts_with(http.request.uri.path, \"/admin/\") or starts_with(http.request.uri.path, \"/websocket\") or starts_with(http.request.uri.path, \"/socket.io/\") or (http.request.uri.path in {\"/login\" \"/web/login\" \"/users/sign_in\"})))"
        rule_file="$WORK_DIR/rule-cache.json"
        jq -n \
            --arg expression "$cache_expression" \
            '{action:"set_cache_settings",action_parameters:{cache:false},description:"swirl-cloud: bypass cache for private and authenticated traffic",enabled:true,expression:$expression}' \
            > "$rule_file"
        reconcile_ruleset_rule \
            http_request_cache_settings \
            "Swirl Cloud cache rules" \
            "swirl-cloud: bypass cache for private and authenticated traffic" \
            "$rule_file"
    fi
}
