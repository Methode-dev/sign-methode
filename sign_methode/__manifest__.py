# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Méthode Sign",
    "summary": "Simple PDF e-signature: OTP, guided signing, PAdES seal, "
    "RFC 3161 timestamps and a hash-chained audit trail",
    "version": "19.0.1.0.2",
    "category": "Productivity/Sign",
    "author": "Méthode",
    "website": "https://github.com/Methode-dev/sign-methode",
    "license": "LGPL-3",
    "depends": ["mail", "portal", "web"],
    "external_dependencies": {"python": ["pyhanko", "reportlab", "pypdf"]},
    "data": [
        "security/sign_security.xml",
        "security/ir.model.access.csv",
        "data/sign_data.xml",
        "data/mail_templates.xml",
        "data/ir_cron.xml",
        "views/sign_request_views.xml",
        "views/sign_template_views.xml",
        "views/sign_role_views.xml",
        "views/res_config_settings_views.xml",
        "wizards/sign_send_views.xml",
        "views/sign_menus.xml",
        "templates/sign_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "sign_methode/static/src/scss/tokens.scss",
            "sign_methode/static/src/common/**/*",
            "sign_methode/static/src/backend/**/*",
        ],
        "web.assets_frontend": [
            "sign_methode/static/src/scss/tokens.scss",
            "sign_methode/static/src/common/**/*",
            "sign_methode/static/src/signer/**/*",
        ],
        "web.assets_tests": [
            "sign_methode/static/tests/tours/**/*",
        ],
    },
    "application": True,
    "installable": True,
}
