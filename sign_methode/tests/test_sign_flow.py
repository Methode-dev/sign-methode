# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessDenied, AccessError, UserError
from odoo.tests import new_test_user, tagged

from odoo.addons.sign_methode.models import sign_pdf, sign_signer

from .common import SignCase, test_ca


@tagged("post_install", "-at_install")
class TestSignFlow(SignCase):
    def test_send_prepares_signature_page(self):
        req = self.new_request()
        self.assertEqual(req.state, "sent")
        self.assertEqual(req.signer_ids.mapped("state"), ["to_sign", "to_sign"])
        # Source kept as received, a signature page appended for signers.
        self.assertEqual(req.source_sha256, sign_pdf.sha256_hex(self.pdf))
        doc = req.document_attachment_id.raw
        self.assertEqual(sign_pdf.page_count(doc), 3)
        self.assertEqual(req.document_sha256, sign_pdf.sha256_hex(doc))
        # A signature and a date per signer, on the appended page.
        self.assertEqual(len(req.item_ids), 4)
        self.assertEqual(set(req.item_ids.mapped("page")), {2})
        self.assertTrue(all(s.sudo().token_ids for s in req.signer_ids))

    def test_full_flow_is_sealed_and_valid(self):
        req = self.new_request()
        alice, bob = req.signer_ids
        self.sign(alice)
        self.assertEqual(alice.state, "signed")
        self.assertTrue(alice.tsa_token and alice.tsa_serial)
        self.assertEqual(req.state, "sent")
        self.sign(bob)
        self.assertEqual(req.state, "completed")
        self.assertEqual(req.seal_level, "B-LTA")
        signed = req.signed_attachment_id.raw
        self.assertEqual(req.signed_sha256, sign_pdf.sha256_hex(signed))
        # Document + signature page + certificate of completion.
        self.assertGreaterEqual(sign_pdf.page_count(signed), 4)
        signer = self.company._sign_methode_seal_signer()
        results = sign_pdf.validate_signed_pdf(
            signed, sign_pdf.validation_context(signer, [test_ca()], fetch=False)
        )
        self.assertEqual([r["kind"] for r in results], ["signature", "signature", "timestamp"])
        for result in results:
            self.assertTrue(result["intact"] and result["valid"] and result["trusted"], result)
        self.assertTrue(req.integrity_ok)
        self.assertEqual(
            req.event_ids.mapped("type")[-3:], ["sign", "complete", "seal"]
        )

    def test_without_seal_document_is_stamped_only(self):
        self.company.sign_methode_seal_p12 = False
        req = self.new_request(partners=self.alice)
        self.sign(req.signer_ids)
        self.assertEqual(req.state, "completed")
        self.assertEqual(req.seal_level, "none")

    def test_identity_check_required(self):
        req = self.new_request()
        with self.assertRaises(AccessDenied):
            req.signer_ids[0]._sign(self.fill(req.signer_ids[0]), consent=True)

    def test_server_decides_fields(self):
        req = self.new_request()
        alice, bob = req.signer_ids
        with self.verified():
            with self.assertRaises(UserError, msg="consent is mandatory"):
                alice._sign(self.fill(alice), consent=False)
            with self.assertRaises(UserError, msg="bob's fields are not alice's"):
                alice._sign(dict(self.fill(alice), **self.fill(bob)), consent=True)
            with self.assertRaises(UserError, msg="required signature missing"):
                alice._sign({}, consent=True)
            bad = {k: "data:image/png;base64,AAAA" for k in self.fill(alice)}
            with self.assertRaises(UserError, msg="not a PNG"):
                alice._sign(bad, consent=True)
            alice._sign(self.fill(alice), consent=True)
            with self.assertRaises(UserError, msg="signs once"):
                alice._sign(self.fill(alice), consent=True)
        # Placement never changed.
        self.assertEqual(set(req.item_ids.mapped("page")), {2})

    def test_sequential(self):
        req = self.new_request(sequential=True)
        alice, bob = req.signer_ids
        self.assertEqual((alice.state, bob.state), ("to_sign", "waiting"))
        self.assertFalse(bob.sudo().token_ids, "not invited before alice signed")
        with self.verified(), self.assertRaises(UserError):
            bob._sign(self.fill(bob), consent=True)
        self.sign(alice)
        self.assertEqual(bob.state, "to_sign")
        self.assertTrue(bob.sudo().token_ids)

    def test_decline_closes_request(self):
        req = self.new_request()
        alice, bob = req.signer_ids
        with self.verified():
            alice._decline("Wrong amount")
        self.assertEqual(req.state, "declined")
        self.assertEqual(bob.state, "revoked")
        with self.verified(), self.assertRaises(UserError):
            bob._sign(self.fill(bob), consent=True)

    def test_expiry_cron(self):
        req = self.new_request()
        req.expire_on = fields.Date.today() - timedelta(days=1)
        self.env["sign.methode.request"]._cron_expire()
        self.assertEqual(req.state, "expired")
        self.assertEqual(set(req.signer_ids.mapped("state")), {"revoked"})

    def test_audit_chain_detects_tampering(self):
        req = self.new_request()
        self.sign(req.signer_ids[0])
        self.assertTrue(req.integrity_ok)
        with self.assertRaises(UserError):
            req.event_ids[0].write({"ip": "1.2.3.4"})
        with self.assertRaises(UserError):
            req.event_ids[0].unlink()
        self.env.cr.execute(
            "UPDATE sign_methode_event SET ip = '6.6.6.6' WHERE id = %s",
            [req.event_ids[1].id],
        )
        req.invalidate_recordset()
        req.event_ids.invalidate_recordset()
        self.assertFalse(req.integrity_ok)

    def test_otp(self):
        req = self.new_request(partners=self.alice)
        signer = req.signer_ids
        with patch.object(sign_signer.secrets, "randbelow", return_value=123456):
            signer._send_otp()
        with self.assertRaises(UserError, msg="no instant resend"):
            signer._send_otp()
        for _attempt in range(sign_signer.OTP_MAX_ATTEMPTS):
            self.assertIn("error", signer._verify_otp("000000"))
        with self.assertRaises(UserError, msg="locked after too many attempts"):
            signer._verify_otp("123456")
        signer.sudo().write({"otp_attempts": 0})
        signer.sudo().otp_expire_on = fields.Datetime.now() - timedelta(seconds=1)
        with self.assertRaises(UserError, msg="expired"):
            signer._verify_otp("123456")
        signer.sudo().write(
            {"otp_sent_on": False, "otp_expire_on": fields.Datetime.now() + timedelta(minutes=5)}
        )
        with patch.object(sign_signer.secrets, "randbelow", return_value=654321):
            signer._send_otp()
        self.assertIsNone(signer._verify_otp(" 654 321 "))
        self.assertTrue(signer.verified_on)
        self.assertIn("E-mail code", signer.auth_method_label)
        self.assertEqual(
            req.event_ids.mapped("type").count("otp_fail"), sign_signer.OTP_MAX_ATTEMPTS
        )

    def test_template_fields(self):
        Role = self.env["sign.methode.role"]
        client, company = Role.create({"name": "Client"}), Role.create({"name": "Us"})
        template = self.env["sign.methode.template"].create(
            {
                "name": "Contract",
                "document": base64.b64encode(self.pdf),
                "field_ids": [
                    (0, 0, {"role_id": client.id, "type": "signature", "page": 1, "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.06}),
                    (0, 0, {"role_id": client.id, "type": "text", "page": 0, "x": 0.1, "y": 0.2, "w": 0.3, "h": 0.03}),
                    (0, 0, {"role_id": company.id, "type": "signature", "page": 1, "x": 0.6, "y": 0.8, "w": 0.3, "h": 0.06}),
                    (0, 0, {"role_id": company.id, "type": "date", "page": 1, "x": 0.6, "y": 0.9, "w": 0.2, "h": 0.03}),
                ],
            }
        )
        wizard = self.env["sign.methode.send"].create(
            {
                "name": "Contract",
                "template_id": template.id,
                "signer_ids": [
                    (0, 0, {"partner_id": self.alice.id, "role_id": client.id, "sequence": 1}),
                    (0, 0, {"partner_id": self.bob.id, "role_id": company.id, "sequence": 2}),
                ],
            }
        )
        req = self.env["sign.methode.request"].browse(wizard.action_send()["res_id"])
        self.assertEqual(req.mode, "fields")
        self.assertEqual(sign_pdf.page_count(req.document_attachment_id.raw), 2)
        alice, bob = req.signer_ids
        self.assertEqual(len(alice.item_ids), 2)
        self.assertEqual(len(bob.item_ids), 2)
        self.sign(alice)
        self.sign(bob)
        self.assertEqual(req.state, "completed")
        date_item = bob.item_ids.filtered(lambda i: i.type == "date")
        self.assertTrue(date_item.value)

    def test_template_role_without_signer(self):
        role = self.env["sign.methode.role"].create({"name": "Witness"})
        template = self.env["sign.methode.template"].create(
            {
                "name": "Contract",
                "document": base64.b64encode(self.pdf),
                "field_ids": [
                    (0, 0, {"role_id": role.id, "type": "signature", "page": 0, "x": 0.1, "y": 0.8, "w": 0.3, "h": 0.06}),
                ],
            }
        )
        with self.assertRaises(UserError):
            self.new_request(template_id=template.id)

    def test_only_drafts_are_deleted(self):
        req = self.new_request()
        with self.assertRaises(UserError):
            req.unlink()
        req.action_cancel()
        self.assertEqual(req.state, "cancelled")
        self.assertEqual(set(req.signer_ids.mapped("state")), {"revoked"})

    def test_refuses_non_pdf(self):
        with self.assertRaises(UserError):
            self.env["sign.methode.send"].create(
                {
                    "name": "x",
                    "document": base64.b64encode(b"hello"),
                    "signer_ids": [(0, 0, {"partner_id": self.alice.id})],
                }
            ).action_send()

    def test_users_cannot_rewrite_evidence(self):
        user = new_test_user(
            self.env, login="sign_sender", groups="sign_methode.group_user"
        )
        req = self.new_request().with_user(user)
        req.sudo().user_id = user
        with self.assertRaises(AccessError):
            req.document_sha256 = "0" * 64
        with self.assertRaises(AccessError):
            req.state = "completed"
        with self.assertRaises(UserError):
            req.name = "Another document"
        with self.assertRaises(UserError):
            req.signer_ids[0].email = "intruder@example.com"
        with self.assertRaises(AccessError):
            req.signer_ids[0].signed_on = fields.Datetime.now()
        with self.assertRaises(AccessError):
            req.item_ids[0].value = '"forged"'
        with self.assertRaises(AccessError):
            self.env["sign.methode.request"].with_user(user).create(
                {"name": "x", "state": "completed"}
            )
        # Still allowed while sent: the deadline.
        req.expire_on = fields.Date.today() + timedelta(days=60)

    def test_tampered_document_is_not_completed(self):
        req = self.new_request(partners=self.alice)
        req.document_attachment_id.sudo().raw = self.pdf
        with self.assertRaises(UserError):
            self.sign(req.signer_ids)

    def test_reminder_keeps_earlier_links(self):
        Signer = self.env["sign.methode.signer"]
        req = self.new_request(partners=self.alice)
        signer = req.signer_ids
        first = signer._new_token()
        req.action_remind()
        self.assertEqual(len(signer.sudo().token_ids), 3, "invitation, first, reminder")
        self.assertEqual(Signer._from_token(first), signer)
        self.assertIn("remind", req.event_ids.mapped("type"))
        req.action_cancel()
        self.assertEqual(Signer._from_token(first), signer, "shows the closed request")
        req.action_reset_draft()
        self.assertFalse(Signer._from_token(first), "a reset forgets old links")
