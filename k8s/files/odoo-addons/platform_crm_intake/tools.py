"""Settings, email texts and reply parsing shared by the add-on's models and routes."""
import re

from odoo.tools import html2plaintext

# System parameters written by scripts/configure-odoo-website.py.
PARAM_PREFIX = "platform_crm_intake."


def param(env, name):
    return env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + name)


TEXTS = {
    "fr": {
        "team_subject": "Chat du site : %(visitor)s",
        "team_from": "%(visitor)s via le chat",
        "team_intro": "Nouveau message de %(visitor)s sur le chat du site.",
        "team_reply": "Répondez simplement à cet e-mail : votre réponse s'affiche dans la conversation, et part par e-mail si le visiteur a quitté le site.",
        "team_open": "Ouvrir la conversation dans Odoo",
        "team_lead": "Piste CRM",
        "conversation": "Conversation",
        "visitor": "Visiteur",
        "you": "Vous",
        "visitor_subject": "Votre conversation avec %(company)s",
        "visitor_intro": "Voici notre réponse à votre message sur le chat :",
        "visitor_reply": "Vous pouvez répondre directement à cet e-mail.",
    },
    "en": {
        "team_subject": "Website chat: %(visitor)s",
        "team_from": "%(visitor)s via chat",
        "team_intro": "New message from %(visitor)s on the website chat.",
        "team_reply": "Just reply to this email: your answer appears in the conversation, and is emailed to the visitor if they have left the website.",
        "team_open": "Open the conversation in Odoo",
        "team_lead": "CRM lead",
        "conversation": "Conversation",
        "visitor": "Visitor",
        "you": "You",
        "visitor_subject": "Your conversation with %(company)s",
        "visitor_intro": "Here is our answer to your message on our chat:",
        "visitor_reply": "You can reply directly to this email.",
    },
}

# Lines where a mail client starts quoting the message being answered, also
# matched against a line joined with the next one ("On <date>, <name>\nwrote:").
QUOTE_LINES = [re.compile(pattern, re.I) for pattern in (
    r"^-{2,}\s*(original message|message d'origine|forwarded message|message transféré)",
    r"^_{10,}$",
    r"^(on|le)\s.+(wrote|a écrit)\s?:$",
)]
# Outlook quotes a header block instead: a From: line followed by Sent:, To: ...
QUOTED_HEADER = re.compile(r"^(from|de)\s?:\s.+", re.I)
QUOTED_HEADER_FIELD = re.compile(r"^(sent|envoyé|date|to|à)\s?:", re.I)


def reply_text(message):
    """Return what the sender wrote, without the quoted conversation or signature."""
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    if part.get_content_type() == "text/html":
        # Outlook puts its quoted header right after an <hr>, which would
        # otherwise join the reply's last line.
        content = html2plaintext(re.sub(r"(?i)<hr\b[^>]*>", "<br/><br/>", content))
    lines = content.replace("\r\n", "\n").split("\n")
    # html2plaintext marks bold labels as *From:*; ignore that markup.
    plain = lambda value: value.replace("*", "").strip()
    kept = []
    for index, line in enumerate(lines):
        current = plain(line)
        joined = " ".join(plain(item) for item in lines[index:index + 2])
        if any(pattern.match(current) or pattern.match(joined) for pattern in QUOTE_LINES):
            break
        if QUOTED_HEADER.match(current) and any(
                QUOTED_HEADER_FIELD.match(plain(item)) for item in lines[index + 1:index + 4]):
            break
        if current.startswith(">"):
            continue
        kept.append(line.rstrip())
    text = re.split(r"\n-- ?\n", "\n".join(kept))[0].strip()
    return text[:5000]
