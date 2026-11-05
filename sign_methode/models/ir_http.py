# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    @api.model
    def _get_translation_frontend_modules_name(self):
        # The public signing page is translated like the rest of the frontend.
        return super()._get_translation_frontend_modules_name() + ["sign_methode"]
