# Odoo Website Integration

The company website uses the platform's Odoo in two ways: contact-form inquiries
become CRM leads, and visitors chat with the team through Odoo Live Chat. The
website owns its form, consent text and chat button. The platform owns Odoo, the
integration user, the network path, and the chat channel and chatbot.

## What the Odoo bootstrap configures

The `bootstrap-database` init container in [the Odoo manifest](../../k8s/corp/odoo.yaml)
installs CRM, Live Chat and the managed
[`platform_crm_intake`](../../k8s/files/odoo-addons/platform_crm_intake/) add-on, and
upgrades the add-on on every start. On the first start after installation it also:

- enables **Leads**, so inquiries are qualified before becoming opportunities;
- names the default Live Chat channel after the organization, with the Odoo
  administrator as first agent;
- adds a chatbot that answers only while no agent is online. It asks for an email
  address and the request, then creates a lead in the first sales team with the
  conversation attached. On wide enough pages the chat opens when the widget loads.

The chatbot speaks English and French, following the page language, then the
browser's; other languages fall back to English. Staff change agents, colours and
chatbot texts in **Live Chat → Configuration**; the bootstrap keeps those changes.
Agents answer in Odoo **Discuss** (browser or mobile app).

## Lead intake

### Provision the integration

Run from the platform host with the platform kubeconfig. `--application` is the
website's application name (its Vault `apps/<name>/` prefix):

```bash
python3 scripts/configure-odoo-website.py --application <website> \
  --website-origin https://<website host>
```

