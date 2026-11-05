# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import json
from unittest.mock import patch

from odoo.tests import HttpCase, tagged

from odoo.addons.sign_methode.models import sign_signer

from .common import SignCase


@tagged("post_install", "-at_install")
class TestSignHttp(HttpCase, SignCase):
    def rpc(self, url, **params):
        response = self.url_open(
            url,
            data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": params}),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def token_for(self, signer):
        return signer._new_token()

    def test_public_signing_flow(self):
        req = self.new_request(partners=self.alice)
        signer = req.signer_ids
        token = self.token_for(signer)
        base = f"/sign/{token}"

        self.assertEqual(self.url_open("/sign/not-a-token").status_code, 404)
        page = self.url_open(base)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"sign_methode.signer_app", page.content)

        info = self.rpc(f"{base}/info")
        self.assertFalse(info["verified"])
        self.assertNotIn("items", info, "fields stay hidden until the code is checked")
        self.assertEqual(self.url_open(f"{base}/document").status_code, 403)

        with patch.object(sign_signer.secrets, "randbelow", return_value=424242):
            self.assertTrue(self.rpc(f"{base}/otp/send")["ok"])
        self.assertIn("error", self.rpc(f"{base}/otp/verify", code="111111"))
        result = self.rpc(f"{base}/otp/verify", code="424242")
        self.assertTrue(result["info"]["verified"])
        items = result["info"]["items"]
        self.assertEqual({i["type"] for i in items}, {"signature", "date"})

        document = self.url_open(f"{base}/document")
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document.headers["Content-Type"], "application/pdf")

        values = {str(i["id"]): self.png for i in items if not i["auto"]}
        self.assertIn("error", self.rpc(f"{base}/submit", values=values, consent=False))
        result = self.rpc(f"{base}/submit", values=values, consent=True)
        self.assertEqual(result["info"]["request_state"], "completed")
        req.invalidate_recordset()
        self.assertEqual(req.state, "completed")

        download = self.url_open(f"{base}/download")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.content.startswith(b"%PDF"))
        self.assertIn("attachment", download.headers["Content-Disposition"])

        # Another browser with the same link must pass the code again.
        self.opener.cookies.clear()
        self.assertEqual(self.url_open(f"{base}/download").status_code, 403)
        types = req.event_ids.mapped("type")
        for expected in ("open", "otp_send", "otp_fail", "otp_ok", "sign", "seal", "download"):
            self.assertIn(expected, types)
        self.assertTrue(req.integrity_ok)

    def test_signer_tour(self):
        req = self.new_request(partners=self.alice, otp_channel="none")
        self.start_tour(
            f"/sign/{req.signer_ids._new_token()}", "sign_methode_signer_tour", login=None
        )
        self.assertEqual(req.state, "completed")
        self.assertEqual(req.seal_level, "B-LTA")
