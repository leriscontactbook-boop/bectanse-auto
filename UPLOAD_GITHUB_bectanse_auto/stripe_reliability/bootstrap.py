"""Existing member base schema, shared by first install and application startup."""

MEMBERS_DDL = """
CREATE TABLE IF NOT EXISTS members (
    code              TEXT PRIMARY KEY,
    nom               TEXT NOT NULL,
    capital           TEXT NOT NULL,
    actif             BOOLEAN DEFAULT TRUE,
    created_at        TIMESTAMP DEFAULT NOW(),
    last_login        TIMESTAMP,
    params            TEXT DEFAULT '{}',
    copy_actif        BOOLEAN DEFAULT TRUE,
    date_souscription TIMESTAMP DEFAULT NOW(),
    date_fin          TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days'),
    email             TEXT DEFAULT '',
    telephone         TEXT DEFAULT '',
    telegram          TEXT DEFAULT '',
    alerte_lue        BOOLEAN DEFAULT TRUE,
    parrain_code      TEXT DEFAULT '',
    filleuls_count    INTEGER DEFAULT 0,
    gains_parrainage  INTEGER DEFAULT 0,
    paiement_iban     TEXT DEFAULT '',
    paiement_bic      TEXT DEFAULT '',
    paiement_titulaire TEXT DEFAULT '',
    paiement_crypto_reseau TEXT DEFAULT '',
    paiement_crypto_adresse TEXT DEFAULT '',
    paiement_type     TEXT DEFAULT '',
    historique        TEXT DEFAULT '[]',
    access_level      TEXT NOT NULL DEFAULT 'member',
    email_verified_at TIMESTAMP DEFAULT NOW()
)
"""


def ensure_member_base(conn):
    conn.run(MEMBERS_DDL)
