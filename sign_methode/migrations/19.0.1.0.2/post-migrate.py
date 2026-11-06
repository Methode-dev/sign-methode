# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Companies sign through their legal representative.

* The "Company" role now signs as the company, through the user who sends the
  request. The role is noupdate data: update it unless someone already changed
  it by hand.
* The invitation e-mail names the company. An upgrade keeps translations
  already in the database, so reload this module's: the e-mails would stay in
  their old wording in every language but English.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    cr.execute(
        """
        UPDATE sign_methode_role role
           SET partner_expression = '{{ object.env.company.partner_id.id }}',
               representative_expression = '{{ object.env.user.partner_id.id }}'
          FROM ir_model_data imd
         WHERE imd.module = 'sign_methode'
           AND imd.name = 'role_company'
           AND imd.model = 'sign.methode.role'
           AND imd.res_id = role.id
           AND role.partner_expression = '{{ object.env.user.partner_id.id }}'
        """
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    langs = [code for code, _name in env["res.lang"].get_installed() if code != "en_US"]
    if langs:
        env["ir.module.module"]._load_module_terms(["sign_methode"], langs, overwrite=True)
