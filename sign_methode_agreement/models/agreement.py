# Copyright 2026 Méthode
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import fields, models


class Agreement(models.Model):
    _name = "agreement"
    _inherit = ["agreement", "sign.methode.mixin"]

    def _sign_get_signers(self):
        """The counterpart first, then the user who sends it for the company."""
        self.ensure_one()
        signers = []
        if self.partner_id:
            signers.append(
                {
                    "partner_id": self.partner_id.id,
                    "role_id": self.env.ref("sign_methode.role_customer").id,
                }
            )
        signers.append(
            {
                "partner_id": self.env.user.partner_id.id,
                "role_id": self.env.ref("sign_methode.role_company").id,
            }
        )
        return signers

    def _sign_get_document_name(self):
        return self.display_name

    def _sign_on_completed(self, request):
        self.signature_date = fields.Date.context_today(self)
        super()._sign_on_completed(request)
