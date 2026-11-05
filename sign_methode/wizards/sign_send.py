# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
from datetime import timedelta

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from ..models.res_company import OTP_CHANNELS


class SignMethodeSend(models.TransientModel):
    """"Send for signature": the one screen a sender sees."""

    _name = "sign.methode.send"
    _description = "Send for Signature"

    res_model = fields.Char()
    res_id = fields.Integer()
    has_record_document = fields.Boolean(compute="_compute_has_record_document")
    name = fields.Char("Document Name", required=True)
    document = fields.Binary("PDF to Sign")
    document_name = fields.Char("File Name")
    template_id = fields.Many2one(
        "sign.methode.template",
        "Layout",
        help="Leave empty for the simple flow: a signature page is added at the "
        "end of the document, nothing to place.",
    )
    signer_ids = fields.One2many("sign.methode.send.signer", "wizard_id", "Signers")
    sequential = fields.Boolean("Sign in Order")
    otp_channel = fields.Selection(
        OTP_CHANNELS,
        "Signer Verification",
        required=True,
        default=lambda self: self.env.company.sign_methode_otp_channel,
    )
    expire_on = fields.Date(
        "Valid Until",
        required=True,
        default=lambda self: fields.Date.today()
        + timedelta(days=self.env.company.sign_methode_expiry_days or 30),
    )
    reminder_days = fields.Integer(
        "Remind Every (days)",
        default=lambda self: self.env.company.sign_methode_reminder_days,
    )
    message = fields.Text()

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        record = self._get_record_from(vals.get("res_model"), vals.get("res_id"))
        if record:
            vals.setdefault("name", record._sign_get_document_name())
            vals["signer_ids"] = [
                Command.create(dict(s, sequence=i))
                for i, s in enumerate(record._sign_get_signers(), start=1)
            ]
            template = self.env["sign.methode.template"].search(
                [("model", "=", record._name)], limit=1
            )
            if template:
                vals["template_id"] = template.id
        return vals

    def _get_record_from(self, model, res_id):
        if not model or not res_id or model not in self.env:
            return None
        record = self.env[model].browse(res_id).exists()
        return record if record and hasattr(record, "_sign_get_document") else None

    def _get_record(self):
        return self._get_record_from(self.res_model, self.res_id)

    @api.depends("res_model", "res_id")
    def _compute_has_record_document(self):
        for wizard in self:
            wizard.has_record_document = bool(wizard._get_record())

    @api.onchange("template_id")
    def _onchange_template_id(self):
        template = self.template_id
        if not template:
            return
        self.sequential = template.sequential
        self.otp_channel = template.otp_channel
        record = self._get_record()
        existing = {line.role_id.id: line for line in self.signer_ids if line.role_id}
        unassigned = [line for line in self.signer_ids if not line.role_id]
        commands = []
        for seq, role in enumerate(template.role_ids.sorted("sequence"), start=1):
            if role.id in existing:
                continue
            partner = role._get_partner(record) if record else False
            if not partner and unassigned:
                line = unassigned.pop(0)
                line.role_id = role
                continue
            commands.append(
                Command.create(
                    {"role_id": role.id, "partner_id": partner.id if partner else False, "sequence": seq}
                )
            )
        if commands:
            self.signer_ids = commands

    def _create_request(self):
        self.ensure_one()
        if not self.signer_ids:
            raise UserError(self.env._("Add at least one signer."))
        if any(not line.partner_id for line in self.signer_ids):
            raise UserError(self.env._("Choose a contact for every signer."))
        record = self._get_record()
        template = self.template_id
        if self.document:
            filename, pdf = self.document_name or self.name, base64.b64decode(self.document)
        elif template and not (template.use_record_document and record):
            filename, pdf = template.document_name, base64.b64decode(template.document)
        elif record:
            filename, pdf = record._sign_get_document()
        else:
            filename = pdf = None
        if not pdf:
            raise UserError(self.env._("Add the PDF to sign."))
        request = self.env["sign.methode.request"].create(
            {
                "name": self.name,
                "res_model": record._name if record else False,
                "res_id": record.id if record else False,
                "template_id": template.id,
                "mode": "fields" if template else "page",
                "sequential": self.sequential,
                "otp_channel": self.otp_channel,
                "expire_on": self.expire_on,
                "reminder_days": self.reminder_days,
                "message": self.message,
                "signer_ids": [
                    Command.create(
                        {
                            "partner_id": line.partner_id.id,
                            "role_id": line.role_id.id,
                            "sequence": index,
                            "otp_channel": line.otp_channel or self.otp_channel,
                        }
                    )
                    for index, line in enumerate(self.signer_ids.sorted("sequence"), 1)
                ],
            }
        )
        request._set_source(pdf, filename)
        return request

    def _open(self, request):
        return {
            "type": "ir.actions.act_window",
            "res_model": "sign.methode.request",
            "res_id": request.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_send(self):
        request = self._create_request()
        request.action_send()
        return self._open(request)

    def action_save_draft(self):
        return self._open(self._create_request())


class SignMethodeSendSigner(models.TransientModel):
    _name = "sign.methode.send.signer"
    _description = "Send for Signature: Signer"
    _order = "sequence, id"

    wizard_id = fields.Many2one("sign.methode.send", required=True, ondelete="cascade")
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one("res.partner", "Contact")
    email = fields.Char(related="partner_id.email", readonly=False)
    phone = fields.Char(related="partner_id.phone", readonly=False, string="Mobile")
    role_id = fields.Many2one("sign.methode.role", "Role")
    otp_channel = fields.Selection(OTP_CHANNELS, "Verification")