This creates Odoo user `integration-<website>` and an API key, verifies the key
authenticates but cannot read CRM records, and stores it at Vault
`apps/<website>/odoo`. Reruns keep a valid stored key. The website origin is the
only page allowed to frame the chat ([below](#live-chat-on-the-website)).

| Vault field | Value |
| --- | --- |
| `url` | `http://odoo.corp.svc.cluster.local:8069` |
| `database` | `odoo` |
| `login` | `integration-<website>` |
| `apiKey` | Bearer key; server-side only, never sent to browsers |

The user is a portal user in the **Website CRM intake integration** group. It can
only call `crm.lead/platform_intake_submit`; it cannot read, change or delete leads
or read contacts. Leads go to the main company with no salesperson, the first sales
team, the application name as UTM source and **Website** as medium.

Only pods in `apps` labelled `swirl-cloud.io/odoo-crm-intake: "true"` reach Odoo
in-cluster on port 8069. The chat bus on 8072 stays behind Traefik.

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
| `reference` | Required. 8–128 characters from `A-Z a-z 0-9 . _ : -`. Generate once per inquiry; reuse on every retry. |
| `values.name` | Required lead title, up to 256 characters. |
| `contact_name`, `partner_name`, `email_from` | Optional, up to 256 characters. `email_from` must be an email address. |
| `phone` | Optional, up to 64 characters. |
| `description` | Optional plain text, up to 10 000 characters, stored as escaped HTML. |
| `lang` | Optional language code such as `fr_FR`; an inactive language is ignored. |

Other fields are rejected; stage, salesperson, tags and scoring stay with CRM staff.

Success returns `200` with `{"id": <lead id>, "created": true}`. A retry with the
same reference, even concurrent, returns the same lead with `"created": false`.
Errors are JSON:

| Status | Meaning | Client action |
| --- | --- | --- |
| `401` | Missing, revoked or wrong key | Stop; refresh the secret |
| `403` | The user is not a CRM intake integration | Stop; reprovision |
| `422` | Invalid reference or values | Fix the request; do not retry |
| `409`, `5xx`, timeout | Conflict or temporary failure | Retry with the same reference and backoff |

Leads stay until CRM staff archive or delete them; handle erasure requests in Odoo
as well as in the website's own copy.

### Rotate the key

Run the command with `--rotate`: the new key goes to Vault and the previous key stays
valid. Once the website's ExternalSecret has refreshed and its pod restarted, run it
with `--revoke-previous`.

## Live Chat on the website

Odoo's widget compiles templates at run time and injects inline styles, which a
strict Content Security Policy blocks. The website therefore frames a page served
from Odoo's origin:

```text
https://odoo.<domain>/platform_crm_intake/chat/<channel id>?lang=<fr|en>[&open=1]
```

The page loads the widget and tells the website whether a chat window is open. With
`open=1` (sent when the visitor clicks the chat button) it opens the chat once Odoo
has restored the visitor's windows; without it, it only restores a chat the visitor
left open, for example on the next page.

| Message | Direction | Meaning |
| --- | --- | --- |
| `{type: "odoo-livechat", open: true\|false}` | Odoo → website | Show or hide the frame |
| `{type: "odoo-livechat", open: false, failed: true}` | Odoo → website | The chat cannot start; offer another contact path |
| `{type: "odoo-livechat-open"}` | website → Odoo | Reopen the chat, or start a new one |

The frame shows the correspondent (chatbot or agent) as online in the page `lang`,
and reports an opened chat within 100 ms. Messages are exchanged only with the
`--website-origin`. The page sends `Content-Security-Policy: frame-ancestors <website
origin>` and returns `404` until the origin is set. `lang` is kept in Odoo's
`frontend_lang` cookie.

On the website side:

- CSP needs only `frame-src https://odoo.<domain>`.
- Keep the frame narrower than 768 px (Odoo's phone layout fills it), e.g.
  400 × 620 px on desktop and full screen on phones.
- Odoo stores `session_id` and `frontend_lang` cookies on `odoo.<domain>` and the
  guest token and history in that origin's local storage, only once the visitor
  opens the chat.

The chat endpoints are public. The Cloudflare login rate limit also covers chat
session starts (`/im_livechat/get_session`, `/im_livechat/cors/get_session`).

## Chat by email

The team can answer without Odoo open: new visitor messages are emailed to the team,
email replies appear in the chat, and a visitor who has left gets the answer by email
and can reply.

```bash
python3 scripts/configure-odoo-website.py --application <website> \
  --website-origin https://<website host> \
  --notify team@<domain> --reply-sender team@<domain> --reply-sender person@mail.example \
  --timezone Europe/Paris --chat-persona <website repo>/infra/odoo/chat-persona.json \
  --resend-key-path apps/<website>/contact [--email-worker]
```

| Option | Effect |
| --- | --- |
| `--notify` | Repeatable. One email per chat after the visitor pauses for 45 seconds, with new messages, recent conversation and links to the chat and its lead. Messages an agent already read in Odoo are skipped. |
| `--reply-sender` | Repeatable. Addresses whose replies are accepted; defaults to `--notify`. List every address you reply from, such as a forwarding target mailbox. |
| `--chat-from` | Sender of chat emails, default `chat@<website host>`. Its plus-addresses receive replies. |
| `--notify-lang`, `--timezone` | Language (`fr` default, or `en`) and time zone (default `UTC`) of the team's emails. |
| `--chat-persona` | JSON naming the chatbot, its PNG avatar and its messages (`type`, `en`, `fr` per step, in bootstrap step order). |
| `--resend-key-path` | Vault path whose `RESEND_API_KEY` becomes Odoo's outgoing server (`smtp.resend.com`, limited to the sender's domain, which also becomes Odoo's mail domain). |
| `--email-worker` | Deploys the Cloudflare Email Worker and routing rule. Needs `CLOUDFLARE_API_TOKEN` with **Workers Scripts: Edit** and **Email Routing Rules: Edit**. |

Each chat email has Reply-To `chat+<chat id>.<t|v>.<signature>@<domain>`: `t` is the
team's copy, `v` the visitor's, and the signature is an HMAC with a secret kept in
Odoo. Cloudflare Email Routing sends `chat@<domain>` and its plus-addresses (the
script enables zone subaddressing) to the `platform-chat-email` Worker
([source](../../scripts/lib/chat_email_worker.js)). The Worker posts the raw email to
`https://odoo.<domain>/platform_crm_intake/mail/inbound` with a shared secret (Vault
`apps/<website>/odoo`, field `inboundSecret`). Odoo then:

- ignores an unknown or wrongly signed address;
- refuses a sender not allowed for that copy (not a listed team sender, or not the
  visitor's address); the Worker bounces it so the sender knows;
- strips quoted text and signatures (Gmail, Outlook, Apple Mail; English and French)
  and posts the reply as the agent (the chatbot persona if nobody was online) or as
  the visitor;
- emails the team's reply to a visitor who has left, at the address they gave.

During an Odoo outage the Worker fails temporarily and the sending server retries.
Chatbot leads skip Odoo's "You have been assigned" email; the chat email links the
lead.

## Changes and recovery

Take a database dump before changing the add-on or CRM configuration
(`kubectl -n corp exec deploy/odoo -c odoo -- pg_dump …`, or the nightly archive in
[operations](../operations.md#backups-and-recovery)). Uninstalling CRM or Live Chat
does not restore the earlier schema; recovery means restoring the dump.

To stop lead intake, archive the integration user (its keys stop working) and remove
the website's pod label. To hide the chat, archive the channel's rule or remove the
widget from the website.

## Related Guides

- [Application onboarding](application-onboarding.md)
- [Operations](../operations.md)
- [Security](../security.md)
