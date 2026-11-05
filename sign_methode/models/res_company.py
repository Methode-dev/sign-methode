# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .sign_pdf import (
    SignPdfError,
    http_timestamper,
    load_pem_certificates,
    load_seal,
    seal_certificate_info,
    validation_context,
)

OTP_CHANNELS = [
    ("email", "E-mail code"),
    ("sms", "SMS code"),
    ("none", "None (link only)"),
]


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_methode_seal_p12 = fields.Binary(
        "Seal Certificate (PKCS#12)", attachment=True, groups="base.group_system"
    )
    sign_methode_seal_password = fields.Char(
        "Seal Certificate Password", groups="base.group_system"
    )
    sign_methode_seal_subject = fields.Char(
        "Seal Subject", compute="_compute_sign_methode_seal", store=True
    )
    sign_methode_seal_expiry = fields.Datetime(
        "Seal Expiry", compute="_compute_sign_methode_seal", store=True
    )
    sign_methode_tsa_url = fields.Char(
        "Timestamp Authority URL",
        help="RFC 3161 TSA. Use a qualified TSA in production.",
    )
    sign_methode_tsa_username = fields.Char(
        "TSA Username", groups="base.group_system"
    )
    sign_methode_tsa_password = fields.Char(
        "TSA Password", groups="base.group_system"
    )
    sign_methode_trust_roots = fields.Text(
        "Additional Trust Roots",
        help="PEM certificates trusted on top of the system store when long-"
        "term validation data is gathered: the root of the TSA, or of the seal "
        "certificate's CA.",
    )
    sign_methode_ltv = fields.Boolean(
        "Long-term Validation",
        default=True,
        help="Embed revocation data and a document timestamp (PAdES B-LTA).",
    )
    sign_methode_otp_channel = fields.Selection(
        OTP_CHANNELS, "Default Signer Verification", default="email", required=True
    )
    sign_methode_expiry_days = fields.Integer("Default Validity (days)", default=30)
    sign_methode_reminder_days = fields.Integer("Remind Every (days)", default=3)

    @api.depends("sign_methode_seal_p12", "sign_methode_seal_password")
    def _compute_sign_methode_seal(self):
        """Read the certificate as it is uploaded, so a bad file or password
        is refused in the settings rather than at the first signature."""
        for company in self:
            seal = company.sudo()
            if not seal.sign_methode_seal_p12:
                company.sign_methode_seal_subject = False
                company.sign_methode_seal_expiry = False
                continue
            try:
                info = seal_certificate_info(
                    base64.b64decode(seal.sign_methode_seal_p12),
                    seal.sign_methode_seal_password,
                )
            except SignPdfError as e:
                raise ValidationError(
                    self.env._("The seal certificate cannot be used: %s", e)
                ) from e
            company.sign_methode_seal_subject = info["subject"]
            company.sign_methode_seal_expiry = info["not_after"].replace(tzinfo=None)

    # -- crypto material, overridable (tests, HSM or QTSP connectors) ---------
    def _sign_methode_seal_signer(self):
        """The pyHanko signer of the company seal, or None if none is set."""
        self.ensure_one()
        me = self.sudo()
        if not me.sign_methode_seal_p12:
            return None
        return load_seal(
            base64.b64decode(me.sign_methode_seal_p12), me.sign_methode_seal_password
        )

    def _sign_methode_timestamper(self):
        self.ensure_one()
        me = self.sudo()
        if not me.sign_methode_tsa_url:
            return None
        return http_timestamper(
            me.sign_methode_tsa_url,
            me.sign_methode_tsa_username,
            me.sign_methode_tsa_password,
        )

    def _sign_methode_validation_context(self, signer):
        self.ensure_one()
        return validation_context(
            signer, load_pem_certificates(self.sudo().sign_methode_trust_roots)
        )

    @api.constrains("sign_methode_trust_roots")
    def _check_sign_methode_trust_roots(self):
        for company in self:
            try:
                load_pem_certificates(company.sign_methode_trust_roots)
            except SignPdfError as e:
                raise ValidationError(str(e)) from e
