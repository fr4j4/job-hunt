"""Normalización de texto (código puro, sin deps internas).

Movido desde jobhunt/scoring.py (refactor de estructura, sin cambio de
comportamiento). jobhunt/scoring.py y jobhunt/db.py re-exportan por compat
(db.py lo tenía duplicado como _norm_text — misma lógica, unificada aquí).
"""
from __future__ import annotations

import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()
