import re

import psycopg2.errors

from odoo import api, fields, models
from odoo.exceptions import AccessError, ConcurrencyError, ValidationError
from odoo.tools import plaintext2html

REFERENCE = re.compile(r"[A-Za-z0-9._:-]{8,128}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LANGUAGE = re.compile(r"[a-z]{2,3}(_[A-Z]{2})?")
# Field -> maximum length. Everything else (stage, salesperson, company,
# tags, scoring) stays under CRM staff control.
FIELDS = {
    "name": 256,
    "contact_name": 256,
    "partner_name": 256,
    "email_from": 256,
    "phone": 64,
    "description": 10000,
    "lang": 8,
}


class CrmLead(models.Model):
    _inherit = "crm.lead"

    platform_intake_user_id = fields.Many2one("res.users", string="Intake integration", readonly=True, copy=False, index=True)
    platform_intake_reference = fields.Char(string="Intake reference", readonly=True, copy=False)

    _platform_intake_unique = models.Constraint(
        "UNIQUE(platform_intake_user_id, platform_intake_reference)",
        "An integration reference can create only one lead.",
    )

    @api.model
    def platform_intake_submit(self, reference, values):
        """Create one lead per (integration user, reference); retries return the same lead."""
        user = self.env.user
        if not user.has_group("platform_crm_intake.group_crm_intake"):
            raise AccessError(self.env._("This user is not a CRM intake integration."))
        if not isinstance(reference, str) or not REFERENCE.fullmatch(reference):
            raise ValidationError(self.env._("reference must be 8-128 characters of A-Z, a-z, 0-9, '.', '_', ':' or '-'."))
        if not isinstance(values, dict) or set(values) - set(FIELDS):
            raise ValidationError(self.env._("values accepts only: %s.", ", ".join(sorted(FIELDS))))
        for key, value in values.items():
            if not isinstance(value, str) or len(value) > FIELDS[key]:
                raise ValidationError(self.env._("%(field)s must be a string of at most %(size)s characters.", field=key, size=FIELDS[key]))
        if not (values.get("name") or "").strip():
            raise ValidationError(self.env._("name is required."))
        if values.get("email_from") and not EMAIL.fullmatch(values["email_from"]):
            raise ValidationError(self.env._("email_from is not a valid email address."))
        if values.get("lang") and not LANGUAGE.fullmatch(values["lang"]):
            raise ValidationError(self.env._("lang must be a language code such as fr_FR."))

        company = user.company_id
        Lead = self.sudo().with_company(company).with_context(mail_create_nosubscribe=True)
        domain = [("platform_intake_user_id", "=", user.id), ("platform_intake_reference", "=", reference)]
        existing = Lead.with_context(active_test=False).search(domain, limit=1)
        if existing:
            return {"id": existing.id, "created": False}
        lead_values = dict(values)
        # An inactive or unknown language is dropped rather than failing the lead.
        language = lead_values.pop("lang", "")
        if language:
            lang = self.env["res.lang"].sudo().search([("code", "=", language)], limit=1)
            if lang:
                lead_values["lang_id"] = lang.id
        if "description" in lead_values:
            lead_values["description"] = plaintext2html(lead_values["description"])
        # The integration user is not an employee, so follow the company's
        # Leads setting (granted to internal users) rather than its own groups.
        use_leads = self.env.ref("crm.group_use_lead") in self.env.ref("base.group_user").sudo().all_implied_ids
        medium = self.env.ref("utm.utm_medium_website", raise_if_not_found=False)
        # Without a team the lead would be missing from every team pipeline.
        team = self.env["crm.team"].sudo().search([("company_id", "in", [company.id, False])], order="sequence, id", limit=1)
        lead_values.update({
            "team_id": team.id,
            "type": "lead" if use_leads else "opportunity",
            "company_id": company.id,
            "user_id": False,
            "source_id": user.platform_intake_source_id.id,
            "medium_id": medium.id if medium else False,
            "platform_intake_user_id": user.id,
            "platform_intake_reference": reference,
        })
        try:
            with self.env.cr.savepoint():
                lead = Lead.create(lead_values)
        except psycopg2.errors.UniqueViolation:
            # A concurrent retry committed first, invisible to this snapshot;
            # Odoo replays the request in a new transaction.
            raise ConcurrencyError("Concurrent intake submission for the same reference") from None
        return {"id": lead.id, "created": True}
