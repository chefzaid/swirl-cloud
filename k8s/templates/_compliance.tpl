{{- /*
Prometheus recording rules generated from files/compliance-controls.yaml.
swirl_cloud_compliance_check_status: 2 passing, 1 no evidence, 0 failing.
The aggregations and the drift alert are static rules in platform/monitoring.yaml.
*/ -}}
{{- define "swirl-cloud.compliance-rule-groups" -}}
{{- $catalog := .Files.Get "files/compliance-controls.yaml" | fromYaml -}}
{{- if not $catalog.controls -}}
{{- fail "files/compliance-controls.yaml must define controls" -}}
{{- end -}}
{{- $rules := list -}}
{{- range $control := $catalog.controls -}}
{{- $automated := "false" -}}
{{- range $check := ($control.checks | default list) -}}
{{- $automated = "true" -}}
{{- $expr := printf "(count(%s) > 0) * 0\nor (count(%s) > 0) * 0 + 2\nor vector(1)" $check.failing $check.present -}}
{{- $rules = append $rules (dict "record" "swirl_cloud_compliance_check_status" "expr" $expr "labels" (dict "control" $control.id "check" $check.id)) -}}
{{- end -}}
{{- $rules = append $rules (dict "record" "swirl_cloud_compliance_control_info" "expr" "vector(1)" "labels" (dict "control" $control.id "title" $control.title "automated" $automated)) -}}
{{- range $framework, $requirements := $control.frameworks -}}
{{- range $requirement := $requirements -}}
{{- $rules = append $rules (dict "record" "swirl_cloud_compliance_requirement" "expr" "vector(1)" "labels" (dict "control" $control.id "framework" $framework "requirement" (toString $requirement))) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toYaml (list (dict "name" "swirl-cloud.compliance-evidence" "rules" $rules)) -}}
{{- end -}}
