"""Bounded, parameterized queries for the administration member directory."""
from datetime import datetime, timedelta

CAPITAL_NUMBER = "REPLACE(REGEXP_REPLACE(COALESCE(capital,''), '[   ]', '', 'g'), ',', '.')"

SORTS = {
    "recent": "created_at DESC NULLS LAST, code ASC",
    "name": "LOWER(COALESCE(nom,'')) ASC, code ASC",
    "expiry": "date_fin ASC NULLS LAST, code ASC",
    "capital": f"CASE WHEN {CAPITAL_NUMBER} ~ '^[0-9]+([.][0-9]+)?$' THEN CAST({CAPITAL_NUMBER} AS NUMERIC) END DESC NULLS LAST, code ASC",
}


def directory_page(conn, args):
    def bounded(value, default, low, high):
        try:
            return max(low, min(high, int(value)))
        except (ValueError, TypeError):
            return default

    page = bounded(args.get("page"), 1, 1, 1000000)
    size = bounded(args.get("page_size"), 15, 1, 100)
    now = datetime.now()
    params = {"now": now}
    clauses = ["code <> 'BCT-DEMO2026'"]
    level = "COALESCE(access_level,'member')"
    eligible = "actif=TRUE AND (date_fin IS NULL OR date_fin > :now)"
    scope = args.get("scope", "all")
    if scope in ("total", "actifs", "copy", "expires", "nouveaux"):
        clauses.append(f"{level} <> 'explorer'")
    if scope == "explorers":
        clauses.append(f"{level} = 'explorer'")
    elif scope in ("actifs", "copy"):
        clauses.append(eligible)
        if scope == "copy":
            clauses.append("copy_actif=TRUE")
    elif scope == "expires":
        clauses.append("date_fin <= :now")
    elif scope == "nouveaux":
        clauses.append("created_at > :new_since")
        params["new_since"] = now - timedelta(days=7)
    query = str(args.get("q", "")).strip()[:200]
    if query:
        params["query"] = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(nom ILIKE :query OR code ILIKE :query OR email ILIKE :query)")
    status = args.get("status")
    if status == "active":
        clauses.extend([f"{level} <> 'explorer'", eligible])
    elif status == "suspended":
        clauses.extend([f"{level} <> 'explorer'", "actif=FALSE", "(date_fin IS NULL OR date_fin > :now)"])
    elif status == "expired":
        clauses.extend([f"{level} <> 'explorer'", "date_fin <= :now"])
    access = args.get("access")
    if access == "explorer":
        clauses.append(f"{level} = 'explorer'")
    elif access == "member":
        clauses.append(f"{level} <> 'explorer'")
    if args.get("expiry") == "7d":
        clauses.extend([f"{level} <> 'explorer'", "date_fin > :now AND date_fin <= :until"])
        params["until"] = now + timedelta(days=7)
    where = " AND ".join(clauses)
    params = {key: value for key, value in params.items() if ":" + key in where}
    total = int(conn.run("SELECT COUNT(*) FROM members WHERE " + where, **params)[0][0])
    pages = max(1, (total + size - 1) // size)
    page = min(page, pages)
    order = SORTS.get(args.get("sort"), SORTS["recent"])
    rows = conn.run(
        "SELECT code,nom,capital,actif,copy_actif,date_fin,email,created_at," + level +
        " FROM members WHERE " + where + " ORDER BY " + order + " LIMIT :limit OFFSET :offset",
        **params, limit=size, offset=(page - 1) * size,
    )
    members = []
    for row in rows:
        m = dict(zip(("code", "nom", "capital", "actif", "copy_actif", "date_fin", "email", "created_at", "access_level"), row))
        expiry = m["date_fin"]
        m["est_expire"] = bool(expiry and expiry <= now)
        m["nouveau_7j"] = bool(m["created_at"] and m["created_at"] > now - timedelta(days=7))
        m["jours_restants"] = max(0, (expiry - now).days) if expiry else None
        m["date_fin"] = expiry.strftime("%d/%m/%Y") if expiry else None
        m.pop("created_at")
        members.append(m)
    return {"ok": True, "membres": members, "page": page, "page_size": size, "pages": pages, "total": total}
