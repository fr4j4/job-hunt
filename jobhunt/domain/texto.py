"""Normalización de texto (código puro, sin deps internas).

Movido desde jobhunt/scoring.py (refactor de estructura, sin cambio de
comportamiento). jobhunt/scoring.py y jobhunt/db.py re-exportan por compat
(db.py lo tenía duplicado como _norm_text — misma lógica, unificada aquí).
"""
from __future__ import annotations

import unicodedata

# Tope de almacenamiento de la descripción completa de una oferta (la IA recibe
# un recorte aparte en prompts.py). Antes: 1800/2000 → fichas largas truncadas.
MAX_DESC = 20000


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()
