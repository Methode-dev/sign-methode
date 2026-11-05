# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models
from odoo.exceptions import UserError


class SignMethodeMixin(models.AbstractModel):
    """Make any model signable.

    Inherit it, add the smart button and the "Send for signature" button to
    the form (see ``sign_methode_account``), and override what differs:

    * ``_sign_get_document()`` -> ``(filename, pdf_bytes)``; defaults to the
      model's first PDF report;
    * ``_sign_get_signers()`` -> list of ``{"partner_id", "role_id"}``;
    * ``_sign_on_completed(request)`` / ``_sign_on_declined(request)``.
    """

    _name = "sign.methode.mixin"
    _description = "Signable Document"

    sign_request_ids = fields.One2many(
        "sign.methode.request",
        "res_id",
        "Signature Requests",
        domain=lambda self: [("res_model", "=", self._name)],
    )
    sign_request_count = fields.Integer(compute="_compute_sign_state")
    sign_state = fields.Selection(
        [
            ("none", "Not sent"),
            ("sent", "Waiting for signatures"),
            ("completed", "Signed"),
            ("declined", "Declined"),
        ],
        "Signature",
        compute="_compute_sign_state",
    )

    @api.depends("sign_request_ids.state")
    def _compute_sign_state(self):
        for record in self:
            requests = record.sign_request_ids.filtered(
                lambda r: r.state in ("sent", "completed", "declined")
            ).sorted("id")
            record.sign_request_count = len(record.sign_request_ids)
            record.sign_state = requests[-1:].state or "none"

    # -- overridable hooks -----------------------------------------------------
    def _sign_get_report(self):
        return (
            self.env["ir.actions.report"]
            .sudo()
            .search(
                [("model", "=", self._name), ("report_type", "=", "qweb-pdf")],
                order="id",
                limit=1,
            )
        )

    def _sign_get_document(self):
        """The PDF to sign for this record, as ``(filename, bytes)``."""
        self.ensure_one()
        report = self._sign_get_report()
        if report:
            # Always a real PDF, also under --test-enable (Odoo returns HTML
            # there unless asked).
            pdf, _type = report.with_context(
                force_report_rendering=True
            )._render_qweb_pdf(report.report_name, self.ids)
            return f"{self.display_name}.pdf", pdf
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("mimetype", "=", "application/pdf"),
            ],
            order="id desc",
            limit=1,
        )
        if attachment:
            return attachment.name, attachment.raw
        return None, None

    def _sign_get_signers(self):
        """Default signers: the record's partner, if it has one."""
        self.ensure_one()
        partner = self["partner_id"] if "partner_id" in self._fields else None
        return [{"partner_id": partner.id}] if partner else []

    def _sign_get_document_name(self):
        self.ensure_one()
        return self.display_name

    def _sign_on_completed(self, request):
        if hasattr(self, "message_post"):
            self.message_post(
                body=self.env._("The document was signed by every party."),
                attachment_ids=request.signed_attachment_id.copy(
                    {"res_model": self._name, "res_id": self.id}
                ).ids,
            )

    def _sign_on_declined(self, request):
        if hasattr(self, "message_post"):
            self.message_post(
                body=self.env._("Signature declined: %s", request.reference)
            )

    # -- buttons ---------------------------------------------------------------
    def action_sign_send(self):
        self.ensure_one()
        if self.sign_request_ids.filtered(lambda r: r.state == "sent"):
            raise UserError(
                self.env._("A signature request for this document is in progress.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Send for signature"),
            "res_model": "sign.methode.send",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }

    def action_view_sign_requests(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "sign_methode.action_sign_request"
        )
        action["domain"] = [("res_model", "=", self._name), ("res_id", "=", self.id)]
        action["context"] = {"create": False}
        if len(self.sign_request_ids) == 1:
            action.update(view_mode="form", res_id=self.sign_request_ids.id, views=[(False, "form")])
        return action
