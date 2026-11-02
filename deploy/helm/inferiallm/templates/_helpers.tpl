{{- define "inferiallm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "inferiallm.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "inferiallm.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "inferiallm.labels" -}}
helm.sh/chart: {{ include "inferiallm.chart" . }}
app.kubernetes.io/name: {{ include "inferiallm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "inferiallm.selectorLabels" -}}
app.kubernetes.io/name: {{ include "inferiallm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "inferiallm.app.selectorLabels" -}}
{{ include "inferiallm.selectorLabels" . }}
app.kubernetes.io/component: app
{{- end }}

{{- define "inferiallm.postgres.selectorLabels" -}}
{{ include "inferiallm.selectorLabels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "inferiallm.redis.selectorLabels" -}}
{{ include "inferiallm.selectorLabels" . }}
app.kubernetes.io/component: redis
{{- end }}

{{- define "inferiallm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "inferiallm.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "inferiallm.secretName" -}}
{{- default (printf "%s-secrets" (include "inferiallm.fullname" .)) .Values.secrets.existingSecret }}
{{- end }}

{{- define "inferiallm.configMapName" -}}
{{- printf "%s-config" (include "inferiallm.fullname" .) }}
{{- end }}

{{- define "inferiallm.pvcName" -}}
{{- printf "%s-data" (include "inferiallm.fullname" .) }}
{{- end }}

{{- define "inferiallm.priorityClassName" -}}
{{- default (printf "%s-critical" (include "inferiallm.fullname" .)) .Values.priorityClass.name }}
{{- end }}

{{- define "inferiallm.appImage" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{- define "inferiallm.replicas" -}}
{{- if eq .Values.mode "ha" }}
{{- max 2 (int .Values.app.replicaCount) }}
{{- else }}
{{- int .Values.app.replicaCount }}
{{- end }}
{{- end }}

{{- define "inferiallm.databaseHost" -}}
{{- if .Values.database.bundled.enabled }}
{{- printf "%s-postgres" (include "inferiallm.fullname" .) }}
{{- else }}
{{- required "database.host is required when database.bundled.enabled=false" .Values.database.host }}
{{- end }}
{{- end }}

{{- define "inferiallm.redisHost" -}}
{{- if .Values.redis.bundled.enabled }}
{{- printf "%s-redis" (include "inferiallm.fullname" .) }}
{{- else }}
{{- required "redis.host is required when redis.bundled.enabled=false" .Values.redis.host }}
{{- end }}
{{- end }}

{{- define "inferiallm.sshSecretName" -}}
{{- if .Values.ssh.existingSecret }}
{{- .Values.ssh.existingSecret }}
{{- else }}
{{- include "inferiallm.secretName" . }}
{{- end }}
{{- end }}
