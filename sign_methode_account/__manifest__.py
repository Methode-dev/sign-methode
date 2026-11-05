# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Méthode Sign - Invoices",
    "summary": "Send customer invoices for electronic signature",
    "version": "19.0.1.0.0",
    "category": "Accounting/Accounting",
    "author": "Méthode",
    "website": "https://github.com/Methode-dev/sign-methode",
    "license": "LGPL-3",
    "depends": ["sign_methode", "account"],
    "data": [
        "security/sign_security.xml",
        "views/account_move_views.xml",
    ],
    "auto_install": True,
    "installable": True,
}
