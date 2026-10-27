# SOC Forwarding

Fluent Bit can send copies of audit and detection events to a security operations
centre (SOC) or managed security provider, as syslog over TLS. Elasticsearch keeps
its own copy either way. Forwarding is off until you name a receiver.

## Sources

| Source | Events | Volume |
| --- | --- | --- |
| `identity` | Keycloak logins, login failures and admin changes ([identity events](../security.md#identity-events)) | low |
| `kubernetes-audit` | Kubernetes API audit records ([API audit log](../security.md#kubernetes-api-audit-log)) | medium |
| `vault-audit` | Vault audit entries, requests and responses with secret values hashed | medium |
| `detection` | [Security detection](security-detection.md) findings, once per finding and hour | low |
| `host-journal` | Every host journal line: SSH, sudo, kernel, services | high |

By default every source except `host-journal` is sent.

## Configure

On a control plane, with the receiver's CA bundle when its certificate is not
signed by a public CA:

```bash
scripts/configure-soc-forwarding.py --host siem.example.net --port 6514 \
  --ca-file soc-ca.pem --sources identity,kubernetes-audit,vault-audit,detection
```

The script first opens a TLS connection to the receiver and checks its certificate
and host name, as Fluent Bit will. It then records the settings in the
`swirl-cloud` Argo CD Application and the `infra/swirl-cloud-identity` ConfigMap.
Argo CD applies them at its next sync, and Fluent Bit restarts on every node.
`--show` prints the recorded settings and `--remove` stops forwarding.
`--skip-check` records settings without connecting, for example when the receiver
allows only the nodes' addresses.

Installer and Ansible reruns keep the recorded settings. A new installation can
set them as environment inputs: `SOC_SYSLOG_HOST`, `SOC_SYSLOG_PORT` (default
6514), `SOC_SYSLOG_SOURCES` and `SOC_SYSLOG_CA_FILE`, a PEM file recorded
base64-encoded as `SOC_SYSLOG_CA`.

Allow the nodes' outbound address to reach the receiver, and list the receiver
among your [external services](external-services.md): it sees user names, IP
addresses, Kubernetes object names and Vault request paths.

## Message format

Each event is one RFC 5424 message, newline-terminated on the TLS stream (no
octet counting, so configure the receiver for newline framing):

| RFC 5424 field | Value |
| --- | --- |
| PRI | Facility 13 (log audit) and the severity below |
| HOSTNAME | The node: the Kubernetes node for API audit and pods, the host for journal lines, the finding's node for detections |
| APP-NAME | `swirl-cloud-<source>`, for example `swirl-cloud-identity` |
| MSGID | Keycloak event or admin operation type, API verb, Vault `request` or `response`, detection alert name, or journal identifier (32 characters at most) |
| MSG | JSON: `{"source": ..., "node": ..., "event": {...}}` |

`event` is the record as Elasticsearch stores it, without collection metadata
(Kubernetes pod details, the raw log line and stream). Fields per source:

- `identity`: `level`, `event_category` (`user` or `admin`) and `keycloak` with
  `type` or `operationType`, `realmName`, `clientId`, `userId`, `username`,
  `ipAddress`, `error` and the resource fields of admin events.
- `kubernetes-audit`: the audit event (`auditID`, `verb`, `user`, `sourceIPs`,
  `objectRef`, `responseStatus`, `requestURI`, timestamps), metadata only.
- `vault-audit`: Vault's audit entry (`type`, `auth`, `request`, `response`,
  `error`); Vault hashes secret values with HMAC before writing it.
- `detection`: the finding's labels (`alertname`, `severity` and the rule's own
  labels such as `user`, `node` or `source_address`), `summary` and `description`.
- `host-journal`: `hostname`, `systemd_unit`, `syslog_identifier`, `priority`,
  `pid`, `uid`, `comm`, `exe` and `message`.

Severity: detection findings are 2 (critical) or 4 (warning); Keycloak events
with an error, API requests answered with 400 or more and Vault entries with an
error are 5 (notice); everything else is 6 (informational).

Messages longer than 64 KiB are cut, which leaves their JSON incomplete; the
platform's audit records stay well below that.

## Delivery

While the receiver is unreachable, each node buffers SOC events on disk (up to
512 MB) and retries 20 times per chunk before dropping it.
[`SocForwardingFailing`](alert-runbooks.md#socforwardingfailing) fires after 15
minutes of retries without a delivery, and
[`SocForwardingDroppedEvents`](alert-runbooks.md#socforwardingdroppedevents) when
events were dropped. Delivery counters are in Prometheus as
`fluentbit_output_proc_records_total{name="soc_syslog"}`.

OTLP export is not offered yet. The off-site tamper-evident archive (TODO §36)
remains the record of reference; the SOC copy is for monitoring.

## Related Guides

- [Security detection](security-detection.md)
- [Security](../security.md)
- [Incident response](incident-response.md)
