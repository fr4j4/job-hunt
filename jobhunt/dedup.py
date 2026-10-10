"""Dedup cross-plataforma: 3 capas sobre la BD.

1. URL normalizada preservando IDs (jk/jl)
2. Fingerprint de título normalizado + empresa fuerte
3. Fuzzy (Jaccard ≥ .55 o secuencia ≥ .86) + empresa compatible
"""
from __future__ import annotations

import sqlite3

from .db import url_key, norm_title, norm_company, companies_match, similar, _GENERIC


def find_duplicate(conn, job: dict) -> str | None:
    """Retorna group_id del duplicado, o None si es nueva."""
    conn.row_factory = sqlite3.Row
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
    # capa 3 (fuzzy): una empresa desconocida actúa de comodín, y títulos genéricos ("Data
    # Scientist" ≈ "Data Scientist - GCP") fusionaban ofertas de empresas distintas. El comodín
    # solo vale si AMBAS empresas son desconocidas (fuentes sin dato); con una empresa real
    # de un lado el fuzzy exige empresa fuerte — el título exacto (capa 2) sigue valiendo.
    job_generica = norm_company(job.get("company", "")) in _GENERIC
    for r in rows:
        rel = companies_match(job.get("company", ""), r["company"])
        if rel == "different":
            continue
        if rel == "weak" and not (job_generica and norm_company(r["company"]) in _GENERIC):
            continue
        if similar(job["title"], r["title"]):
            return r["group_id"]
    return None