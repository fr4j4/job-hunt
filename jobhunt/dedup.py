"""Dedup cross-plataforma: 3 capas sobre la BD.

1. URL normalizada preservando IDs (jk/jl)
2. Fingerprint de título normalizado + empresa fuerte
3. Fuzzy (Jaccard ≥ .55 o secuencia ≥ .86) + empresa compatible
"""
from __future__ import annotations

from .db import url_key, norm_title, norm_company, companies_match, similar


def find_duplicate(conn, job: dict) -> str | None:
    """Retorna group_id del duplicado, o None si es nueva."""
    # capa 1: URL con ID
    uk = url_key(job.get("url"))
    if uk:
        row = conn.execute("SELECT group_id FROM ofertas WHERE url LIKE ? LIMIT 1",
                           (f"%{uk}%",)).fetchone()
        if row:
            return row["group_id"]

    n_title = norm_title(job.get("title", ""))
    if not n_title:
        return None

    rows = conn.execute("SELECT group_id, title, company FROM ofertas WHERE active=1").fetchall()
    for r in rows:
        if norm_title(r["title"]) == n_title:
            rel = companies_match(job.get("company", ""), r["company"])
            if rel == "strong":
                return r["group_id"]
            if rel == "weak":
                return r["group_id"]  # genérica: aceptar
    for r in rows:
        if companies_match(job.get("company", ""), r["company"]) == "different":
            continue
        if similar(job["title"], r["title"]):
            return r["group_id"]
    return None