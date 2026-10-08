# Odoo Website Integration

The company website uses the platform's Odoo for two things: every contact-form
inquiry becomes a CRM lead, and visitors can chat with the team through Odoo
Live Chat. The website owns its form, consent text and chat button; the platform
owns Odoo, the integration user, the network path, and the chat channel and its
chatbot.

## What the Odoo bootstrap configures

The `bootstrap-database` init container in [the Odoo manifest](../../k8s/corp/odoo.yaml)
installs CRM, Live Chat and the managed
[`platform_crm_intake`](../../k8s/files/odoo-addons/platform_crm_intake/) add-on, and
upgrades the add-on on every start. On the first start after installation it
also:

- enables **Leads**, so website inquiries arrive as leads to qualify before they
  become opportunities;
- names the default Live Chat channel after the organization and makes the
  Odoo administrator its first agent;
- adds a chatbot that answers only while no agent is online. It greets the
  visitor, asks for an email address and the request, then creates a lead in
  the first sales team with the conversation attached. Where the page is wide
  enough, the channel rule also opens the chat as soon as the widget loads.

The chatbot text is English with French translations. Visitors see the language
of the website page they chat from, or otherwise of their browser; any other
language falls back to English. Staff can change agents, colours, the chatbot and its texts in
**Live Chat → Configuration**. The bootstrap does not overwrite those changes.

Agents receive conversations in Odoo **Discuss** in the browser or the Odoo
mobile app. While no agent has Odoo open, the chatbot takes the conversation.

## Lead intake

### Provision the integration

Run from the platform host with the platform kubeconfig, naming the website's
application (its Vault `apps/<name>/` prefix):

```bash
python3 scripts/configure-odoo-website.py --application <website> \
  --website-origin https://<website host>
```

The command creates the Odoo user `integration-<website>` and an API key. It
verifies that the key authenticates and cannot read CRM records, and stores it
at Vault `apps/<website>/odoo`. Reruns keep a valid stored key. It also records
the website origin, the only page allowed to frame the chat (see below).

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

## Live Chat on the website

Odoo's chat widget compiles its templates at run time and injects inline
styles, which a strict Content Security Policy rightly blocks. The website
therefore shows the chat in a frame served from Odoo's own origin:

```text
https://odoo.<domain>/platform_crm_intake/chat/<channel id>?lang=<fr|en>[&open=1]
```

The page loads the widget and reports to the website whether a chat window is
open. With `open=1`, sent when the visitor clicks the website's chat button, it
also opens the chat once Odoo has restored the visitor's windows; without it,
it only restores a conversation the visitor left open, for example on the next
page they visit.

| Message | Direction | Meaning |
| --- | --- | --- |
| `{type: "odoo-livechat", open: true\|false}` | Odoo → website | Show or hide the frame |
| `{type: "odoo-livechat", open: false, failed: true}` | Odoo → website | The chat cannot start; offer another contact path |
| `{type: "odoo-livechat-open"}` | website → Odoo | Reopen the chat, or start a new one |

Messages are exchanged only with the website origin given to
`configure-odoo-website.py --website-origin`. The page sends
`Content-Security-Policy: frame-ancestors <website origin>`, so no other site can
frame it, and returns `404` until that origin is set. `lang` selects the
language of the chatbot and is kept in Odoo's `frontend_lang` cookie.

The website's own policy then needs only `frame-src https://odoo.<domain>`.
Keep the frame narrower than 768 px (Odoo's phone layout fills it), for example
400 × 620 px on desktop and the full screen on phones. Inside the frame, Odoo
sets its `session_id` and `frontend_lang` cookies on `odoo.<domain>` and keeps the
guest token and chat history in that origin's local storage; nothing is stored
until the visitor opens the chat.

The chat endpoints are public. The Cloudflare login rate limit also covers chat
session starts (`/im_livechat/get_session` and `/im_livechat/cors/get_session`).

## Chat by email

The team does not need Odoo open to answer the website chat. New visitor
messages are emailed to the team, a reply to that email appears in the chat,
and a visitor who has left receives the answer by email and can reply to it.

```bash
python3 scripts/configure-odoo-website.py --application <website> \
  --website-origin https://<website host> \
  --notify team@<domain> --reply-sender team@<domain> --reply-sender person@mail.example \
  --timezone Europe/Paris --chat-persona <website repo>/infra/odoo/chat-persona.json \
  --resend-key-path apps/<website>/contact [--email-worker]
```

| Option | Effect |
| --- | --- |
| `--notify` | Receives one email per chat once the visitor has paused for 45 seconds, with the new messages, the recent conversation, and links to the chat and its CRM lead. Messages an agent already read in Odoo are not emailed. |
| `--reply-sender` | Addresses whose replies are accepted; defaults to the `--notify` addresses. List every address you actually reply from, such as the mailbox a forwarding address delivers to. |
| `--chat-from` | Sender of chat emails, `chat@<website host>` by default. Its plus-addresses receive replies. |
| `--notify-lang`, `--timezone` | Language (`fr` or `en`) and time zone of the team's emails. |
| `--chat-persona` | JSON file naming the chatbot, its PNG avatar and its messages (`type`, `en`, `fr` per step, in the order of the bootstrap's chatbot steps). |
| `--resend-key-path` | Vault path whose `RESEND_API_KEY` becomes Odoo's outgoing mail server (`smtp.resend.com`, limited to the sender's domain). Odoo also gets that domain as its mail domain, so its own emails can be sent too. |
| `--email-worker` | Deploys the Cloudflare Email Worker and its routing rule (below). Needs `CLOUDFLARE_API_TOKEN` with **Workers Scripts: Edit** and **Email Routing Rules: Edit**. |

Every chat email has a Reply-To of the form
`chat+<chat id>.<t|v>.<signature>@<domain>`, where `t` marks the team's copy, `v`
the visitor's, and the signature is an HMAC of both with a secret kept in Odoo.
Cloudflare Email Routing hands `chat@<domain>`, including its plus-addresses
(the script turns on the zone's subaddressing), to the `platform-chat-email` Worker ([source](../../scripts/lib/chat_email_worker.js)),
which posts the raw email to `https://odoo.<domain>/platform_crm_intake/mail/inbound`
with a shared secret (Vault `apps/<website>/odoo`, field `inboundSecret`). Odoo
then:

- ignores an unknown or wrongly signed address;
- refuses a sender that may not answer that copy (not a listed team sender, or
  not the visitor's own address); the Worker bounces it, so a mistaken sender
  learns about it;
- removes the quoted conversation and signature (Gmail, Outlook, Apple Mail, in
  English and French), then posts the rest in the chat: the team's reply as the
  chat's agent (the chatbot persona when nobody was online), the visitor's as the
  visitor;
- emails the team's reply to the visitor when they are no longer on the website,
  using the address they gave the chatbot.

An Odoo outage makes the Worker fail temporarily, so the sending mail server
retries. Chatbot leads no longer trigger Odoo's separate "You have been
assigned" email, because the chat email already links the lead.

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
