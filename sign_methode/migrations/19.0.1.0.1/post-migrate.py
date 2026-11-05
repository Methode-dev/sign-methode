# Copyright 2026 Méthode
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Links moved from one column per signer to one row per link sent: keep
the links already in signers' inboxes working."""


def migrate(cr, version):
    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'sign_methode_signer' "
        "AND column_name IN ('token_hash', 'download_token_hash')"
    )
    columns = {row[0] for row in cr.fetchall()}
    for column, kind in (("token_hash", "sign"), ("download_token_hash", "download")):
        if column not in columns:
            continue
        cr.execute(
            f"""
            INSERT INTO sign_methode_signer_token
                (signer_id, token_hash, kind, create_uid, create_date,
                 write_uid, write_date)
            SELECT id, {column}, %s, 1, now() at time zone 'UTC', 1,
                   now() at time zone 'UTC'
              FROM sign_methode_signer
             WHERE {column} IS NOT NULL
            ON CONFLICT (token_hash) DO NOTHING
            """,
            [kind],
        )
        cr.execute(f"ALTER TABLE sign_methode_signer DROP COLUMN {column}")
    cr.execute(
        "UPDATE sign_methode_signer SET session_salt = md5(random()::text) "
        "WHERE session_salt IS NULL"
    )
