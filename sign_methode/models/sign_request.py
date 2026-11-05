# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import format_datetime

from . import sign_pdf
from .res_company import OTP_CHANNELS
from .sign_template import AUTO_TYPES

_logger = logging.getLogger(__name__)

STATES = [
    ("draft", "Draft"),
    ("sent", "Waiting for signatures"),
    ("completed", "Signed"),
    ("declined", "Declined"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
]
# Written by the engine only (as superuser): the evidence of the request.
EVIDENCE_FIELDS = {
    "state",
    "reference",
    "source_attachment_id",
    "source_sha256",
    "document_attachment_id",
    "document_sha256",
    "signed_attachment_id",
    "signed_sha256",
    "seal_level",
    "sent_on",
    "completed_on",
    "item_ids",
    "event_ids",
}
# What the signers were asked to sign: frozen once the request is sent.
FROZEN_FIELDS = {
    "name",
    "mode",
    "template_id",
    "res_model",
    "res_id",
    "sequential",
    "otp_channel",
    "signer_ids",
    "company_id",
    "message",
}

SEAL_LEVELS = [
    ("none", "Not sealed"),
    ("B-B", "PAdES B-B"),
    ("B-T", "PAdES B-T (timestamped)"),
    ("B-LTA", "PAdES B-LTA (long-term)"),
]


class SignMethodeRequest(models.Model):
    """One document sent to one or more signers."""

    _name = "sign.methode.request"
    _description = "Signature Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Document", required=True, tracking=True)
    reference = fields.Char(readonly=True, copy=False, index=True, default="/")
    state = fields.Selection(
        STATES, default="draft", required=True, tracking=True, index=True, copy=False
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    user_id = fields.Many2one(
        "res.users", "Requested By", default=lambda self: self.env.user, tracking=True
    )
    template_id = fields.Many2one("sign.methode.template", ondelete="set null")
    mode = fields.Selection(
        [
            ("page", "Signature page at the end"),
            ("fields", "Fields placed on the document"),
        ],
        default="page",
        required=True,
    )
    res_model = fields.Char("Related Model", index=True)
    res_id = fields.Many2oneReference("Related Record", model_field="res_model")
    record_name = fields.Char(compute="_compute_record_name")
    message = fields.Text(help="Personal message added to the invitation.")
    sequential = fields.Boolean("Sign in Order")
    otp_channel = fields.Selection(
        OTP_CHANNELS,
        "Signer Verification",
        required=True,
        default=lambda self: self.env.company.sign_methode_otp_channel,
    )
    expire_on = fields.Date(
        "Valid Until",
        default=lambda self: fields.Date.today()
        + timedelta(days=self.env.company.sign_methode_expiry_days or 30),
    )
    reminder_days = fields.Integer(
        "Remind Every (days)",
        default=lambda self: self.env.company.sign_methode_reminder_days,
    )

    source_attachment_id = fields.Many2one(
        "ir.attachment", "Source Document", readonly=True, copy=False
    )
    source_sha256 = fields.Char("Source SHA-256", readonly=True, copy=False)
    document_attachment_id = fields.Many2one(
        "ir.attachment",
        "Document to Sign",
        readonly=True,
        copy=False,
        help="The exact file shown to signers (source plus signature page).",
    )
    document_sha256 = fields.Char("Document SHA-256", readonly=True, copy=False)
    signed_attachment_id = fields.Many2one(
        "ir.attachment", "Signed Document", readonly=True, copy=False
    )
    signed_sha256 = fields.Char("Signed SHA-256", readonly=True, copy=False)
    seal_level = fields.Selection(SEAL_LEVELS, readonly=True, copy=False)
    sent_on = fields.Datetime(readonly=True, copy=False)
    completed_on = fields.Datetime(readonly=True, copy=False)

    signer_ids = fields.One2many(
        "sign.methode.signer", "request_id", "Signers", copy=True
    )
    item_ids = fields.One2many("sign.methode.item", "request_id", "Fields")
    event_ids = fields.One2many("sign.methode.event", "request_id", "Audit Trail")
    signed_count = fields.Integer(compute="_compute_progress")
    signer_count = fields.Integer(compute="_compute_progress")
    progress = fields.Char(compute="_compute_progress")
    integrity_ok = fields.Boolean("Audit Trail Intact", compute="_compute_integrity")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            forged = (EVIDENCE_FIELDS - {"reference", "state"}) & set(vals)
            if not self.env.su and (forged or vals.get("state", "draft") != "draft"):
                raise AccessError(self.env._("Requests start as drafts."))
            if vals.get("reference", "/") == "/":
                vals["reference"] = (
                    self.env["ir.sequence"].next_by_code("sign.methode.request") or "/"
                )
        requests = super().create(vals_list)
        for req in requests:
            req._log("create")
        return requests

    def write(self, vals):
        if not self.env.su:
            if EVIDENCE_FIELDS & set(vals):
                raise AccessError(
                    self.env._("The evidence of a signature request cannot be edited.")
                )
            if FROZEN_FIELDS & set(vals) and any(r.state != "draft" for r in self):
                raise UserError(
                    self.env._("A sent request cannot be changed; cancel it instead.")
                )
        return super().write(vals)

    def unlink(self):
        if any(r.state != "draft" for r in self):
            raise UserError(
                self.env._("Only drafts can be deleted; cancel the request instead.")
            )
        return super(
            SignMethodeRequest, self.with_context(sign_methode_purge=True)
        ).unlink()

    @api.depends("res_model", "res_id")
    def _compute_record_name(self):
        for req in self:
            record = req._get_record()
            req.record_name = record.display_name if record else False

    @api.depends("signer_ids.state")
    def _compute_progress(self):
        for req in self:
            req.signer_count = len(req.signer_ids)
            req.signed_count = len(req.signer_ids.filtered(lambda s: s.state == "signed"))
            req.progress = f"{req.signed_count} / {req.signer_count}"

    def _compute_integrity(self):
        for req in self:
            req.integrity_ok = req.sudo().event_ids._check_chain()

    def _get_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id or self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists() or None

    def _log(self, event_type, signer=None, details=None, actor=None):
        self.ensure_one()
        return self.env["sign.methode.event"]._log(
            self, event_type, signer=signer, details=details, actor=actor
        )

    def _attach(self, name, data, suffix=""):
        self.ensure_one()
        return (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": f"{name}{suffix}.pdf",
                    "raw": data,
                    "mimetype": "application/pdf",
                    "res_model": self._name,
                    "res_id": self.id,
                }
            )
        )

    # ---------------------------------------------------------------------
    # Building the request
    # ---------------------------------------------------------------------
    def _set_source(self, pdf_bytes, filename=None):
        """Store the file to sign, as received (before any normalisation)."""
        self.ensure_one()
        if self.state != "draft":
            raise UserError(self.env._("The document of a sent request is frozen."))
        try:
            sign_pdf.normalize_pdf(pdf_bytes)
        except sign_pdf.SignPdfError as e:
            raise UserError(self.env._("This file cannot be signed: %s", e)) from e
        self.source_attachment_id.sudo().unlink()
        stem = (filename or self.name).removesuffix(".pdf")
        self.sudo().write(
            {
                "source_attachment_id": self._attach(stem, pdf_bytes).id,
                "source_sha256": sign_pdf.sha256_hex(pdf_bytes),
            }
        )

    def _prepare_document(self):
        """Freeze the document signers will see and place every item."""
        self.ensure_one()
        source = self.source_attachment_id.raw
        if not source:
            raise UserError(self.env._("Attach the PDF to sign first."))
        doc = sign_pdf.normalize_pdf(source)
        pages = sign_pdf.page_count(doc)
        self.sudo().item_ids.unlink()
        Item = self.env["sign.methode.item"].sudo()
        signers = self.signer_ids.sorted("sequence")
        if self.mode == "page":
            extra, placements = sign_pdf.signature_pages(
                self.env._("Signatures"),
                f"{self.name} — {self.reference}",
                [
                    {
                        "name": s.name,
                        "email": s.email,
                        "role": s.role_id.name or "",
                    }
                    for s in signers
                ],
                {
                    "signature": self.env._("Signature"),
                    "date": self.env._("Signed on:"),
                    "footer": self.env._(
                        "Electronically signed. The certificate of completion "
                        "at the end of this document lists the evidence."
                    ),
                },
            )
            doc = sign_pdf.append_pdf(doc, extra)
            for placement in placements:
                signer = signers[placement.pop("signer_index")]
                Item.create(
                    dict(
                        placement,
                        page=placement["page"] + pages,
                        request_id=self.id,
                        signer_id=signer.id,
                    )
                )
        else:
            if not self.template_id.field_ids:
                raise UserError(self.env._("The template has no fields."))
            by_role = {}
            for signer in signers:
                by_role.setdefault(signer.role_id.id, signer)
            for field in self.template_id.field_ids:
                signer = by_role.get(field.role_id.id)
                if not signer:
                    raise UserError(
                        self.env._(
                            "No signer has the role %s required by the template.",
                            field.role_id.name,
                        )
                    )
                if field.page >= pages:
                    raise UserError(
                        self.env._(
                            "The template places a field on page %(page)s but the "
                            "document only has %(count)s pages.",
                            page=field.page + 1,
                            count=pages,
                        )
                    )
                Item.create(
                    {
                        "request_id": self.id,
                        "signer_id": signer.id,
                        "type": field.type,
                        "required": field.required or field.type in AUTO_TYPES,
                        "page": field.page,
                        "x": field.x,
                        "y": field.y,
                        "w": field.w,
                        "h": field.h,
                        "placeholder": field.placeholder,
                    }
                )
            missing = signers.filtered(
                lambda s: not s.item_ids.filtered(lambda i: i.type == "signature")
            )
            if missing:
                raise UserError(
                    self.env._(
                        "%s has no signature field in this template.",
                        ", ".join(missing.mapped("name")),
                    )
                )
        self.sudo().write(
            {
                "document_attachment_id": self._attach(self.name, doc, "").id,
                "document_sha256": sign_pdf.sha256_hex(doc),
            }
        )

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------
    def action_send(self):
        for req in self:
            if req.state != "draft":
                raise UserError(self.env._("Only drafts can be sent."))
            if not req.signer_ids:
                raise UserError(self.env._("Add at least one signer."))
            for signer in req.signer_ids:
                signer._check_reachable()
            req._prepare_document()
            req.sudo().write({"state": "sent", "sent_on": fields.Datetime.now()})
            req._log(
                "send",
                details={
                    "document_sha256": req.document_sha256,
                    "source_sha256": req.source_sha256,
                    "signers": [
                        {"name": s.name, "email": s.email, "channel": s.otp_channel}
                        for s in req.signer_ids
                    ],
                },
            )
            waiting = req.signer_ids.sorted("sequence")
            if req.sequential:
                waiting[1:].sudo().write({"state": "waiting"})
                waiting = waiting[:1]
            waiting.sudo().write({"state": "to_sign"})
            for signer in waiting:
                signer._notify("invite")
        return True

    def action_cancel(self):
        for req in self:
            if req.state in ("completed", "cancelled"):
                continue
            req.signer_ids.filtered(lambda s: s.state != "signed")._revoke()
            req.sudo().state = "cancelled"
            req._log("cancel")
        return True

    def action_remind(self):
        for req in self.filtered(lambda r: r.state == "sent"):
            for signer in req.signer_ids.filtered(lambda s: s.state == "to_sign"):
                signer._notify("remind")
        return True

    def action_reset_draft(self):
        for req in self:
            if req.state not in ("cancelled", "expired", "declined"):
                raise UserError(self.env._("Only closed requests go back to draft."))
            req.sudo().item_ids.unlink()
            req.signer_ids.sudo().write(
                {"state": "draft", "signed_on": False, "decline_reason": False}
            )
            req.signer_ids._reset_links()
            req.sudo().write({"document_attachment_id": False, "state": "draft"})
        return True

    def _signer_signed(self, signer):
        self.ensure_one()
        if all(s.state == "signed" for s in self.signer_ids):
            self._complete()
            return
        if self.sequential:
            following = self.signer_ids.filtered(lambda s: s.state == "waiting").sorted(
                "sequence"
            )[:1]
            if following:
                following.sudo().state = "to_sign"
                following._notify("invite")

    def _signer_declined(self, signer):
        self.ensure_one()
        self.sudo().state = "declined"
        (self.signer_ids - signer).filtered(lambda s: s.state != "signed")._revoke()
        self.message_post(
            body=self.env._(
                "%(name)s declined to sign: %(reason)s",
                name=signer.name,
                reason=signer.decline_reason or "-",
            ),
            partner_ids=self.user_id.partner_id.ids,
            subtype_xmlid="mail.mt_comment",
        )
        record = self._get_record()
        if record and hasattr(record, "_sign_on_declined"):
            record._sign_on_declined(self)

    # ---------------------------------------------------------------------
    # Completion: stamp, certificate of completion, PAdES seal
    # ---------------------------------------------------------------------
    def _format_dt(self, dt, signer=None):
        tz = (signer and signer.partner_id.tz) or self.user_id.tz or "UTC"
        lang = (signer and signer.partner_id.lang) or self.env.lang
        return "%s (%s)" % (
            format_datetime(
                self.env(context=dict(self.env.context, lang=lang)),
                dt,
                tz=tz,
                dt_format="short",
            ),
            tz,
        )

    def _evidence_data(self):
        self.ensure_one()
        _ = self.env._
        channel_labels = dict(OTP_CHANNELS)
        sections = [
            {
                "title": _("Document"),
                "rows": [
                    [_("Name"), self.name],
                    [_("Reference"), self.reference],
                    [_("Requested by"), self.user_id.name],
                    [_("Company"), self.company_id.name],
                    [_("Sent on"), self._format_dt(self.sent_on)],
                    [_("Completed on"), self._format_dt(self.completed_on)],
                ],
            },
            {
                "title": _("Fingerprints (SHA-256)"),
                "mono": True,
                "rows": [
                    [_("Original file"), self.source_sha256],
                    [_("Document presented to signers"), self.document_sha256],
                ],
            },
        ]
        for signer in self.signer_ids.sorted("sequence"):
            sections.append(
                {
                    "title": _("Signer: %s", signer.name),
                    "rows": [
                        [_("E-mail"), signer.email],
                        [_("Role"), signer.role_id.name or "-"],
                        [
                            _("Identity check"),
                            signer.auth_method_label
                            or channel_labels.get(signer.otp_channel),
                        ],
                        [_("Signed on (server)"), self._format_dt(signer.signed_on)],
                        [
                            _("Timestamp authority"),
                            _(
                                "%(time)s, serial %(serial)s",
                                time=self._format_dt(signer.tsa_time),
                                serial=signer.tsa_serial,
                            )
                            if signer.tsa_time
                            else _("not timestamped"),
                        ],
                        [_("IP address"), signer.ip or "-"],
                        [_("Device"), signer.user_agent or "-"],
                        [_("Signed values (SHA-256)"), signer.values_sha256],
                    ],
                }
            )
        events = self.sudo().event_ids.sorted("sequence")
        type_labels = dict(self.env["sign.methode.event"]._fields["type"].selection)
        return {
            "title": _("Certificate of completion"),
            "intro": _(
                "This page is part of the signed document. Each signature below "
                "was collected after the signer's identity check and bound to the "
                "document fingerprint; the whole file is sealed with %(company)s's "
                "electronic seal and timestamped.",
                company=self.company_id.name,
            ),
            "sections": sections,
            "events": {
                "title": _("Audit trail"),
                "header": [_("Date (UTC)"), _("Event"), _("Actor"), _("IP")],
                "rows": [
                    [
                        fields.Datetime.to_string(e.date),
                        type_labels.get(e.type, e.type),
                        e.actor,
                        e.ip or "",
                    ]
                    for e in events
                ],
            },
            "footer": _(
                "Audit trail head: %(hash)s (%(count)s events, hash-chained).",
                hash=events[-1:].hash,
                count=len(events),
            ),
        }

    def _complete(self):
        self.ensure_one()
        doc = self.document_attachment_id.sudo().raw
        if sign_pdf.sha256_hex(doc) != self.document_sha256:
            raise UserError(
                self.env._(
                    "The document of %s changed after it was sent; it cannot be "
                    "completed.",
                    self.reference,
                )
            )
        self.sudo().write({"state": "completed", "completed_on": fields.Datetime.now()})
        self._log("complete")
        stamped = sign_pdf.stamp(doc, [i._stamp_vals() for i in self.item_ids])
        final = sign_pdf.append_pdf(stamped, sign_pdf.evidence_pages(self._evidence_data()))
        final, level = self._seal(final)
        attachment = self._attach(self.name, final, " - " + self.env._("signed"))
        self.sudo().write(
            {
                "signed_attachment_id": attachment.id,
                "signed_sha256": sign_pdf.sha256_hex(final),
                "seal_level": level,
            }
        )
        self._log("seal", details={"level": level, "sha256": self.signed_sha256})
        self.message_post(
            body=self.env._("All parties signed. Signature level: %s", level),
            attachment_ids=attachment.ids,
        )
        for signer in self.signer_ids:
            signer._notify("completed")
        record = self._get_record()
        if record and hasattr(record, "_sign_on_completed"):
            record._sign_on_completed(self)

    def _seal(self, pdf_bytes):
        """Apply the company seal (PAdES), one signature per signer."""
        company = self.company_id
        signer = company._sign_methode_seal_signer()
        if not signer:
            _logger.warning("%s: no seal certificate, document not sealed", self.reference)
            return pdf_bytes, "none"
        timestamper = company._sign_methode_timestamper()
        vc = None
        if company.sign_methode_ltv:
            vc = company._sign_methode_validation_context(signer)
        entries = [
            {
                "field": f"Signature{index}",
                "name": s.name,
                "reason": self.env._(
                    "Signed electronically by %(name)s <%(email)s> — %(method)s",
                    name=s.name,
                    email=s.email,
                    method=s.auth_method_label or "-",
                ),
                "location": s.ip or None,
            }
            for index, s in enumerate(self.signer_ids.sorted("sequence"), start=1)
        ]
        try:
            return sign_pdf.pades_seal(
                pdf_bytes, signer, entries, timestamper=timestamper, ltv=bool(vc), vc=vc
            )
        except Exception:
            if not vc:
                raise
            _logger.exception(
                "%s: long-term validation data unavailable, sealing without",
                self.reference,
            )
            return sign_pdf.pades_seal(
                pdf_bytes, signer, entries, timestamper=timestamper, ltv=False
            )

    # ---------------------------------------------------------------------
    # Buttons
    # ---------------------------------------------------------------------
    def _download_action(self, attachment):
        self.ensure_one()
        if not attachment:
            raise UserError(self.env._("This file is not available yet."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def action_download_signed(self):
        self.ensure_one()
        self._log("download", details={"file": "signed"})
        return self._download_action(self.signed_attachment_id)

    def action_download_document(self):
        self.ensure_one()
        return self._download_action(
            self.document_attachment_id or self.source_attachment_id
        )

    def action_open_record(self):
        self.ensure_one()
        record = self._get_record()
        if not record:
            raise UserError(self.env._("The related record no longer exists."))
        return {
            "type": "ir.actions.act_window",
            "res_model": record._name,
            "res_id": record.id,
            "view_mode": "form",
        }

    # ---------------------------------------------------------------------
    # Crons
    # ---------------------------------------------------------------------
    @api.model
    def _cron_expire(self):
        expired = self.search(
            [("state", "=", "sent"), ("expire_on", "<", fields.Date.today())]
        )
        for req in expired:
            req.signer_ids.filtered(lambda s: s.state != "signed")._revoke()
            req.sudo().state = "expired"
            req._log("expire")

    @api.model
    def _cron_remind(self):
        now = fields.Datetime.now()
        for req in self.search([("state", "=", "sent"), ("reminder_days", ">", 0)]):
            limit = now - timedelta(days=req.reminder_days)
            for signer in req.signer_ids.filtered(
                lambda s: s.state == "to_sign"
                and s.last_notified_on
                and s.last_notified_on <= limit
            ):
                signer._notify("remind")
