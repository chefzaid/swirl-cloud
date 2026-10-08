from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    platform_intake_source_id = fields.Many2one(
        "utm.source", string="CRM intake source", copy=False,
        help="Source set on leads submitted by this integration user.")
