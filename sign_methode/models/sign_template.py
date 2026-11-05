# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_company import OTP_CHANNELS
from .sign_pdf import SignPdfError, normalize_pdf, page_count

FIELD_TYPES = [
    ("signature", "Signature"),
    ("initials", "Initials"),
    ("name", "Full Name"),
    ("date", "Signing Date"),
    ("text", "Text"),
    ("checkbox", "Checkbox"),
]
# Filled by the server when the signer signs, never by the client.
AUTO_TYPES = ("name", "date")


class SignMethodeTemplate(models.Model):
    """A PDF with signature fields placed once, reused for many requests."""

    _name = "sign.methode.template"
    _description = "Signature Template"
    _inherit = ["mail.thread"]
    _order = "name, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", default=lambda self: self.env.company, index=True
    )
    document = fields.Binary(required=True, attachment=True)
    document_name = fields.Char()
    page_count = fields.Integer(compute="_compute_page_count", store=True)
    model_id = fields.Many2one(
        "ir.model",
        "Applies To",
        ondelete="cascade",
        domain=[("transient", "=", False)],
        help="Offer this template when sending a record of this model.",
    )
    model = fields.Char(related="model_id.model", store=True, index=True)
    use_record_document = fields.Boolean(
        help="When sent from a record, sign the record's own PDF (e.g. the "
        "invoice) with this template's field layout instead of the template "
        "file. The layouts must match."
    )
    field_ids = fields.One2many("sign.methode.template.field", "template_id", "Fields")
    role_ids = fields.Many2many(
        "sign.methode.role", compute="_compute_role_ids", string="Roles"
    )
    sequential = fields.Boolean(
        "Sign in Order", help="Each signer is invited once the previous one signed."
    )
    otp_channel = fields.Selection(
        OTP_CHANNELS,
        "Signer Verification",
        default=lambda self: self.env.company.sign_methode_otp_channel,
        required=True,
    )
    request_ids = fields.One2many("sign.methode.request", "template_id")
    request_count = fields.Integer(compute="_compute_request_count")

    @api.depends("document")
    def _compute_page_count(self):
        for template in self:
            try:
                template.page_count = (
                    page_count(base64.b64decode(template.document))
                    if template.document
                    else 0
                )
            except Exception:
                template.page_count = 0

    @api.depends("field_ids.role_id")
    def _compute_role_ids(self):
        for template in self:
            template.role_ids = template.field_ids.role_id

    def _compute_request_count(self):
        groups = self.env["sign.methode.request"]._read_group(
            [("template_id", "in", self.ids)], ["template_id"], ["__count"]
        )
        counts = {template.id: count for template, count in groups}
        for template in self:
            template.request_count = counts.get(template.id, 0)

    @api.constrains("document")
    def _check_document(self):
        for template in self:
            try:
                normalize_pdf(base64.b64decode(template.document))
            except SignPdfError as e:
                raise ValidationError(
                    self.env._("The template file must be a readable PDF (%s).", e)
                ) from e

    def action_open_editor(self):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "sign_methode.template_editor",
            "name": self.name,
            "params": {"template_id": self.id},
        }

    def action_view_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "sign_methode.action_sign_request"
        )
        action["domain"] = [("template_id", "=", self.id)]
        return action

    # -- template editor RPCs ------------------------------------------------
    def editor_get_data(self):
        self.ensure_one()
        roles = self.env["sign.methode.role"].search([])
        return {
            "id": self.id,
            "name": self.name,
            "readonly": not self.env.user.has_group("sign_methode.group_manager"),
            "pdf_url": f"/web/content/sign.methode.template/{self.id}/document",
            "roles": [{"id": r.id, "name": r.name, "color": r.color} for r in roles],
            "types": [{"key": k, "label": label} for k, label in FIELD_TYPES],
            "fields": [f._editor_vals() for f in self.field_ids],
        }

    def editor_save_fields(self, field_list):
        """Replace the template's fields with the editor's state."""
        self.ensure_one()
        if not self.env.user.has_group("sign_methode.group_manager"):
            raise UserError(self.env._("Only signature managers can edit templates."))
        # Requests copy the fields when they are sent: editing a template never
        # moves a field under a signer's feet.
        keep = self.env["sign.methode.template.field"]
        Field = self.env["sign.methode.template.field"]
        valid_types = dict(FIELD_TYPES)
        for vals in field_list:
            clean = {
                "role_id": int(vals["role_id"]),
                "type": vals["type"] if vals["type"] in valid_types else "text",
                "required": bool(vals.get("required", True)),
                "page": int(vals["page"]),
                "x": float(vals["x"]),
                "y": float(vals["y"]),
                "w": float(vals["w"]),
                "h": float(vals["h"]),
                "placeholder": vals.get("placeholder") or False,
            }
            field = Field.browse(int(vals["id"])) if vals.get("id") else Field
            if field and field.template_id == self:
                field.write(clean)
            else:
                field = Field.create(dict(clean, template_id=self.id))
            keep |= field
        (self.field_ids - keep).unlink()
        return [f._editor_vals() for f in self.field_ids]


class SignMethodeTemplateField(models.Model):
    _name = "sign.methode.template.field"
    _description = "Signature Template Field"
    _order = "page, y, x, id"

    template_id = fields.Many2one(
        "sign.methode.template", required=True, ondelete="cascade", index=True
    )
    role_id = fields.Many2one("sign.methode.role", required=True, ondelete="restrict")
    type = fields.Selection(FIELD_TYPES, required=True, default="signature")
    required = fields.Boolean(default=True)
    page = fields.Integer(required=True, help="0-based page index.")
    x = fields.Float(required=True, digits=(6, 5))
    y = fields.Float(required=True, digits=(6, 5))
    w = fields.Float(required=True, digits=(6, 5))
    h = fields.Float(required=True, digits=(6, 5))
    placeholder = fields.Char(translate=True)

    @api.constrains("x", "y", "w", "h", "page")
    def _check_box(self):
        for field in self:
            if (
                field.page < 0
                or field.w <= 0
                or field.h <= 0
                or field.x < 0
                or field.y < 0
                or field.x + field.w > 1.0001
                or field.y + field.h > 1.0001
            ):
                raise ValidationError(self.env._("A field lies outside its page."))

    def _editor_vals(self):
        self.ensure_one()
        return {
            "id": self.id,
            "role_id": self.role_id.id,
            "type": self.type,
            "required": self.required,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "placeholder": self.placeholder or "",
        }
