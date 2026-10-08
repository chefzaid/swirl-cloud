{{- /*
Odoo add-ons mounted read-only at /mnt/extra-addons, built from
files/odoo-addons/<module>/. platform/ manifests are plain files, so
corp/odoo.yaml carries markers that the renderer replaces with these blocks
(see swirl-cloud.render in resources.yaml). ConfigMap keys cannot contain
"/", so nested paths are stored as "a__b" and mapped back through items.
*/ -}}
{{- define "swirl-cloud.odoo-addons" -}}
{{- toJson (list
  (dict "module" "odoo_keycloak_sso" "configMap" "odoo-keycloak-sso-addon")
  (dict "module" "platform_crm_intake" "configMap" "odoo-crm-intake-addon")
  (dict "module" "web_dark_mode" "configMap" "odoo-dark-mode-addon" "annotations" (dict
    "upstream.repository" "https://github.com/OCA/web"
    "upstream.revision" "b96307a8953f3bae075b8f7d94f40f00c2e63d1f"))) -}}
{{- end -}}

{{- define "swirl-cloud.odoo-addon-binary" -}}
{{- has (ext .) (list ".png" ".jpg" ".gif" ".ico" ".woff" ".woff2") | ternary "true" "" -}}
{{- end -}}

{{- define "swirl-cloud.odoo-addon-configmaps" -}}
{{- $root := . -}}
{{- range $index, $addon := include "swirl-cloud.odoo-addons" $root | fromJsonArray -}}
{{- $prefix := printf "files/odoo-addons/%s/" $addon.module -}}
{{- $text := dict -}}
{{- $binary := dict -}}
{{- range $path, $_ := $root.Files.Glob (printf "%s**" $prefix) -}}
{{- $key := trimPrefix $prefix $path | replace "/" "__" -}}
{{- if include "swirl-cloud.odoo-addon-binary" $path -}}
{{- $_ := set $binary $key ($root.Files.Get $path | b64enc) -}}
{{- else -}}
{{- $_ := set $text $key ($root.Files.Get $path) -}}
{{- end -}}
{{- end -}}
{{- if not (or $text $binary) -}}
{{- fail (printf "Odoo add-on %s has no files under %s" $addon.module $prefix) -}}
{{- end -}}
{{- if $index }}
---
{{ end -}}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ $addon.configMap }}
  namespace: corp
{{- with $addon.annotations }}
  annotations:
{{ toYaml . | indent 4 }}
{{- end }}
data:
{{- range $key := keys $text | sortAlpha }}
  {{ $key }}: {{ index $text $key | toJson }}
{{- end }}
{{- if $binary }}
binaryData:
{{- range $key := keys $binary | sortAlpha }}
  {{ $key }}: {{ index $binary $key }}
{{- end }}
{{- end }}
{{ end -}}
{{- end -}}

{{- define "swirl-cloud.odoo-addon-volumes" -}}
{{- $root := . -}}
{{- range $addon := include "swirl-cloud.odoo-addons" $root | fromJsonArray }}
{{- $prefix := printf "files/odoo-addons/%s/" $addon.module }}
- name: {{ $addon.configMap }}
  configMap:
    name: {{ $addon.configMap }}
    items:
{{- range $path, $_ := $root.Files.Glob (printf "%s**" $prefix) }}
{{- $relative := trimPrefix $prefix $path }}
    - key: {{ replace "/" "__" $relative }}
      path: {{ $relative }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "swirl-cloud.odoo-addon-mounts" -}}
{{- range $addon := include "swirl-cloud.odoo-addons" . | fromJsonArray }}
- name: {{ $addon.configMap }}
  mountPath: /mnt/extra-addons/{{ $addon.module }}
  readOnly: true
{{- end }}
{{- end -}}

{{- /* Changes to any add-on file restart Odoo, whose bootstrap upgrades it. */ -}}
{{- define "swirl-cloud.odoo-addon-checksum" -}}
{{- $root := . -}}
{{- $parts := list -}}
{{- range $path, $_ := $root.Files.Glob "files/odoo-addons/**" -}}
{{- $parts = append $parts (printf "%s\n%s" $path ($root.Files.Get $path | b64enc)) -}}
{{- end -}}
{{- join "\n" $parts | sha256sum -}}
{{- end -}}
