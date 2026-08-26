#!/usr/bin/env python3
"""Nettoie et importe les anciens formulaires Bectanse sans exposer les données."""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from marketing_automation import ensure_marketing_schema, sync_marketing_segments


SUSPICIOUS_DOMAINS = {
    "gmai.com", "gamil.com", "glail.com", "gmail.con", "gmail.fr",
    "gmail.co", "gmail.col", "gmail.comr", "gmail.clm", "gmail.vom",
    "gmail.xom", "gmail,.com", "gmail.pagot", "gnail.com", "gmaik.com",
    "gmal.com", "gmil.com", "icloud.con", "icoud.com", "ocloud.com",
    "outlook.fe",
}


def normalized(value):
    value = str(value or "").strip().lower()
    return "".join(
        char for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )


def find_column(headers, choices):
    for header in headers:
        clean = normalized(header)
        if any(normalized(choice) in clean for choice in choices):
            return header
    return None


def parse_date(value):
    value = str(value or "").strip()
    for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def source_name(path, row, source_column):
    filename = normalized(os.path.basename(path))
    if "money club" in filename:
        return "money-club-2025"
    if source_column and str(row.get(source_column) or "").strip():
        return normalized(row.get(source_column)).replace(" ", "-")[:80]
    return "essai-gratuit-2026"


def read_records(paths):
    records = {}
    stats = {"raw_rows": 0, "empty_email": 0, "invalid_syntax": 0,
             "test_rows": 0, "suspicious_domain": 0, "duplicates": 0}
    email_pattern = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
    for path in paths:
        with open(path, "r", encoding="utf-8-sig", newline="") as source:
            sample = source.read(10000)
            source.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(source, dialect=dialect)
            headers = reader.fieldnames or []
            email_column = find_column(headers, ["e-mail", "email"])
            name_column = find_column(headers, ["prénom", "prenom", "appelles-tu"])
            date_column = find_column(headers, ["horodateur", "date"])
            phone_column = find_column(headers, ["whatsapp", "téléphone", "telephone"])
            country_column = find_column(headers, ["pays", "country"])
            source_column = find_column(headers, ["source"])
            if not email_column:
                raise RuntimeError(f"Colonne e-mail introuvable dans {os.path.basename(path)}")
            for row in reader:
                stats["raw_rows"] += 1
                email = re.sub(r"\s+", "", str(row.get(email_column) or "").strip().lower())
                if not email:
                    stats["empty_email"] += 1
                    continue
                if not email_pattern.fullmatch(email):
                    stats["invalid_syntax"] += 1
                    continue
                name = str(row.get(name_column) or "").strip() if name_column else ""
                if ("test" in normalized(name) or
                        re.search(r"(^|[._+-])test([._+@-]|$)", email) or
                        "bectanseacademie" in email):
                    stats["test_rows"] += 1
                    continue
                domain = email.rsplit("@", 1)[1]
                if domain in SUSPICIOUS_DOMAINS:
                    stats["suspicious_domain"] += 1
                    continue
                record = {
                    "email": email,
                    "first_name": name.split()[0].title()[:80] if name else "",
                    "phone": str(row.get(phone_column) or "").strip()[:40] if phone_column else "",
                    "country": str(row.get(country_column) or "").strip()[:80] if country_column else "",
                    "collected_at": parse_date(row.get(date_column)) if date_column else None,
                    "source": source_name(path, row, source_column),
                }
                previous = records.get(email)
                if previous:
                    stats["duplicates"] += 1
                    previous_date = previous.get("collected_at") or datetime.min
                    current_date = record.get("collected_at") or datetime.min
                    if current_date <= previous_date:
                        continue
                records[email] = record
    return records, stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="Exports CSV des anciens formulaires")
    parser.add_argument("--apply", action="store_true", help="Appliquer réellement l'import")
    args = parser.parse_args()
    records, stats = read_records(args.files)
    conn = app.get_conn()
    try:
        ensure_marketing_schema(conn)
        sync_marketing_segments(conn)
        known = {str(row[0]).strip().lower() for row in conn.run(
            "SELECT email FROM marketing_contacts WHERE TRIM(COALESCE(email,''))<>''"
        )}
        legacy = {str(row[0]).strip().lower() for row in conn.run(
            "SELECT email FROM marketing_legacy_leads"
        )}
        eligible = [record for email, record in records.items() if email not in known]
        summary = {
            **stats,
            "clean_unique": len(records),
            "already_known_members_or_explorers": sum(1 for email in records if email in known),
            "already_in_legacy_base": sum(1 for email in records if email in legacy),
            "eligible": len(eligible),
            "applied": bool(args.apply),
            "inserted": 0,
            "updated": 0,
        }
        if args.apply:
            conn.run("""UPDATE marketing_settings SET legacy_campaign_enabled=FALSE,
                legacy_paused_reason='Import contrôlé en attente de lancement',updated_at=NOW()
                WHERE id=1""")
            for record in eligible:
                existed = record["email"] in legacy
                conn.run("""INSERT INTO marketing_legacy_leads
                    (email,first_name,source,status,phone,country,collected_at,consent_basis)
                    VALUES (:email,:first_name,:source,'active',:phone,:country,:collected_at,
                            'demande-formulaire-proprietaire')
                    ON CONFLICT (LOWER(email)) DO UPDATE SET
                        first_name=CASE WHEN marketing_legacy_leads.first_name=''
                            THEN EXCLUDED.first_name ELSE marketing_legacy_leads.first_name END,
                        phone=CASE WHEN marketing_legacy_leads.phone=''
                            THEN EXCLUDED.phone ELSE marketing_legacy_leads.phone END,
                        country=CASE WHEN marketing_legacy_leads.country=''
                            THEN EXCLUDED.country ELSE marketing_legacy_leads.country END,
                        collected_at=COALESCE(marketing_legacy_leads.collected_at,EXCLUDED.collected_at)""",
                    **record)
                summary["updated" if existed else "inserted"] += 1
        print(json.dumps(summary, ensure_ascii=False, default=str))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
