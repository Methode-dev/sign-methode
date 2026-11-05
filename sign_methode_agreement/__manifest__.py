# Copyright 2026 Méthode
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
{
    "name": "Méthode Sign - Agreements",
    "summary": "Send agreements (contracts) for electronic signature",
    "version": "19.0.1.0.0",
    "category": "Contract",
    "author": "Méthode",
    "website": "https://github.com/Methode-dev/sign-methode",
    # AGPL because OCA's agreement is: never a dependency of a daf_* module.
    "license": "AGPL-3",
    "depends": ["sign_methode", "agreement"],
    "data": ["views/agreement_views.xml"],
    "auto_install": True,
    "installable": True,
}
