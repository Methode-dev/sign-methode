# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import models
from odoo.exceptions import UserError


class SignMethodeSigner(models.Model):
    _inherit = "sign.methode.signer"

    def _send_otp_sms(self, code):
        self.ensure_one()
        number = self.signatory_id._phone_format(number=self.phone) or self.phone
        sms = (
            self.env["sms.sms"]
            .sudo()
            .create(
                {
                    "number": number,
                    "partner_id": self.signatory_id.id,
                    "body": self.env._(
                        "%(code)s is your code to sign %(document)s. Valid 10 minutes.",
                        code=code,
                        document=self.request_id.name,
                    ),
                }
            )
        )
        sms._send(unlink_failed=False, unlink_sent=True, raise_exception=False)
        if sms.exists() and sms.state == "error":
            raise UserError(
                self.env._("The SMS could not be sent. Ask the sender to check your number.")
            )
