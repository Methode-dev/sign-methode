# Copyright 2026 Méthode
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields
from odoo.tests import tagged

from odoo.addons.sign_methode.tests.common import SignCase


@tagged("post_install", "-at_install")
class TestAgreementSign(SignCase):
    def test_agreement_signature(self):
        self.env.user.partner_id.email = "admin@example.com"
        agreement = self.env["agreement"].create(
            {"name": "Partnership", "code": "AGR-1", "partner_id": self.alice.id}
        )
        # No report on agreements: the contract PDF is attached to the record.
        self.env["ir.attachment"].create(
            {
                "name": "partnership.pdf",
                "raw": self.pdf,
                "mimetype": "application/pdf",
                "res_model": "agreement",
                "res_id": agreement.id,
            }
        )
        action = agreement.action_sign_send()
        wizard = self.env["sign.methode.send"].with_context(action["context"]).create({})
        # Alice signs in person; the company through the user who sends it.
        alice, company = wizard.signer_ids
        self.assertEqual(alice.partner_id, self.alice)
        self.assertFalse(alice.representative_id)
        self.assertEqual(company.partner_id, self.env.company.partner_id)
        self.assertEqual(company.representative_id, self.env.user.partner_id)
        req = self.env["sign.methode.request"].browse(wizard.action_send()["res_id"])
        for signer in req.signer_ids:
            self.sign(signer)
        self.assertEqual(req.state, "completed")
        self.assertEqual(agreement.sign_state, "completed")
        self.assertEqual(agreement.signature_date, fields.Date.today())
