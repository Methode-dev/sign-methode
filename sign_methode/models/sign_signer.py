# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta, timezone

from odoo import api, fields, models
from odoo.exceptions import AccessDenied, AccessError, UserError, ValidationError
from odoo.http import request as http_request

from . import sign_pdf
from .res_company import OTP_CHANNELS
from .sign_template import AUTO_TYPES

_logger = logging.getLogger(__name__)

OTP_VALIDITY = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_DELAY = timedelta(seconds=45)
OTP_MAX_SENDS = 10
MAX_TEXT = 1000

SIGNER_STATES = [
    ("draft", "Draft"),
    ("waiting", "Waiting for previous signer"),
    ("to_sign", "To sign"),
    ("signed", "Signed"),
    ("declined", "Declined"),
    ("revoked", "Closed"),
]


# Written by the engine only (as superuser): the evidence of the signature.
EVIDENCE_FIELDS = {
    "state",
    "signed_on",
    "ip",
    "user_agent",
    "values_sha256",
    "tsa_token",
    "tsa_time",
    "tsa_serial",
    "decline_reason",
    "verified_on",
    "auth_method_label",
    "last_notified_on",
}
# Who is asked to sign: frozen once the request is sent.
FROZEN_FIELDS = {
    "request_id",
    "partner_id",
    "name",
    "email",
    "phone",
    "role_id",
    "sequence",
    "otp_channel",
}


