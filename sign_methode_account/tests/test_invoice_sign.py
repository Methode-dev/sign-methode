# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.sign_methode.models import sign_pdf
from odoo.addons.sign_methode.tests.common import SignCase


@tagged("post_install", "-at_install")
class TestInvoiceSign(SignCase, AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invoice = cls.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.alice.id,
                "invoice_line_ids": [
                    Command.create({"name": "Underwriting fee", "quantity": 1, "price_unit": 1200})
                ],
            }
        )

    def test_invoice_signature(self):
        with self.assertRaises(UserError, msg="drafts are not signed"):
            self.invoice._sign_get_document()
        self.invoice.action_post()
        action = self.invoice.action_sign_send()
        wizard = (
            self.env["sign.methode.send"]
            .with_context(action["context"])
            .create({})
        )
        self.assertEqual(wizard.signer_ids.partner_id, self.alice)
        req = self.env["sign.methode.request"].browse(wizard.action_send()["res_id"])
        self.assertEqual(self.invoice.sign_state, "sent")
        self.assertGreaterEqual(sign_pdf.page_count(req.document_attachment_id.raw), 2)
        self.sign(req.signer_ids)
        self.assertEqual(req.state, "completed")
        self.assertEqual(self.invoice.sign_state, "completed")
        self.assertIn(
            req.signed_sha256,
            [sign_pdf.sha256_hex(a.raw) for a in self.invoice.message_ids.attachment_ids],
        )
