"""Categorías de rol y gate dev/no-dev (código puro).

Movido desde jobhunt/channel.py (refactor de estructura, sin cambio de
comportamiento). jobhunt/channel.py re-exporta estos nombres por compat.
"""
from __future__ import annotations

import re

from ..config import Config

_DEV_CATEGORIES = {"Full Stack", "Backend", "Frontend", "Data", "Mobile", "AI/ML",
                   "Tech Lead", "DevOps/Cloud", "QA", "Software", "Seguridad"}

_CAT_RULES = (   # CH-1: word boundaries — 'Retail'≠AI, 'Full Time'≠Full Stack, 'Data Entry'≠Data
    ("Backend", r"\bback.?end\b"),
    ("Frontend", r"\bfront.?end\b|\bfront\b"),
    ("Full Stack", r"\bfull.?stack\b|\bstack\b"),
    ("Mobile", r"\bm[oó]vil\b|\bmobile\b"),
    ("Data", r"\bdata\b(?!\s*entry)|\bdatos\b"),
    ("AI/ML", r"\bai\b|\bml\b|\bia\b|\bmachine learning\b"),
    ("Tech Lead", r"\btech\s*lead\b|\blead\b(?!\s+(?:de\s+)?(?:ventas|comercial|retail|tienda))"),
    ("DevOps/Cloud", r"\bdev.?ops\b|\bcloud\b|\binfra\w*\b|\bsre\b"),
    ("QA", r"\bqa\b|\btesting\b"),
    ("Software", r"\bsoftware\b|\bdesarroll\w*\b"),
    ("Seguridad", r"\bseguridad\b|\bsecops\b|\bsecurity\b"),
)


def _categorias_dev(rc: str) -> set[str]:
    """Normaliza rol_categoria libre → categorías dev canónicas:
    'Backend Developer' → {Backend} · 'Fullstack Developer' → {Full Stack, Software}
    'DevOps' → {DevOps/Cloud} · 'Desarrollo Móvil' → {Mobile, Software}.
    Match por palabra completa (CH-1): 'Retail', 'Full Time', 'Data Entry',
    'Team Lead Ventas' → set() (antes: AI/ML, Full Stack, Data, Tech Lead)."""
    r = rc.lower()
    return {cat for cat, pat in _CAT_RULES if re.search(pat, r)}


_NONDEV_CATEGORIES = {"Ingeniería no-software", "Analista/Empresa", "Profesor/Formación",
                      "Soporte/TI", "No-tech", "Otro"}


def is_dev(rol_categoria: str | None, title: str, cfg: Config, description: str = "") -> bool:
    """Gate dev: rol_categoria IA primero; regex SOLO en modo degradado (IA apagada).

    spec-techs-dev-gate §2.3: con IA activa (cfg.ia.enabled=true), rol_categoria es
    la ÚNICA fuente — sin rol → no dev (espera a que la IA procese). Con IA apagada,
    la regex corregida (word boundaries + lookahead) se ejecuta sobre título+descripción.
    La IA es la autoridad: categoría no-dev explícita → False siempre."""
    rc = (rol_categoria or "").strip()
    if rc:
        if rc in _NONDEV_CATEGORIES:
            return False
        if rc in _DEV_CATEGORIES:
            return True
        if _categorias_dev(rc) & _DEV_CATEGORIES:
            return True
    if cfg.ia.enabled:
        # IA activa: sin rol_categoria → no dev (la regex NO se ejecuta — §2.3)
        return False
    # modo degradado (IA apagada): regex corregida sobre título + descripción
    t = (title or "").lower()
    d = (description or "").lower()
    if re.search(cfg.relevance.nontech_titles, t, re.I):
        return False
    return bool(re.search(
        r"\bdev(?:eloper|ops)?\b|\bdesarroll\w*\b(?=\s+(?:de\s+)?(?:software|aplicaciones|web|backend|frontend|api|sistemas|app))|"
        r"\bsoftware\b|\bbackend\b|\bfrontend\b|\bfull.?stack\b|\bdata\b|\bpython\b|\bjava\b|\bqa\b|\bdevops\b|\bsistemas\b|"
        r"\binformátic\w*\b|\binformatic\w*\b", t + " " + d, re.I))
