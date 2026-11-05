# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import hashlib
import json

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.http import request as http_request

EVENT_TYPES = [
    ("create", "Created"),
    ("send", "Sent"),
    ("notify", "Invitation sent"),
    ("remind", "Reminder sent"),
    ("open", "Opened"),
    ("otp_send", "Code sent"),
    ("otp_ok", "Code verified"),
    ("otp_fail", "Wrong code"),
    ("sign", "Signed"),
    ("decline", "Declined"),
    ("complete", "Completed"),
    ("seal", "Sealed"),
    ("download", "Downloaded"),
    ("cancel", "Cancelled"),
    ("expire", "Expired"),
]
GENESIS = "0" * 64


class SignMethodeEvent(models.Model):
    """Append-only audit trail of a request, hash-chained.

    Each event's ``hash`` covers its own content and the previous event's
    hash, so editing or deleting any past event breaks every later link.
    """

    _name = "sign.methode.event"
    _description = "Signature Audit Event"
    _order = "request_id, sequence"
    _log_access = False

    request_id = fields.Many2one(
        "sign.methode.request", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(required=True)
    type = fields.Selection(EVENT_TYPES, required=True)
    date = fields.Datetime(required=True)
    signer_id = fields.Many2one("sign.methode.signer", ondelete="set null")
    actor = fields.Char(help="Who acted: signer e-mail, user login or 'system'.")
    ip = fields.Char("IP Address")
    user_agent = fields.Char()
    details = fields.Text(help="JSON payload")
    previous_hash = fields.Char(required=True)
    hash = fields.Char(required=True, index=True)

    _request_sequence_uniq = models.Constraint(
        "UNIQUE(request_id, sequence)", "Audit events are numbered once per request."
    )

    def write(self, vals):
        raise UserError(self.env._("Audit events cannot be modified."))

    def unlink(self):
        if not self.env.context.get("sign_methode_purge"):
            raise UserError(self.env._("Audit events cannot be deleted."))
        return super().unlink()

    def _payload(self):
        self.ensure_one()
        return {
            "request": self.request_id.id,
            "sequence": self.sequence,
            "type": self.type,
            "date": fields.Datetime.to_string(self.date),
            "signer": self.signer_id.id or None,
            "actor": self.actor or "",
            "ip": self.ip or "",
            "user_agent": self.user_agent or "",
            "details": self.details or "",
        }

    @staticmethod
    def _digest(previous_hash, payload):
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((previous_hash + data).encode()).hexdigest()

    def _log(self, req, event_type, signer=None, details=None, actor=None):
        """Append an event to ``req``'s chain (locks the request row)."""
        self.env.cr.execute(
            "SELECT id FROM sign_methode_request WHERE id = %s FOR UPDATE", [req.id]
        )
        self.env.cr.execute(
            "SELECT sequence, hash FROM sign_methode_event WHERE request_id = %s "
            "ORDER BY sequence DESC LIMIT 1",
            [req.id],
        )
        last = self.env.cr.fetchone()
        sequence, previous = (last[0] + 1, last[1]) if last else (1, GENESIS)
        ip = user_agent = ""
        if http_request:
            ip = http_request.httprequest.remote_addr or ""
            user_agent = (http_request.httprequest.user_agent.string or "")[:300]
        if actor is None:
            actor = (signer.email or signer.name) if signer else self.env.user.login
        vals = {
            "request_id": req.id,
            "sequence": sequence,
            "type": event_type,
            "date": fields.Datetime.now(),
            "signer_id": signer.id if signer else False,
            "actor": actor,
            "ip": ip,
            "user_agent": user_agent,
            "details": json.dumps(details, sort_keys=True, default=str)
            if details
            else "",
            "previous_hash": previous,
        }
        event = self.sudo().new(vals)
        vals["hash"] = self._digest(previous, event._payload())
        return self.sudo().create(vals)

    def _check_chain(self):
        """True when ``self`` (one request's events) is an unbroken chain."""
        previous = GENESIS
        for index, event in enumerate(self.sorted("sequence"), start=1):
            if event.sequence != index or event.previous_hash != previous:
                return False
            if self._digest(previous, event._payload()) != event.hash:
                return False
            previous = event.hash
        return True
