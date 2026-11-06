# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Public signing routes. The link token is the only thing in the URL; it is
looked up by hash, and every route but the page shell and ``info`` also needs
the identity check (OTP) passed in this browser session."""

import logging

from werkzeug.exceptions import Forbidden, NotFound

from odoo import http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import content_disposition, request

_logger = logging.getLogger(__name__)


class SignMethodeController(http.Controller):
    def _signer(self, token):
        signer = request.env["sign.methode.signer"]._from_token(token)
        if not signer:
            raise NotFound()
        self._set_lang(signer)
        return signer.with_context(lang=request.env.lang)

    def _set_lang(self, signer):
        """Speak the signer's language, whatever the browser's session says."""
        lang = signer.signatory_id.lang or signer.request_id.company_id.partner_id.lang
        installed = dict(request.env["res.lang"].get_installed())
        if lang not in installed:
            return
        request.update_context(lang=lang)
        # The frontend's JS translations follow the session's language. Only
        # an anonymous visitor's session is switched: a logged-in user signing
        # their own part keeps theirs.
        if request.env.user._is_public() and request.session.context.get("lang") != lang:
            request.session.context = dict(request.session.context, lang=lang)

    def _call(self, token, method, *args):
        signer = self._signer(token)
        try:
            result = getattr(signer, method)(*args)
        except (UserError, AccessDenied) as e:
            request.env.cr.rollback()
            return {"error": str(e.args[0] if e.args else e)}
        if isinstance(result, dict) and result.get("error"):
            # Refusals that must persist (a wrong code counts as an attempt).
            return {"error": result["error"]}
        return {"ok": True, "result": result, "info": signer._portal_info()}

    @http.route("/sign/<string:token>", type="http", auth="public", website=False)
    def sign_page(self, token, **kw):
        signer = request.env["sign.methode.signer"]._from_token(token)
        if not signer:
            return request.render(
                "sign_methode.signer_invalid",
                {"html_data": {"lang": request.env.lang.replace("_", "-")}},
                status=404,
            )
        self._set_lang(signer)
        return request.render(
            "sign_methode.signer_page",
            {
                # The frontend loads its translations for <html lang>.
                "html_data": {"lang": request.env.lang.replace("_", "-")},
                "token": token,
                "company": signer.request_id.company_id,
                "document": signer.request_id.name,
            },
        )

    @http.route("/sign/<string:token>/info", type="jsonrpc", auth="public")
    def sign_info(self, token):
        signer = self._signer(token)
        opened = request.session.get("sign_methode_opened", [])
        if signer.id not in opened:
            signer.request_id._log("open", signer=signer)
            request.session["sign_methode_opened"] = opened + [signer.id]
        return signer._portal_info()

    @http.route("/sign/<string:token>/otp/send", type="jsonrpc", auth="public")
    def sign_otp_send(self, token):
        return self._call(token, "_send_otp")

    @http.route("/sign/<string:token>/otp/verify", type="jsonrpc", auth="public")
    def sign_otp_verify(self, token, code):
        return self._call(token, "_verify_otp", code)

    @http.route("/sign/<string:token>/submit", type="jsonrpc", auth="public")
    def sign_submit(self, token, values, consent=False):
        return self._call(token, "_sign", values, consent)

    @http.route("/sign/<string:token>/decline", type="jsonrpc", auth="public")
    def sign_decline(self, token, reason=""):
        return self._call(token, "_decline", reason)

    def _pdf_response(self, attachment, filename, download):
        disposition = "attachment" if download else "inline"
        return request.make_response(
            attachment.raw,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", content_disposition(filename, disposition)),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    @http.route("/sign/<string:token>/document", type="http", auth="public")
    def sign_document(self, token, **kw):
        signer = self._signer(token)
        if not signer._is_verified():
            raise Forbidden()
        req = signer.request_id
        return self._pdf_response(
            req.document_attachment_id.sudo(), f"{req.name}.pdf", download=False
        )

    @http.route("/sign/<string:token>/download", type="http", auth="public")
    def sign_download(self, token, **kw):
        signer = self._signer(token)
        req = signer.request_id
        if not signer._is_verified() or req.state != "completed":
            raise Forbidden()
        req._log("download", signer=signer, details={"file": "signed"})
        return self._pdf_response(
            req.signed_attachment_id.sudo(),
            req.signed_attachment_id.name,
            download=True,
        )
