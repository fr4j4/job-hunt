"""Fechas: parseo, canonicalización y antigüedad de ofertas (código puro).

Movido desde jobhunt/channel.py (refactor de estructura, sin cambio de
comportamiento). jobhunt/channel.py re-exporta estos nombres por compat.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
          "diciembre": 12, "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
          "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def normalize_date(raw: str | int | float | None, now: datetime | None = None) -> str:
    """Convierte los formatos de fecha de las 8 fuentes a YYYY-MM-DD ('' si no parseable).

    LinkedIn: ISO datetime · Laborum: DD-MM-YYYY · Jooble: 'Publicado el 21 de Jul, 2026'
    Computrabajo: 'Hace X horas/días' (relativo) · AIRA: publication_days (int) · epoch.
    """
    if raw is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        # publication_days (días desde publicación) o epoch
        n = int(raw)
        if 0 <= n < 400:            # publication_days
            return (now - timedelta(days=n)).date().isoformat()
        if n > 10**12:              # epoch ms
            return datetime.fromtimestamp(n / 1000, timezone.utc).date().isoformat()
        if n > 10**9:               # epoch s
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # ISO completo o YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10]
    # DD-MM-YYYY (Laborum)
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    # 'Publicado el 21 de Jul, 2026' (Jooble)
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñ]+),?\s+(\d{4})", s, re.I)
    if m:
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1))).isoformat()
            except ValueError:
                return ""
    # 'Hace X horas/días/semanas/meses' / 'Hoy' / 'Ayer' (Computrabajo)
    m = re.search(r"[Hh]ace\s+([\d\s]+)\s*(minuto|hora|día|dia|semana|mes)", s)
    if m:
        n = int(re.sub(r"\D", "", m.group(1)) or 1)
        unit = m.group(2).lower()
        delta = {"minuto": 0, "hora": 0, "día": n, "dia": n, "semana": n * 7, "mes": n * 30}[unit]
        return (now - timedelta(days=delta)).date().isoformat()
    if re.search(r"\bhoy\b", s, re.I):
        return now.date().isoformat()
    if re.search(r"\bayer\b", s, re.I):
        return (now - timedelta(days=1)).date().isoformat()
    # epoch string
    if s.isdigit():
        return normalize_date(int(s), now)
    return ""


def canonical_date(row: dict, now: datetime | None = None) -> str:
    """Fecha canónica de la oferta: min(date_posted, first_seen) con clamp.

    - sin date_posted → first_seen (cota honesta: Indeed filtro 168h)
    - date_posted más fresca que first_seen → clamp a first_seen (anti repost-fresh)
    """
    now = now or datetime.now(timezone.utc)
    d = normalize_date(row.get("date_posted") or "", now)
    fs = str(row.get("first_seen") or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}", fs):
        return d
    if not d:
        return fs
    return d if d <= fs else fs


def age_days(row: dict, now: datetime | None = None) -> int:
    """Días de antigüedad según date_canonical. Negativa → 0."""
    now = now or datetime.now(timezone.utc)
    c = canonical_date(row, now)
    if not re.match(r"\d{4}-\d{2}-\d{2}", c):
        return 0
    try:
        dd = (datetime.now(timezone.utc).date() if now is None
              else now.date()) - date.fromisoformat(c)
        return max(0, dd.days)
    except ValueError:
        return 0
