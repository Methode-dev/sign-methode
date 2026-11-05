# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import io
from contextlib import contextmanager
from unittest.mock import patch

from asn1crypto import keys, pem, x509
from PIL import Image, ImageDraw

from odoo.tests import TransactionCase
from odoo.tools.misc import file_open

from odoo.addons.sign_methode.models import sign_pdf


def data_file(name):
    with file_open(f"sign_methode/tests/data/{name}", "rb") as f:
        return f.read()


def _der(name):
    _type, _headers, der = pem.unarmor(data_file(name))
    return der


def test_ca():
    return x509.Certificate.load(_der("test_ca.pem"))


def dummy_timestamper():
    return sign_pdf.timestamps.DummyTimeStamper(
        x509.Certificate.load(_der("test_tsa_cert.pem")),
        keys.PrivateKeyInfo.load(_der("test_tsa_key.pem")),
    )


def signature_png():
    image = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).line((10, 80, 290, 20), fill=(17, 24, 39, 255), width=5)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class SignCase(TransactionCase):
    """A company with the test seal and TSA, two signers, a 2-page PDF."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.write(
            {
                "sign_methode_seal_p12": base64.b64encode(data_file("test_seal.p12")),
                "sign_methode_seal_password": "test",
                "sign_methode_tsa_url": "http://tsa.invalid",
                "sign_methode_ltv": True,
            }
        )
        cls.pdf = data_file("test_contract.pdf")
        Partner = cls.env["res.partner"]
        cls.alice = Partner.create({"name": "Alice Martin", "email": "alice@example.com"})
        cls.bob = Partner.create({"name": "Bob Durand", "email": "bob@example.com"})
        cls.png = signature_png()
        cls.patches = [
            patch.object(
                type(cls.env["res.company"]),
                "_sign_methode_timestamper",
                lambda self: dummy_timestamper(),
            ),
            patch.object(
                type(cls.env["res.company"]),
                "_sign_methode_validation_context",
                lambda self, signer: sign_pdf.validation_context(
                    signer, [test_ca()], fetch=False
                ),
            ),
        ]
        # wkhtmltopdf fetches the report assets over HTTP, which deadlocks on
        # the test transaction: keep the QWeb rendering, stub the conversion.
        cls.patches.append(
            patch.object(
                type(cls.env["ir.actions.report"]),
                "_run_wkhtmltopdf",
                lambda self, *args, **kwargs: cls.pdf,
            )
        )
        for p in cls.patches:
            p.start()
            cls.addClassCleanup(p.stop)

    def new_request(self, partners=None, **vals):
        wizard = self.env["sign.methode.send"].create(
            dict(
                {
                    "name": "Partnership agreement",
                    "document": base64.b64encode(self.pdf),
                    "document_name": "agreement.pdf",
                    "signer_ids": [
                        (0, 0, {"partner_id": p.id, "sequence": i})
                        for i, p in enumerate(partners or (self.alice, self.bob))
                    ],
                },
                **vals,
            )
        )
        action = wizard.action_send()
        return self.env["sign.methode.request"].browse(action["res_id"])

    @contextmanager
    def verified(self):
        with patch.object(
            type(self.env["sign.methode.signer"]), "_is_verified", lambda self: True
        ):
            yield

    def fill(self, signer):
        return {
            str(item.id): self.png if item.type in ("signature", "initials") else "x"
            for item in signer.item_ids
            if item.type not in ("name", "date")
        }

    def sign(self, signer):
        with self.verified():
            signer._sign(self.fill(signer), consent=True)
