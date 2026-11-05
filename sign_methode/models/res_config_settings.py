# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_methode_seal_p12 = fields.Binary(
        related="company_id.sign_methode_seal_p12", readonly=False
    )
    sign_methode_seal_password = fields.Char(
        related="company_id.sign_methode_seal_password", readonly=False
    )
    sign_methode_seal_subject = fields.Char(related="company_id.sign_methode_seal_subject")
    sign_methode_seal_expiry = fields.Datetime(
        related="company_id.sign_methode_seal_expiry"
    )
    sign_methode_tsa_url = fields.Char(
        related="company_id.sign_methode_tsa_url", readonly=False
    )
    sign_methode_tsa_username = fields.Char(
        related="company_id.sign_methode_tsa_username", readonly=False
    )
    sign_methode_tsa_password = fields.Char(
        related="company_id.sign_methode_tsa_password", readonly=False
    )
    sign_methode_ltv = fields.Boolean(related="company_id.sign_methode_ltv", readonly=False)
    sign_methode_trust_roots = fields.Text(
        related="company_id.sign_methode_trust_roots", readonly=False
    )
    sign_methode_otp_channel = fields.Selection(
        related="company_id.sign_methode_otp_channel", readonly=False
    )
    sign_methode_expiry_days = fields.Integer(
        related="company_id.sign_methode_expiry_days", readonly=False
    )
    sign_methode_reminder_days = fields.Integer(
        related="company_id.sign_methode_reminder_days", readonly=False
    )
