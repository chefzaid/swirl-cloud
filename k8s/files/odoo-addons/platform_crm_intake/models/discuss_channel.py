"""Email side of the website's Live Chat.

New visitor messages are emailed to the team once the visitor pauses. A reply
to that email is posted in the chat as the conversation's agent (the chatbot
persona when nobody was online), and emailed to the visitor when they are no
longer on the website; the visitor's own email reply returns to the chat.
Reply addresses carry a per-chat signature, and only listed team senders (or
the visitor's own address) are accepted. See docs/guides/odoo-website.md.
"""
import hashlib
import hmac
import logging
import re
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import formataddr, getaddresses, parseaddr

from markupsafe import Markup

from odoo import api, fields, models
from odoo.tools import email_normalize, html2plaintext, plaintext2html

from ..tools import TEXTS, param, reply_text

_logger = logging.getLogger(__name__)

QUIET = timedelta(seconds=45)
REPLY = re.compile(r"(?P<local>[a-z0-9._-]+)\+(?P<channel>\d+)\.(?P<audience>[tv])\.(?P<token>[0-9a-f]{20})@(?P<domain>[a-z0-9.-]+)")


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    platform_notified_message_id = fields.Integer(
        copy=False, help="Last visitor message already emailed to the team.")

    @api.model
    def _platform_chat_config(self):
        get = lambda name: param(self.env, name)
        sender = email_normalize(get("chat_from") or "") or ""
        split = lambda value: [e for e in (email_normalize(item) for item in (value or "").split(",")) if e]
        return {
            "notify": split(get("chat_notify_emails")),
            "senders": set(split(get("chat_reply_senders"))),
            "sender": sender,
            "local": sender.partition("@")[0],
            "domain": sender.partition("@")[2],
            "secret": get("reply_secret") or "",
            "lang": "en" if (get("chat_notify_lang") or "fr").startswith("en") else "fr",
            "tz": get("chat_timezone") or "UTC",
        }

    def _platform_reply_address(self, audience, config):
        token = hmac.new(config["secret"].encode(), f"{self.id}.{audience}".encode(), hashlib.sha256).hexdigest()[:20]
        return f"{config['local']}+{self.id}.{audience}.{token}@{config['domain']}"

    def _platform_is_visitor(self, message):
        return bool(
            (message.author_guest_id and message.author_guest_id in self.livechat_customer_guest_ids)
            or (message.author_id and message.author_id in self.livechat_customer_partner_ids)
        )

    def _platform_visitor_messages(self, after_id=0):
        return self.message_ids.filtered(
            lambda m: m.id > after_id and m.message_type in ("comment", "email") and self._platform_is_visitor(m)
        ).sorted("id")

    def _platform_lead(self):
        return self.env["crm.lead"].sudo().with_context(active_test=False).search(
            [("origin_channel_id", "=", self.id)], order="id desc", limit=1)

    def _platform_visitor_email(self):
        """The visitor's address: from the chat's lead, a chatbot answer, or their account."""
        if address := email_normalize(self._platform_lead().email_from or ""):
            return address
        for message in self._platform_visitor_messages():
            if address := email_normalize(html2plaintext(message.body or "").strip()):
                return address
        return email_normalize(self.livechat_customer_partner_ids[:1].email or "") or False

    def _platform_visitor_label(self, t):
        return self._platform_visitor_email() or self.livechat_customer_partner_ids[:1].name or t["visitor"]

    def _platform_persona(self):
        bot = self.livechat_bot_partner_ids[:1] or self.livechat_channel_id.rule_ids.chatbot_script_id[:1].operator_partner_id
        return (self.livechat_operator_id or bot).name or self.env.company.name

    def _platform_transcript(self, config, visitor, limit=12):
        messages = self.message_ids.filtered(lambda m: m.message_type in ("comment", "email") and m.body).sorted("id")[-limit:]
        local = self.with_context(tz=config["tz"])
        return [{
            "author": visitor if self._platform_is_visitor(m) else (m.author_id.name or ""),
            "time": fields.Datetime.context_timestamp(local, m.create_date).strftime("%d/%m %H:%M"),
            "text": html2plaintext(m.body or "").strip(),
            "visitor": self._platform_is_visitor(m),
        } for m in messages]

    def _platform_send(self, *, subject, email_from, email_to, reply_to, template, values):
        root = f"<platform-chat-{self.id}@{values['config']['domain']}>"
        mail = self.env["mail.mail"].sudo().create({
            "subject": subject,
            "email_from": email_from,
            "email_to": email_to,
            "reply_to": reply_to,
            "body_html": self.env["ir.qweb"]._render(template, values),
            # A shared root keeps every email about this chat in one thread.
            "references": root,
            "headers": repr({"In-Reply-To": root}),
            "auto_delete": True,
        })
        mail.send()

    @api.model
    def _platform_notify_team(self):
        """Cron: email visitor messages to the team once the visitor pauses."""
        config = self._platform_chat_config()
        if not (config["notify"] and config["sender"] and config["secret"]):
            return
        now = fields.Datetime.now()
        channels = self.sudo().search([("channel_type", "=", "livechat"), ("last_interest_dt", ">=", now - timedelta(days=2))])
        for channel in channels:
            new = channel._platform_visitor_messages(channel.platform_notified_message_id)
            if not new or new[-1].create_date > now - QUIET:
                continue
            # What an agent already read in Odoo needs no email.
            agents = channel.channel_member_ids.filtered(lambda m: m.partner_id in channel.livechat_agent_partner_ids)
            seen = max(agents.mapped("seen_message_id.id") or [0])
            unread = new.filtered(lambda m: m.id > seen)
            if unread:
                channel._platform_email_team(unread, config)
            channel.platform_notified_message_id = new[-1].id
            # One commit per chat: a later failure must not resend this email.
            self.env.cr.commit()

    def _platform_email_team(self, messages, config):
        t = TEXTS[config["lang"]]
        visitor = self._platform_visitor_label(t)
        base = self.get_base_url()
        lead = self._platform_lead()
        self._platform_send(
            subject=t["team_subject"] % {"visitor": visitor},
            email_from=formataddr((t["team_from"] % {"visitor": visitor}, config["sender"])),
            email_to=", ".join(config["notify"]),
            reply_to=formataddr((self._platform_persona(), self._platform_reply_address("t", config))),
            template="platform_crm_intake.chat_team_email",
            values={
                "config": config, "t": t, "visitor": visitor, "intro": t["team_intro"] % {"visitor": visitor},
                "new": [html2plaintext(m.body or "").strip() for m in messages],
                "transcript": self._platform_transcript(config, visitor),
                "open_url": f"{base}/odoo/discuss?active_id=discuss.channel_{self.id}",
                "lead_url": lead and f"{base}/odoo/crm/{lead.id}",
            },
        )

    def _platform_email_visitor(self, message, config):
        address = self._platform_visitor_email()
        if not address:
            return
        lang = (self.livechat_lang_id.code or self.livechat_customer_guest_ids[:1].lang or "fr")[:2]
        t = TEXTS["en" if lang == "en" else "fr"]
        company = self.env.company.name
        self._platform_send(
            subject=t["visitor_subject"] % {"company": company},
            email_from=formataddr((f"{self._platform_persona()} · {company}", config["sender"])),
            email_to=address,
            reply_to=formataddr((self._platform_persona(), self._platform_reply_address("v", config))),
            template="platform_crm_intake.chat_visitor_email",
            values={
                "config": config, "t": t, "company": company, "persona": self._platform_persona(),
                "reply": html2plaintext(message.body or "").strip(),
                "transcript": self._platform_transcript(config, t["you"]),
            },
        )

    def _platform_visitor_online(self):
        visitors = [*self.livechat_customer_guest_ids, *self.livechat_customer_partner_ids]
        return not self.livechat_end_dt and any(visitor.im_status == "online" for visitor in visitors)

    @api.model
    def _platform_receive_reply(self, raw, envelope_to=None):
        """Post an emailed reply into its chat. Returns accepted, ignored or forbidden."""
        config = self._platform_chat_config()
        if not (config["sender"] and config["secret"]):
            return "ignored"
        message = BytesParser(policy=policy.default).parsebytes(raw)
        recipients = [envelope_to or ""] + [address for _name, address in getaddresses(
            message.get_all("To", []) + message.get_all("Cc", []) + message.get_all("Delivered-To", []))]
        for recipient in recipients:
            match = REPLY.fullmatch((recipient or "").strip().lower())
            if match and match["local"] == config["local"] and match["domain"] == config["domain"]:
                break
        else:
            return "ignored"
        channel = self.sudo().browse(int(match["channel"])).exists()
        if not channel or channel.channel_type != "livechat" or not hmac.compare_digest(
                channel._platform_reply_address(match["audience"], config), match.group(0)):
            return "ignored"
        sender = email_normalize(parseaddr(message.get("From", ""))[1] or "")
        allowed = config["senders"] if match["audience"] == "t" else {channel._platform_visitor_email()}
        if not sender or sender not in allowed:
            _logger.info("Website chat %s: reply from an address that may not answer it was refused", channel.id)
            return "forbidden"
        text = reply_text(message)
        if not text:
            return "ignored"
        body = Markup(plaintext2html(text))
        if match["audience"] == "t":
            posted = channel.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_comment",
                                          author_id=channel.livechat_operator_id.id)
            if not channel._platform_visitor_online():
                channel._platform_email_visitor(posted, config)
        else:
            # Post as the visitor, the way the chat itself does; the team learns
            # about it through the notification job.
            partner = channel.livechat_customer_partner_ids[:1]
            visitor = channel.with_context(guest=channel.livechat_customer_guest_ids[:1])
            visitor.message_post(body=body, message_type="comment", subtype_xmlid="mail.mt_comment",
                                 **({"author_id": partner.id} if partner else {}))
        return "accepted"
