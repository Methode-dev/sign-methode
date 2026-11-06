# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class SignMethodeRole(models.Model):
    """Who signs: "Customer", "Company", "Broker"...

    Template fields belong to a role; when a request is sent from a record,
    ``partner_expression`` (``{{ object.partner_id.id }}``) picks the partner.
    When it is a company, ``representative_expression`` picks the person who
    signs for it.
    """

    _name = "sign.methode.role"
    _description = "Signature Role"
    _order = "sequence, id"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    color = fields.Integer(default=1)
    partner_expression = fields.Char(
        help="Inline template evaluated on the record a request is sent from, "
        "e.g. {{ object.partner_id.id }}. Leave empty to choose by hand."
    )
    representative_expression = fields.Char(
        help="For a company: inline template picking its legal representative, "
        "e.g. {{ object.env.user.partner_id.id }}. Leave empty to choose by hand."
    )
    active = fields.Boolean(default=True)

    def _get_partner(self, record):
        self.ensure_one()
        return self._render_partner(self.partner_expression, record)

    def _get_representative(self, record):
        self.ensure_one()
        if not self._get_partner(record).is_company:
            return self.env["res.partner"]
        return self._render_partner(self.representative_expression, record)

    def _render_partner(self, expression, record):
        if not expression or not record:
            return self.env["res.partner"]
        rendered = self.env["mail.render.mixin"]._render_template(
            expression, record._name, record.ids, engine="inline_template"
        )[record.id]
        try:
            partner_id = int(str(rendered).strip() or 0)
        except ValueError:
            return self.env["res.partner"]
        return self.env["res.partner"].browse(partner_id).exists()
