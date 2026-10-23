#!/usr/bin/env bash
# Cloudflare API requests, zone settings and ruleset rules for
# configure-cloudflare.sh, which provides logging, API_BASE, CURL_CONFIG,
# WORK_DIR and ZONE_ID.

cf_request() {
    local method="$1"
    local path="$2"
    local data_file="${3:-}"
    local curl_args=(
        --config "$CURL_CONFIG"
        --request "$method"
        --url "$API_BASE$path"
        --header "Content-Type: application/json"
    )

    if [[ -n "$data_file" ]]; then
        curl_args+=(--data-binary "@$data_file")
    fi

    curl "${curl_args[@]}"
}

require_success() {
    local response="$1"
    local operation="$2"

    if ! jq -e '.success == true' <<< "$response" >/dev/null 2>&1; then
        local details
        details="$(jq -r '[.errors[]?.message, .messages[]?.message] | map(select(. != null and . != "")) | join("; ")' <<< "$response" 2>/dev/null || true)"
        error "$operation failed${details:+: $details}"
    fi
}

set_zone_value() {
    local path="$1"
    local desired_value="$2"
    local description="$3"
    local setting_file response current_value

    response="$(cf_request GET "/zones/$ZONE_ID/$path")"
    require_success "$response" "Reading $description"
    current_value="$(jq -c '.result.value' <<< "$response")"
    if [[ "$current_value" == "$(jq -cn --arg value "$desired_value" '$value')" ]]; then
        info "$description already set to $desired_value."
        return 0
    fi

    setting_file="$WORK_DIR/setting-${path//\//-}.json"
    jq -n --arg value "$desired_value" '{value:$value}' > "$setting_file"
    response="$(cf_request PATCH "/zones/$ZONE_ID/$path" "$setting_file")"
    require_success "$response" "Setting $description"
    info "Set $description to $desired_value."
}

set_zone_setting() {
    set_zone_value "settings/$1" "$2" "$3"
}

reconcile_ruleset_rule() {
    local phase="$1"
    local ruleset_name="$2"
    local rule_description="$3"
    local desired_rule_file="$4"
    local ruleset_response ruleset_id rule_count rule_id update_response
    local create_file current_fingerprint desired_fingerprint

    ruleset_response="$(cf_request GET "/zones/$ZONE_ID/rulesets/phases/$phase/entrypoint")"
    if jq -e '.success == true' <<< "$ruleset_response" >/dev/null 2>&1; then
        ruleset_id="$(jq -r '.result.id' <<< "$ruleset_response")"
    elif jq -e 'any(.errors[]?; .code == 10003)' <<< "$ruleset_response" >/dev/null 2>&1; then
        create_file="$WORK_DIR/ruleset-create-${phase}.json"
        jq -n \
            --arg name "$ruleset_name" \
            --arg phase "$phase" \
            --slurpfile rule "$desired_rule_file" \
            '{name:$name,kind:"zone",phase:$phase,rules:$rule}' > "$create_file"
        update_response="$(cf_request POST "/zones/$ZONE_ID/rulesets" "$create_file")"
        require_success "$update_response" "Creating $ruleset_name"
        info "Created Cloudflare rule: $rule_description"
        return 0
    else
        require_success "$ruleset_response" "Reading $ruleset_name"
    fi

    rule_count="$(jq --arg description "$rule_description" \
        '[.result.rules[]? | select(.description == $description)] | length' <<< "$ruleset_response")"
    ((rule_count <= 1)) || error "More than one Cloudflare rule is named: $rule_description"

    if ((rule_count == 0)); then
        update_response="$(cf_request POST "/zones/$ZONE_ID/rulesets/$ruleset_id/rules" "$desired_rule_file")"
        require_success "$update_response" "Creating Cloudflare rule: $rule_description"
        info "Created Cloudflare rule: $rule_description"
        return 0
    fi

    rule_id="$(jq -r --arg description "$rule_description" \
        '.result.rules[] | select(.description == $description) | .id' <<< "$ruleset_response")"
    current_fingerprint="$(jq -Sc --arg description "$rule_description" \
        '.result.rules[] | select(.description == $description) |
         {action,action_parameters,description,enabled,expression,ratelimit} |
         with_entries(select(.value != null))' <<< "$ruleset_response")"
    desired_fingerprint="$(jq -Sc \
        '{action,action_parameters,description,enabled,expression,ratelimit} |
         with_entries(select(.value != null))' "$desired_rule_file")"
    if [[ "$current_fingerprint" == "$desired_fingerprint" ]]; then
        info "Cloudflare rule already correct: $rule_description"
        return 0
    fi

    update_response="$(cf_request PATCH "/zones/$ZONE_ID/rulesets/$ruleset_id/rules/$rule_id" "$desired_rule_file")"
    require_success "$update_response" "Updating Cloudflare rule: $rule_description"
    info "Updated Cloudflare rule: $rule_description"
}