def _token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SignMethodeSigner(models.Model):
    _name = "sign.methode.signer"
    _description = "Signer"
    _order = "request_id, sequence, id"

    request_id = fields.Many2one(
        "sign.methode.request", required=True, ondelete="cascade", index=True
    )
    request_state = fields.Selection(related="request_id.state", string="Request Status")
    sequence = fields.Integer(default=10)
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict")
    role_id = fields.Many2one("sign.methode.role", ondelete="restrict")
    color = fields.Integer(related="role_id.color")
    # Snapshots: the evidence must show who was asked, not who the partner
    # became later.
    name = fields.Char(required=True)
    email = fields.Char(required=True)
    phone = fields.Char("Mobile")
    state = fields.Selection(SIGNER_STATES, default="draft", required=True, copy=False)
    otp_channel = fields.Selection(OTP_CHANNELS, "Verification", required=True)

    token_ids = fields.One2many(
        "sign.methode.signer.token", "signer_id", groups="base.group_system"
    )
    # Verified browser sessions are tied to it: a new one (request reset to
    # draft) asks every browser for a new code.
    session_salt = fields.Char(
        copy=False,
        default=lambda self: secrets.token_hex(16),
        groups="base.group_system",
    )
    last_notified_on = fields.Datetime(readonly=True, copy=False)
    otp_hash = fields.Char(copy=False, groups="base.group_system")
    otp_expire_on = fields.Datetime(copy=False, groups="base.group_system")
    otp_attempts = fields.Integer(copy=False, groups="base.group_system")
    otp_sent_count = fields.Integer(copy=False, groups="base.group_system")
    otp_sent_on = fields.Datetime(copy=False, groups="base.group_system")
    verified_on = fields.Datetime("Identity Verified On", readonly=True, copy=False)
    auth_method_label = fields.Char("Identity Check", readonly=True, copy=False)

    item_ids = fields.One2many("sign.methode.item", "signer_id")
    signed_on = fields.Datetime(readonly=True, copy=False)
    ip = fields.Char("IP Address", readonly=True, copy=False)
    user_agent = fields.Char(readonly=True, copy=False)
    values_sha256 = fields.Char("Values SHA-256", readonly=True, copy=False)
    tsa_token = fields.Binary("Timestamp Token", readonly=True, copy=False, attachment=True)
    tsa_time = fields.Datetime("Timestamp", readonly=True, copy=False)
    tsa_serial = fields.Char("Timestamp Serial", readonly=True, copy=False)
    decline_reason = fields.Text(readonly=True, copy=False)

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        for signer in self:
            signer.name = signer.partner_id.name
            signer.email = signer.partner_id.email
            signer.phone = signer.partner_id.phone

    @api.model_create_multi
    def create(self, vals_list):
        Partner = self.env["res.partner"]
        Request = self.env["sign.methode.request"]
        for vals in vals_list:
            if not self.env.su and (
                EVIDENCE_FIELDS & set(vals)
                or Request.browse(vals.get("request_id")).state not in (False, "draft")
            ):
                raise AccessError(self.env._("Signers are added to draft requests only."))
            partner = Partner.browse(vals.get("partner_id"))
            vals.setdefault("name", partner.name)
            vals.setdefault("email", partner.email)
            vals.setdefault("phone", partner.phone)
            if not vals.get("otp_channel"):
                req = self.env["sign.methode.request"].browse(vals.get("request_id"))
                vals["otp_channel"] = req.otp_channel or "email"
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if EVIDENCE_FIELDS & set(vals):
                raise AccessError(self.env._("The evidence of a signature cannot be edited."))
            if FROZEN_FIELDS & set(vals) and any(
                s.request_id.state != "draft" for s in self
            ):
                raise UserError(self.env._("The signers of a sent request are frozen."))
        return super().write(vals)

    @api.constrains("email")
    def _check_email(self):
        for signer in self:
            if signer.email and "@" not in signer.email:
                raise ValidationError(self.env._("%s is not an e-mail address.", signer.email))

    def _check_reachable(self):
        for signer in self:
            if not signer.email:
                raise UserError(self.env._("%s has no e-mail address.", signer.name))
            if signer.otp_channel == "sms" and not signer.phone:
                raise UserError(
                    self.env._("%s has no mobile number for the SMS code.", signer.name)
                )

    # ---------------------------------------------------------------------
    # Access links. Only a hash of each token is stored: the link exists in
    # the e-mail and nowhere else. Every link sent stays valid (a reminder
    # does not break the invitation the signer may already have open); what
    # it allows follows the signer's and the request's state, and acting
    # always needs the identity check.
    # ---------------------------------------------------------------------
    def _new_token(self, kind="sign"):
        self.ensure_one()
        token = secrets.token_urlsafe(32)
        self.env["sign.methode.signer.token"].sudo().create(
            {"signer_id": self.id, "token_hash": _token_hash(token), "kind": kind}
        )
        return token

    def _revoke(self):
        """Close the signer's part. Links keep showing that it is closed."""
        self.sudo().write({"otp_hash": False})
        self.sudo().filtered(lambda s: s.state in ("draft", "waiting", "to_sign")).write(
            {"state": "revoked"}
        )

    def _reset_links(self):
        """Forget every link and verified session (request back to draft)."""
        me = self.sudo()
        me.token_ids.unlink()
        for signer in me:
            signer.session_salt = secrets.token_hex(16)

    @api.model
    def _from_token(self, token):
        """The signer a link belongs to, as superuser, or an empty recordset."""
        if not token or len(token) > 100:
            return self.browse()
        link = (
            self.env["sign.methode.signer.token"]
            .sudo()
            .search([("token_hash", "=", _token_hash(token))], limit=1)
        )
        return link.signer_id.with_context(active_test=False)

    def _sign_url(self, token):
        base = self.request_id.get_base_url()
        return f"{base}/sign/{token}"

    def _notify(self, kind):
        """E-mail the signer; ``kind`` is invite, remind or completed."""
        self.ensure_one()
        templates = {
            "invite": "sign_methode.mail_template_invite",
            "remind": "sign_methode.mail_template_invite",
            "completed": "sign_methode.mail_template_completed",
        }
        ctx = {"sign_kind": kind}
        email_values = {}
        if kind in ("invite", "remind"):
            ctx["sign_url"] = self._sign_url(self._new_token())
            self.sudo().last_notified_on = fields.Datetime.now()
        elif kind == "completed":
            # A separate link, so the one the signer may still have open in
            # their browser keeps working.
            ctx["sign_url"] = self._sign_url(self._new_token("download"))
            email_values["attachment_ids"] = self.request_id.signed_attachment_id.ids
        template = self.env.ref(templates[kind]).sudo()
        template.with_context(**ctx).send_mail(
            self.id,
            email_layout_xmlid="mail.mail_notification_light",
            email_values=email_values,
        )
        if kind != "completed":
            self.request_id._log("remind" if kind == "remind" else "notify", signer=self)
        # Out now, not at the next run of the mail queue.
        cron = self.env.ref("mail.ir_cron_mail_scheduler_action", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    # ---------------------------------------------------------------------
    # One-time codes
    # ---------------------------------------------------------------------
    def _otp_digest(self, code):
        secret = self.env["ir.config_parameter"].sudo().get_param("database.secret")
        return hmac.new(
            secret.encode(), f"{self.id}:{code}".encode(), hashlib.sha256
        ).hexdigest()

    def _is_verified(self):
        """Whether this browser session passed the identity check."""
        self.ensure_one()
        if self.otp_channel == "none":
            return True
        if self.env.user.partner_id == self.partner_id and not self.env.user._is_public():
            return True
        if not http_request:
            return False
        verified = http_request.session.get("sign_methode_verified", {})
        return verified.get(str(self.id)) == self._session_key()

    def _session_key(self):
        me = self.sudo()
        return _token_hash(f"{me.session_salt}:{me.id}")

    def _mark_verified(self, method_label):
        self.ensure_one()
        if http_request:
            verified = dict(http_request.session.get("sign_methode_verified", {}))
            verified[str(self.id)] = self._session_key()
            http_request.session["sign_methode_verified"] = verified
        self.sudo().write(
            {"verified_on": fields.Datetime.now(), "auth_method_label": method_label}
        )

    def _send_otp(self):
        self.ensure_one()
        me = self.sudo()
        now = fields.Datetime.now()
        if me.otp_sent_on and now - me.otp_sent_on < OTP_RESEND_DELAY:
            raise UserError(self.env._("Please wait a few seconds before asking again."))
        if me.otp_sent_count >= OTP_MAX_SENDS:
            raise UserError(
                self.env._("Too many codes were requested. Ask the sender for a new link.")
            )
        code = f"{secrets.randbelow(10**6):06d}"
        me.write(
            {
                "otp_hash": self._otp_digest(code),
                "otp_expire_on": now + OTP_VALIDITY,
                "otp_attempts": 0,
                "otp_sent_count": me.otp_sent_count + 1,
                "otp_sent_on": now,
            }
        )
        if self.otp_channel == "sms":
            self._send_otp_sms(code)
            target = self._mask(self.phone, keep=2)
        else:
            self.env.ref("sign_methode.mail_template_otp").sudo().with_context(
                sign_otp=code
            ).send_mail(self.id, force_send=True)
            target = self._mask_email(self.email)
        self.request_id._log("otp_send", signer=self, details={"to": target})
        return target

    def _send_otp_sms(self, code):
        """Overridden by sign_methode_sms."""
        raise UserError(self.env._("SMS codes need the 'Méthode Sign SMS' module."))

    def _verify_otp(self, code):
        self.ensure_one()
        me = self.sudo()
        now = fields.Datetime.now()
        code = (code or "").strip().replace(" ", "")
        if not me.otp_hash or not me.otp_expire_on or me.otp_expire_on < now:
            raise UserError(self.env._("This code has expired. Ask for a new one."))
        if me.otp_attempts >= OTP_MAX_ATTEMPTS:
            raise UserError(self.env._("Too many wrong codes. Ask for a new one."))
        if not hmac.compare_digest(me.otp_hash, self._otp_digest(code)):
            me.otp_attempts += 1
            self.request_id._log("otp_fail", signer=self)
            left = OTP_MAX_ATTEMPTS - me.otp_attempts
            # Returned, not raised: the attempt counter must not roll back.
            return {
                "error": self.env._("Wrong code, %s attempt(s) left.", left)
                if left
                else self.env._("Too many wrong codes. Ask for a new one.")
            }
        me.write({"otp_hash": False, "otp_expire_on": False})
        target = (
            self._mask(self.phone, keep=2)
            if self.otp_channel == "sms"
            else self._mask_email(self.email)
        )
        label = (
            self.env._("SMS code sent to %s", target)
            if self.otp_channel == "sms"
            else self.env._("E-mail code sent to %s", target)
        )
        self._mark_verified(label)
        self.request_id._log("otp_ok", signer=self)

    @staticmethod
    def _mask(value, keep=2):
        value = value or ""
        return "•" * max(len(value) - keep, 0) + value[-keep:]

    @staticmethod
    def _mask_email(email):
        local, _sep, domain = (email or "").partition("@")
        return f"{local[:2]}•••@{domain}"

    # ---------------------------------------------------------------------
    # Signing
    # ---------------------------------------------------------------------
    def _check_can_act(self):
        self.ensure_one()
        req = self.request_id
        if req.state != "sent" or self.state != "to_sign":
            raise UserError(self.env._("This document can no longer be signed."))
        if req.expire_on and req.expire_on < fields.Date.today():
            raise UserError(self.env._("This signature request has expired."))
        if not self._is_verified():
            raise AccessDenied(self.env._("Verify your identity first."))

    def _sign(self, values, consent):
        """Record the signer's values, timestamp them, move the request on.

        ``values`` maps item ids to what the signer entered. Placement, type
        and whether a field is required are the server's, never the client's.
        """
        self.ensure_one()
        self._check_can_act()
        if not consent:
            raise UserError(self.env._("Please accept to sign electronically."))
        values = {str(k): v for k, v in (values or {}).items()}
        now = fields.Datetime.now()
        mine = self.item_ids
        unknown = set(values) - {str(i.id) for i in mine}
        if unknown:
            raise UserError(self.env._("Unknown fields in the submission."))
        clean = {}
        for item in mine.sorted(lambda i: (i.page, i.y, i.x)):
            if item.type == "name":
                clean[item.id] = self.name
            elif item.type == "date":
                clean[item.id] = self.request_id._format_dt(now, self)
            else:
                clean[item.id] = item._clean_value(values.get(str(item.id)))
            if item.required and clean[item.id] in (None, "", False):
                raise UserError(self.env._("Please fill in every required field."))
        for item in mine.sudo():
            item.write({"value": json.dumps(clean[item.id]), "filled_on": now})
        values_sha256 = hashlib.sha256(
            json.dumps(
                {str(k): v for k, v in clean.items()}, sort_keys=True
            ).encode()
        ).hexdigest()
        vals = {
            "state": "signed",
            "signed_on": now,
            "values_sha256": values_sha256,
        }
        if http_request:
            vals["ip"] = http_request.httprequest.remote_addr
            vals["user_agent"] = (http_request.httprequest.user_agent.string or "")[:300]
        if not self.auth_method_label:
            vals["auth_method_label"] = (
                self.env._("Logged in as %s", self.env.user.login)
                if self.env.user.partner_id == self.partner_id
                else self.env._("Access link sent to %s", self._mask_email(self.email))
            )
        digest = hashlib.sha256(
            f"{self.request_id.document_sha256}:{values_sha256}:{self.id}".encode()
        ).digest()
        timestamper = self.request_id.company_id._sign_methode_timestamper()
        tsa = {}
        if timestamper:
            try:
                token, gen_time, serial = sign_pdf.timestamp_digest(timestamper, digest)
                vals.update(
                    {
                        "tsa_token": base64.b64encode(token),
                        "tsa_time": gen_time.astimezone(timezone.utc).replace(
                            tzinfo=None
                        ),
                        "tsa_serial": serial,
                    }
                )
                tsa = {"tsa_serial": serial, "tsa_time": str(gen_time)}
            except Exception as e:
                _logger.exception("Timestamp authority unreachable")
                tsa = {"tsa_error": str(e)[:200]}
        self.sudo().write(vals)
        self.request_id._log(
            "sign",
            signer=self,
            details=dict(
                tsa, values_sha256=values_sha256, document=self.request_id.document_sha256
            ),
        )
        self.request_id._signer_signed(self)

    def _decline(self, reason):
        self.ensure_one()
        self._check_can_act()
        self.sudo().write(
            {
                "state": "declined",
                "decline_reason": (reason or "").strip()[:MAX_TEXT],
            }
        )
        self.request_id._log("decline", signer=self, details={"reason": reason})
        self.request_id._signer_declined(self)

    def action_sign_now(self):
        """Back-office shortcut: the logged-in user signs their own part."""
        self.ensure_one()
        if self.partner_id != self.env.user.partner_id:
            raise UserError(self.env._("You can only sign for yourself."))
        if self.state != "to_sign":
            raise UserError(self.env._("It is not your turn to sign."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/sign/{self._new_token()}",
            "target": "self",
        }

    def action_resend(self):
        for signer in self.filtered(lambda s: s.state == "to_sign"):
            signer._notify("remind")
        return True

    # ---------------------------------------------------------------------
    # Data for the signing page
    # ---------------------------------------------------------------------
    def _portal_info(self):
        self.ensure_one()
        req = self.request_id
        verified = self._is_verified()
        info = {
            "state": self.state,
            "request_state": req.state,
            "document": req.name,
            "reference": req.reference,
            "company": req.company_id.name,
            "sender": req.user_id.name,
            "message": req.message or "",
            "signer": {"name": self.name, "email": self.email},
            "expire_on": fields.Date.to_string(req.expire_on) if req.expire_on else False,
            "otp_channel": self.otp_channel,
            "otp_target": self._mask(self.phone, keep=2)
            if self.otp_channel == "sms"
            else self._mask_email(self.email),
            "verified": verified,
            "can_download": req.state == "completed" and verified,
            "others": [
                {"name": s.name, "state": s.state}
                for s in req.signer_ids.sorted("sequence")
                if s != self
            ],
        }
        if verified:
            info["items"] = [
                i._portal_vals() for i in self.item_ids.sorted(lambda i: (i.page, i.y, i.x))
            ]
        return info


class SignMethodeSignerToken(models.Model):
    """One access link sent to a signer (its hash only)."""

    _name = "sign.methode.signer.token"
    _description = "Signer Access Link"
    _order = "id desc"

    signer_id = fields.Many2one(
        "sign.methode.signer", required=True, ondelete="cascade", index=True
    )
    token_hash = fields.Char(required=True, index=True)
    kind = fields.Selection(
        [("sign", "Invitation"), ("download", "Signed copy")],
        required=True,
        default="sign",
    )

    _token_hash_uniq = models.Constraint("UNIQUE(token_hash)", "Links are unique.")
