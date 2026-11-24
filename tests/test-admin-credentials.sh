#!/usr/bin/env bash
# Guard the installer's administrator credential rules: the Keycloak
# administrator's login (a username or an email) and password, the derived
# primary email, the local administrator password, and the install scope names.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scripts" && pwd)"
# shellcheck source=../scripts/lib/sso-admin.sh
source "$SCRIPT_DIR/lib/sso-admin.sh"
# shellcheck source=../scripts/lib/installer-selection.sh
source "$SCRIPT_DIR/lib/installer-selection.sh"
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

for login in platform-admin zaid a.b_c-9 admin@example.com first.last@sub.example.fr "$(printf 'a%.0s' {1..128})"; do
    sso_admin_validate_username "$login" || fail "Login '$login' is refused: $SSO_ADMIN_VALIDATION_ERROR"
done
check_login() {
    local login="$1" message="$2"
    if sso_admin_validate_username "$login"; then fail "Login '$login' is accepted"; fi
    [[ "$SSO_ADMIN_VALIDATION_ERROR" == *"$message"* ]] || fail "Login '$login': '$SSO_ADMIN_VALIDATION_ERROR', expected '$message'"
}
check_login ab "between 3 and 128"
check_login "$(printf 'a%.0s' {1..129})" "between 3 and 128"
check_login "a..b" "consecutive dots"
check_login "admin." "end with a dot"
check_login "first.@example.com" "local part that ends with a dot"
check_login "admin@example" "valid email address"
check_login "-admin" "must start with a letter or digit"
check_login "ad min" "must start with a letter or digit"
check_login 'admin$' "must start with a letter or digit"

PASSWORD='Str0ng!Passw0rd'
sso_admin_validate_password "$PASSWORD" platform-admin || fail "A strong password is refused: $SSO_ADMIN_VALIDATION_ERROR"
check_password() {
    local password="$1" login="$2" message="$3"
    if sso_admin_validate_password "$password" "$login"; then fail "Password '$password' is accepted"; fi
    [[ "$SSO_ADMIN_VALIDATION_ERROR" == *"$message"* ]] || fail "Password '$password': '$SSO_ADMIN_VALIDATION_ERROR', expected '$message'"
}
check_password 'Sh0rt!pass' admin "at least 12 characters"
check_password 'nouppercase1!x' admin "lowercase, uppercase, numeric, and special"
check_password 'NoDigitsHere!x' admin "lowercase, uppercase, numeric, and special"
check_password 'NoSpecial1234x' admin "lowercase, uppercase, numeric, and special"
check_password $'Tab\tInside1!xx' admin "control characters"
check_password 'Admin.User-1x' 'admin.user-1X' "cannot equal the login"

[[ "$(sso_admin_primary_email admin@example.org example.com)" == admin@example.org ]] || fail 'An email login is not its own email'
[[ "$(sso_admin_primary_email admin example.com)" == admin@example.com ]] || fail 'A username does not get the platform domain'
[[ "$(PLATFORM_DOMAIN=example.net sso_admin_primary_email admin)" == admin@example.net ]] || fail 'PLATFORM_DOMAIN is not used'
if PLATFORM_DOMAIN="" sso_admin_primary_email admin >/dev/null; then fail 'A username without a domain gets an email'; fi

LOCAL_ADMIN_PASSWORD="$PASSWORD"
validate_local_admin_password || fail "A strong local password is refused: $LOCAL_ADMIN_PASSWORD_ERROR"
for LOCAL_ADMIN_PASSWORD in 'Sh0rt!pass' 'nouppercase1!x' $'New\nLine1!xxxxx'; do
    if validate_local_admin_password; then fail "Local password '$LOCAL_ADMIN_PASSWORD' is accepted"; fi
    [[ -n "$LOCAL_ADMIN_PASSWORD_ERROR" ]] || fail 'A refused local password has no reason'
done

for scope in 1:infra infra:infra Infrastructure:infra infra-only:infra 2:apps apps:apps ALL:apps infra+apps:apps; do
    [[ "$(normalize_install_scope "${scope%%:*}")" == "${scope#*:}" ]] || fail "Scope ${scope%%:*} is not ${scope#*:}"
done
if normalize_install_scope 3 >/dev/null; then fail 'An unknown scope is accepted'; fi

printf '[PASS] Administrator credentials\n'
