{{- /*
Sizing profiles (TODO §26). values.yaml sizingProfile names files/sizing/<profile>.yaml;
"standard" leaves every manifest as written. swirl-cloud.render passes each rendered
manifest through swirl-cloud.sized: for each document listed under the manifest's path
(Kind/name) it replaces exact text (each "from" exactly once), then sets container
resources (merged), existing environment values and arguments, and PVC storage.
Documents without an entry are emitted unchanged. Every entry must match, so a
manifest change that breaks one fails the render instead of silently dropping a size.
scripts/lib/sizing.py applies the same text replacements outside Helm.
*/ -}}
{{- define "swirl-cloud.sizing" -}}
{{- $profile := .Values.sizingProfile | default "standard" -}}
{{- if eq $profile "standard" -}}
{}
{{- else -}}
{{- $body := .Files.Get (printf "files/sizing/%s.yaml" $profile) -}}
{{- if not $body -}}
{{- fail (printf "sizingProfile %q: files/sizing/%s.yaml is missing" $profile $profile) -}}
{{- end -}}
{{ $body }}
{{- end -}}
{{- end -}}

{{- define "swirl-cloud.sized" -}}
{{- $root := index . 0 -}}
{{- $path := index . 1 -}}
{{- $content := index . 2 -}}
{{- $manifests := (include "swirl-cloud.sizing" $root | fromYaml).manifests | default dict -}}
{{- $overrides := get $manifests $path | default dict -}}
{{- if not $overrides -}}
{{ $content }}
{{- else -}}
{{- $seen := dict -}}
{{- $documents := list -}}
{{- range $document := splitList "\n---" $content -}}
{{- $resource := $document | fromYaml | default dict -}}
{{- $key := printf "%s/%s" (get $resource "kind" | default "") (get (get $resource "metadata" | default dict) "name" | default "") -}}
{{- $entry := get $overrides $key -}}
{{- if not $entry -}}
{{- $documents = append $documents $document -}}
{{- else -}}
{{- $_ := set $seen $key true -}}
{{- range $edit := $entry.replace | default list -}}
{{- if ne (len (splitList $edit.from $document)) 2 -}}
{{- fail (printf "sizing %s %s: %q must occur exactly once" $path $key $edit.from) -}}
{{- end -}}
{{- $document = replace $edit.from $edit.to $document -}}
{{- end -}}
{{- $resource = $document | fromYaml -}}
{{- range $name, $settings := $entry.containers | default dict -}}
{{- $found := false -}}
{{- range $container := $resource.spec.template.spec.containers -}}
{{- if eq $container.name $name -}}
{{- $found = true -}}
{{- if $settings.resources -}}
{{- $_ := set $container "resources" (mergeOverwrite (deepCopy ($container.resources | default dict)) $settings.resources) -}}
{{- end -}}
{{- if $settings.args -}}
{{- $_ := set $container "args" $settings.args -}}
{{- end -}}
{{- range $variable, $value := $settings.env | default dict -}}
{{- $set := false -}}
{{- range $env := $container.env | default list -}}
{{- if eq $env.name $variable -}}
{{- $_ := set $env "value" $value -}}
{{- $set = true -}}
{{- end -}}
{{- end -}}
{{- if not $set -}}
{{- fail (printf "sizing %s %s: container %s has no %s to set" $path $key $name $variable) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- if not $found -}}
{{- fail (printf "sizing %s %s: no container %s" $path $key $name) -}}
{{- end -}}
{{- end -}}
{{- if $entry.storage -}}
{{- if ne $resource.kind "PersistentVolumeClaim" -}}
{{- fail (printf "sizing %s %s: storage applies to PersistentVolumeClaims only" $path $key) -}}
{{- end -}}
{{- $_ := set $resource.spec.resources.requests "storage" $entry.storage -}}
{{- end -}}
{{- $documents = append $documents (printf "\n%s" (toYaml $resource)) -}}
{{- end -}}
{{- end -}}
{{- range $key, $_ := $overrides -}}
{{- if not (hasKey $seen $key) -}}
{{- fail (printf "sizing %s: no document %s" $path $key) -}}
{{- end -}}
{{- end -}}
{{ join "\n---" $documents }}
{{- end -}}
{{- end -}}
