# Odoo Website Integration

The company website uses the platform's Odoo for two things: every contact-form
inquiry becomes a CRM lead, and visitors can chat with the team through Odoo
Live Chat. The website owns its form, consent text and chat button; the platform
owns Odoo, the integration user, the network path, and the chat channel and its
chatbot.

## What the Odoo bootstrap configures

The `bootstrap-database` init container in [the Odoo manifest](../../k8s/corp/odoo.yaml)
installs CRM, Live Chat and the managed `platform_crm_intake` add-on, and
upgrades the add-on on every start. On the first start after installation it
also:

- enables **Leads**, so website inquiries arrive as leads to qualify before they
  become opportunities;
- names the default Live Chat channel after the organization and makes the
  Odoo administrator its first agent;
- adds a chatbot that answers only while no agent is online. It greets the
  visitor, asks for an email address and the request, then creates a lead in
  the first sales team with the conversation attached. The rule opens the chat
  window as soon as the website loads the widget.

The chatbot text is English with French translations. Visitors see the language
of their browser, as in the rest of the chat window; any other language falls
back to English. Staff can change agents, colours, the chatbot and its texts in
**Live Chat → Configuration**. The bootstrap does not overwrite those changes.

Agents receive conversations in Odoo **Discuss** in the browser or the Odoo
mobile app. While no agent has Odoo open, the chatbot takes the conversation.

## Lead intake

### Provision the integration

Run from the platform host with the platform kubeconfig, naming the website's
application (its Vault `apps/<name>/` prefix):

```bash
python3 scripts/configure-odoo-intake.py --application <website>
```

The command creates the Odoo user `integration-<website>` and an API key. It
verifies that the key authenticates and cannot read CRM records, and stores it
at Vault `apps/<website>/odoo`. Reruns keep a valid stored key.

| Vault field | Value |
| --- | --- |
| `url` | `http://odoo.corp.svc.cluster.local:8069` |
| `database` | `odoo` |
| `login` | `integration-<website>` |
| `apiKey` | Bearer key; server-side only, never sent to browsers |

The user is a portal user in the dedicated **Website CRM intake integration**
group. It can only call `crm.lead/platform_intake_submit`; it cannot read,
change or delete leads, or read other contacts. Leads are created in the main
company with no salesperson, the first sales team, the website's application
name as UTM source and **Website** as medium.

The website's pods reach Odoo in-cluster on port 8069 when they run in `apps`
and carry the label `swirl-cloud.io/odoo-crm-intake: "true"`. No other
application pod may connect; the chat bus on 8072 stays behind Traefik.

### API

```http
POST {url}/json/2/crm.lead/platform_intake_submit
Authorization: bearer {apiKey}
X-Odoo-Database: odoo
Content-Type: application/json

{"reference": "contact:7f3c9a2e-…", "values": {"name": "Website inquiry", "contact_name": "Ada",
 "email_from": "ada@example.org", "description": "Plain-text message", "lang": "fr_FR"}}
```

| Field | Rule |
| --- | --- |
| `reference` | Required. 8–128 characters from `A-Z a-z 0-9 . _ : -`. Generate it once per inquiry and reuse it on every retry. |
| `values.name` | Required lead title, up to 256 characters. |
| `contact_name`, `partner_name`, `email_from` | Optional, up to 256 characters. `email_from` must be an email address. |
| `phone` | Optional, up to 64 characters. |
| `description` | Optional plain text, up to 10 000 characters, stored as escaped HTML. |
| `lang` | Optional language code such as `fr_FR`; an inactive language is ignored. |

Any other field is rejected. Stage, salesperson, tags and scoring stay under
CRM staff control.

A successful call returns `200` with `{"id": <lead id>, "created": true}`. A retry
with the same reference returns the same lead with `"created": false`, including
when retries run concurrently. Error responses are JSON:

| Status | Meaning | Client action |
| --- | --- | --- |
| `401` | Missing, revoked or wrong key | Stop; refresh the secret |
| `403` | The user is not a CRM intake integration | Stop; reprovision |
| `422` | Invalid reference or values | Fix the request; do not retry |
| `409`, `5xx`, timeout | Conflict or temporary failure | Retry with the same reference and backoff |

Leads stay in Odoo until CRM staff archive or delete them; erasure requests are
handled in Odoo, in addition to the website's own copy.

### Rotate the key

Run the command with `--rotate`. The new key goes to Vault and the previous key
stays valid. After the website's ExternalSecret has refreshed and its pod has
restarted, run it with `--revoke-previous`.

## Live Chat widget

The channel's **Widget** tab shows the embed code. The website loads it only
when a visitor asks to chat:

```html
<script src="https://odoo.<domain>/im_livechat/loader/<channel id>"></script>
<script src="https://odoo.<domain>/im_livechat/assets_embed.js"></script>
```

Load the loader first. The widget then calls Odoo's public `cors` endpoints and
opens a WebSocket to `wss://odoo.<domain>/websocket`, so the website's Content
Security Policy must allow `https://odoo.<domain>` for scripts, styles, fonts,
images and connections, plus `wss://odoo.<domain>` for connections. The widget
draws in a shadow root and sets no cookies. It keeps the visitor's guest token,
chat history and window state in the website origin's local storage and
IndexedDB, so the visitor's own request to chat is what starts that storage.

The chat endpoints are public, and Odoo answers any origin. The Cloudflare
login rate limit also covers chat session starts
(`/im_livechat/get_session` and `/im_livechat/cors/get_session`).

## Changes and recovery

Take a database dump before changing the add-on or the CRM configuration
(`kubectl -n corp exec deploy/odoo -c odoo -- pg_dump …`, or the nightly archive
in [operations](../operations.md#backups-and-recovery)). Uninstalling CRM or Live Chat
does not restore the earlier schema, so recovery means restoring that dump.

To stop lead intake, archive the integration user in Odoo (its keys stop
working) and remove the website's pod label. To hide the chat, archive the
channel's rule or remove the widget from the website.

## Related Guides

- [Application onboarding](application-onboarding.md)
- [Operations](../operations.md)
- [Security](../security.md)
