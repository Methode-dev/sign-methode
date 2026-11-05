# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import json

from odoo import fields, models
from odoo.exceptions import UserError

from . import sign_pdf
from .sign_template import AUTO_TYPES, FIELD_TYPES

MAX_TEXT = 1000


class SignMethodeItem(models.Model):
    """A field of one request, copied from the template when it is sent.

    Placement lives here, on the server; the signing page only reads it and
    sends values back.
    """

    _name = "sign.methode.item"
    _description = "Signature Request Field"
    _order = "request_id, page, y, x, id"

    request_id = fields.Many2one(
        "sign.methode.request", required=True, ondelete="cascade", index=True
    )
    signer_id = fields.Many2one(
        "sign.methode.signer", required=True, ondelete="cascade", index=True
    )
    type = fields.Selection(FIELD_TYPES, required=True)
    required = fields.Boolean(default=True)
    page = fields.Integer(required=True)
    x = fields.Float(required=True, digits=(6, 5))
    y = fields.Float(required=True, digits=(6, 5))
    w = fields.Float(required=True, digits=(6, 5))
    h = fields.Float(required=True, digits=(6, 5))
    placeholder = fields.Char()
    value = fields.Text(help="JSON-encoded value entered by the signer.")
    filled_on = fields.Datetime()

    def _clean_value(self, value):
        self.ensure_one()
        if self.type in ("signature", "initials"):
            if not value:
                return None
            try:
                sign_pdf.decode_png(value)
            except sign_pdf.SignPdfError as e:
                raise UserError(self.env._("Invalid signature image (%s).", e)) from e
            return value
        if self.type == "checkbox":
            return bool(value)
        if value is None:
            return None
        if not isinstance(value, str):
            raise UserError(self.env._("Invalid value in the submission."))
        return value.strip()[:MAX_TEXT] or None

    def _stamp_vals(self):
        self.ensure_one()
        return {
            "type": self.type,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "value": json.loads(self.value) if self.value else None,
        }

    def _portal_vals(self):
        self.ensure_one()
        return {
            "id": self.id,
            "type": self.type,
            "auto": self.type in AUTO_TYPES,
            "required": self.required,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "placeholder": self.placeholder or "",
        }
