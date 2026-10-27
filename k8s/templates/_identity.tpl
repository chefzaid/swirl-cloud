{{- define "swirl-cloud.identity" -}}
{{- $domain := required "publicDomain is required" .Values.publicDomain -}}
{{- if not (regexMatch "^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[A-Za-z0-9_.~%+-]+)+\\.git$" .Values.gitopsRepositoryURL) -}}
{{- fail "gitopsRepositoryURL must be an HTTP(S) .git URL without embedded credentials" -}}
{{- end -}}
{{- $slug := .Values.organizationSlug | default (first (splitList "." $domain)) -}}
{{- $name := .Values.organizationName | default $slug -}}
{{- $group := .Values.gitlabGroupPath | default $slug -}}
{{- $project := .Values.gitlabProjectName | default "swirl-cloud" -}}
{{- $realm := .Values.keycloakRealm | default $slug -}}
{{- $tls := .Values.tlsSecretName | default (printf "%s-tls" (replace "." "-" $domain)) -}}
{{- $alm := .Values.sonarAlmSetting | default (printf "%s-gitlab" $slug) -}}
{{- if or (not (regexMatch "^[a-z0-9]([a-z0-9-]*[a-z0-9])?$" $slug)) (gt (len $slug) 63) -}}
{{- fail "organizationSlug must be a lowercase DNS label of at most 63 characters" -}}
{{- end -}}
{{- if or (not (regexMatch "^[A-Za-z0-9_-][A-Za-z0-9_.-]*(/[A-Za-z0-9_-][A-Za-z0-9_.-]*)*$" $group)) (not (regexMatch "^[A-Za-z0-9_-][A-Za-z0-9_.-]*$" $project)) -}}
{{- fail "gitlabGroupPath and gitlabProjectName must be valid repository path components" -}}
{{- end -}}
{{- if or (eq $realm "master") (not (regexMatch "^[A-Za-z0-9_-][A-Za-z0-9_.-]*$" $realm)) (not (regexMatch "^[A-Za-z0-9_-][A-Za-z0-9_.-]*$" $alm)) -}}
{{- fail "keycloakRealm and sonarAlmSetting must be valid identifiers; master is reserved" -}}
{{- end -}}
{{- if or (not (regexMatch "^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$" $tls)) (gt (len $tls) 253) -}}
{{- fail "tlsSecretName must be a Kubernetes DNS subdomain" -}}
{{- end -}}
{{- /* Installations formatted Kafka with this ID before it was recorded. */ -}}
{{- $kafka := .Values.kafkaClusterId | default "8ipNY9RxQtWkattTais5yQ" -}}
{{- if not (regexMatch "^[A-Za-z0-9_-]{22}$" $kafka) -}}
{{- fail "kafkaClusterId must be a 22-character Kafka cluster ID" -}}
{{- end -}}
{{- $language := .Values.portalLanguage | default "en" -}}
{{- if not (has $language (list "en" "fr")) -}}
{{- fail "portalLanguage must be en or fr" -}}
{{- end -}}
{{- $idp := .Values.cloudflareAccessIdpName | default (printf "%s Keycloak" $name) -}}
{{- $groupName := .Values.gitlabGroupName | default $name -}}
{{- $scanner := index .Values "trivy-operator" -}}
{{- if or (contains "__GITLAB_GROUP_PATH__" $scanner.image.repository) (contains "__GITLAB_GROUP_PATH__" $scanner.trivy.image.repository) -}}
{{- fail "Supply trivy-operator.image.repository and trivy-operator.trivy.image.repository for the selected GitLab project; the installer generates these Helm parameters" -}}
{{- end -}}
{{- range $display := list $name $idp $groupName -}}
{{- if or (regexMatch "[[:cntrl:]]" $display) (gt (len $display) 160) -}}
{{- fail "Organization, GitLab group and Cloudflare display names must be printable and at most 160 characters" -}}
{{- end -}}
{{- end -}}
{{- dict "__ORGANIZATION_SLUG__" $slug "__ORGANIZATION_NAME_JSON__" (toJson $name)
    "__GITLAB_GROUP_PATH__" $group "__GITLAB_PROJECT_NAME__" $project
    "__GITLAB_GROUP_NAME_JSON__" (toJson $groupName) "__KEYCLOAK_REALM__" $realm
    "__TLS_SECRET_NAME__" $tls "__SONAR_ALM_SETTING__" $alm
    "__CLOUDFLARE_ACCESS_IDP_NAME_JSON__" (toJson $idp)
    "__PLATFORM_TITLE_JSON__" (toJson (printf "%s Cloud" $name)) "__KAFKA_CLUSTER_ID__" $kafka
    "__PORTAL_LANGUAGE__" $language | toJson -}}
{{- end -}}
{{- /* Optional forwarding of audit and detection events to a SOC over syslog
     (docs/guides/soc-forwarding.md); an empty host turns it off. */ -}}
{{- define "swirl-cloud.soc-forwarding" -}}
{{- $host := .Values.socSyslogHost | default "" -}}
{{- $port := .Values.socSyslogPort | default "" -}}
{{- $sources := .Values.socSyslogSources | default "" -}}
{{- $ca := .Values.socSyslogCa | default "" -}}
{{- $known := list "identity" "kubernetes-audit" "vault-audit" "detection" "host-journal" -}}
{{- if $host -}}
{{- if or (gt (len $host) 253) (not (regexMatch "^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$" $host)) -}}
{{- fail "socSyslogHost must be a host name or IPv4 address" -}}
{{- end -}}
{{- $port = $port | default "6514" -}}
{{- if or (not (regexMatch "^[1-9][0-9]{0,4}$" $port)) (gt (atoi $port) 65535) -}}
{{- fail "socSyslogPort must be a TCP port" -}}
{{- end -}}
{{- $sources = $sources | default "identity,kubernetes-audit,vault-audit,detection" -}}
{{- range $source := splitList "," $sources -}}
{{- if not (has $source $known) -}}
{{- fail "socSyslogSources must list identity, kubernetes-audit, vault-audit, detection or host-journal, separated by commas" -}}
{{- end -}}
{{- end -}}
{{- if and $ca (not (contains "-----BEGIN CERTIFICATE-----" (b64dec $ca))) -}}
{{- fail "socSyslogCa must be a base64-encoded PEM certificate" -}}
{{- end -}}
{{- end -}}
{{- $selected := list -}}
{{- range $source := $known -}}
{{- if and $host (has $source (splitList "," $sources)) -}}
{{- $selected = append $selected $source -}}
{{- end -}}
{{- end -}}
{{- dict "host" $host "port" $port "sources" $sources "ca" $ca "selected" $selected | toJson -}}
{{- end -}}
