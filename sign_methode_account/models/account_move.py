# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "sign.methode.mixin"]

    def _sign_get_document(self):
        """The invoice PDF the customer received, or a freshly rendered one."""
        self.ensure_one()
        if self.state != "posted" or not self.is_invoice(include_receipts=True):
            raise UserError(self.env._("Only posted invoices can be signed."))
        if self.invoice_pdf_report_id:
            return self.invoice_pdf_report_id.name, self.invoice_pdf_report_id.raw
        pdf, _type = (
            self.env["ir.actions.report"]
            .with_context(force_report_rendering=True)
            ._render_qweb_pdf("account.account_invoices", self.ids)
        )
        return f"{self._get_move_display_name() or self.name}.pdf", pdf

    def _sign_get_signers(self):
        self.ensure_one()
        partner = self.partner_id
        # Invoices go to the invoice address; prefer a person with an e-mail.
        if partner and not partner.email and partner.commercial_partner_id.email:
            partner = partner.commercial_partner_id
        return [{"partner_id": partner.id}] if partner else []

    def _sign_on_completed(self, request):
        attachment = request.signed_attachment_id.sudo().copy(
            {"res_model": self._name, "res_id": self.id}
        )
        self.message_post(
            body=self.env._(
                "%(partner)s signed the invoice (%(ref)s).",
                partner=", ".join(request.signer_ids.mapped("name")),
                ref=request.reference,
            ),
            attachment_ids=attachment.ids,
        )
