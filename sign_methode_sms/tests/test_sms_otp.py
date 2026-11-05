# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from unittest.mock import patch

from odoo.tests import tagged

from odoo.addons.sign_methode.models import sign_signer
from odoo.addons.sign_methode.tests.common import SignCase
from odoo.addons.sms.tests.common import SMSCase


@tagged("post_install", "-at_install")
class TestSmsOtp(SignCase, SMSCase):
    def test_sms_code(self):
        self.alice.phone = "+33612345678"
        req = self.new_request(partners=self.alice, otp_channel="sms")
        signer = req.signer_ids
        with self.mockSMSGateway(), patch.object(
            sign_signer.secrets, "randbelow", return_value=246810
        ):
            target = signer._send_otp()
        self.assertTrue(target.endswith("78"))
        self.assertSMSIapSent(["+33612345678"], content="246810")
        self.assertIsNone(signer._verify_otp("246810"))
        self.assertIn("SMS", signer.auth_method_label)
