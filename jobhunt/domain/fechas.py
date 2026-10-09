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


# Precisión de una fecha de publicación (columna date_precision):
#   exact  → la página/API dio una fecha calendario (ISO, DD-MM-YYYY, '21 de Jul, 2026')
#   dia    → relativa al día ('hace 3 días', 'hoy', 'ayer', 'hace 5 horas'): ±0 días
#   aprox  → relativa gruesa ('hace 2 semanas', 'hace 1 mes', 'más de 30 días'): ±varios días
_NUM_PALABRA = {"un": 1, "una": 1, "uno": 1, "unos": 1, "unas": 1, "dos": 2, "tres": 3, "cuatro": 4,
                "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
                "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_UNIDADES = [  # (regex de unidad, clave) — es/en, singular/plural
    (r"segundos?|seconds?", "s"), (r"minutos?|minutes?|mins?", "min"),
    (r"horas?|hours?|hrs?", "h"), (r"d[ií]as?|days?", "d"),
    (r"semanas?|weeks?", "sem"), (r"meses|mes|months?", "mes"), (r"a[ñn]os?|years?", "anio"),
]


def _restar_meses(d: date, n: int) -> date:
    m = d.month - 1 - n
    y, mo = d.year + m // 12, m % 12 + 1
    dias_mes = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28, 31, 30, 31, 30,
                31, 31, 30, 31, 30, 31][mo - 1]
    return date(y, mo, min(d.day, dias_mes))


def _relativa(s: str, now: datetime) -> tuple[str, str]:
    """'hace 3 días' / 'hace una semana' / '2 weeks ago' / 'hoy' / 'ayer' / 'más de 30 días'
    → (YYYY-MM-DD, precision) respecto de `now` (momento de captura). ('','') si no aplica."""
    low = s.lower().strip()
    hoy = now.date()
    if re.search(r"\b(hoy|today|reci[eé]n|just now|ahora)\b", low):
        return hoy.isoformat(), "dia"
    if re.search(r"\b(ayer|yesterday)\b", low):
        return (hoy - timedelta(days=1)).isoformat(), "dia"
    unidades = "|".join(f"(?:{u})" for u, _ in _UNIDADES)
    m = re.search(rf"(?:hace\s+|publicad[oa]\s+hace\s+)?(?:(m[aá]s de|\+|over|more than)\s*)?"
                  rf"(\d+|{'|'.join(_NUM_PALABRA)})\s*({unidades})\b(?:\s+ago)?", low)
    if not m or not re.search(r"\bhace\b|\bago\b|\bpublicad", low):
        return "", ""
    n = int(m.group(2)) if m.group(2).isdigit() else _NUM_PALABRA.get(m.group(2), 1)
    unit = next(k for u, k in _UNIDADES if re.fullmatch(u, m.group(3)))
    if unit in ("s", "min", "h"):
        # horas pueden cruzar medianoche: se resuelve con el reloj real
        delta = {"s": timedelta(seconds=n), "min": timedelta(minutes=n), "h": timedelta(hours=n)}[unit]
        return (now - delta).date().isoformat(), "dia"
    if unit == "d":
        return (hoy - timedelta(days=n)).isoformat(), "aprox" if m.group(1) else "dia"
    if unit == "sem":
        return (hoy - timedelta(days=7 * n)).isoformat(), "aprox"
    if unit == "mes":
        return _restar_meses(hoy, n).isoformat(), "aprox"
    return _restar_meses(hoy, 12 * n).isoformat(), "aprox"


def resolver_fecha(raw: str | int | float | None, now: datetime | None = None) -> tuple[str, str]:
    """Fecha de publicación EXACTA (YYYY-MM-DD) + precisión a partir de lo que dice la fuente.

    Absoluta ('2026-10-06T…', '06-10-2026', 'Publicado el 21 de Jul, 2026') → exact.
    Relativa ('hace 3 días', 'hace una semana', '2 weeks ago', 'hoy', 'ayer') → se resuelve
    contra `now` = momento de captura, así la fecha guardada no se mueve y la antigüedad que
    se muestra (hoy − fecha) siempre está al día. ('', '') si no se entiende."""
    if raw is None:
        return "", ""
    now = now or datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        n = int(raw)
        if 0 <= n < 400:            # publication_days (días desde publicación)
            return (now - timedelta(days=n)).date().isoformat(), "dia"
        if n > 10**12:              # epoch ms
            return datetime.fromtimestamp(n / 1000, timezone.utc).date().isoformat(), "exact"
        if n > 10**9:               # epoch s
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat(), "exact"
        return "", ""
    s = str(raw).strip()
    if not s:
        return "", ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)            # ISO / YYYY-MM-DD
    if m:
        return s[:10], "exact"
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", s)         # DD-MM-YYYY (Laborum)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat(), "exact"
        except ValueError:
            return "", ""
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñ]+),?\s+(\d{4})", s, re.I)   # Jooble
    if m:
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1))).isoformat(), "exact"
            except ValueError:
                return "", ""
    rel = _relativa(s, now)
    if rel[0]:
        return rel
    if s.isdigit():
        return resolver_fecha(int(s), now)
    return "", ""


def normalize_date(raw: str | int | float | None, now: datetime | None = None) -> str:
    """Compat: solo la fecha ISO de resolver_fecha ('' si no parseable)."""
    return resolver_fecha(raw, now)[0]


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
