"""Verificación y extracción de sueldos DESDE EL TEXTO de la oferta (hoja: puro).

Motivo: un LLM local pequeño rellena `salario_clp_mensual` con un valor
plausible aunque la oferta no diga nada. Un sueldo solo es real si el monto
aparece en el texto. Este módulo:
  - extrae los montos explícitos del texto (CLP/USD, "$2.500.000", "2,5 millones",
    "1.8M", "800 mil", rangos "$1.800.000 - $2.200.000"),
  - decide si un valor propuesto por la IA está respaldado por esos montos.
"""
from __future__ import annotations

import re

from .stats import FLOOR, CEILING, USD_CLP

_NUM = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
# $ 2.500.000 · CLP 2500000 · 2.500.000 pesos · USD 3,000 · US$3000
_RE_MONTO = re.compile(
    rf"(?P<pre>US\$|USD|CLP|\$)\s*(?P<n>{_NUM})(?P<suf>\s*(?:millones?|mill\.?|mm|m\b|mil\b|k\b))?"
    rf"|(?P<n2>{_NUM})\s*(?P<suf2>millones?|mill\.?|mil\b|k\b|pesos|clp|usd|dólares|dolares)"
    rf"|(?P<n3>\d+[.,]\d)\s*(?P<suf3>M)\b",
    re.I)
_RE_ANUAL = re.compile(r"anual|al a[ñn]o|/\s*a[ñn]o|per year|yearly|/year", re.I)


def _num(s: str) -> float | None:
    """'2.500.000' → 2500000 · '2,5' → 2.5 · '3,000' → 3000 · '1.8' → 1.8."""
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):          # miles
        return float(re.sub(r"[.,]", "", s))
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+[.,]\d{1,2}", s):  # 2.400.000,00
        ent, dec = s[:-3], s[-2:]
        return float(re.sub(r"[.,]", "", ent) + "." + dec)
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def extraer_montos(texto: str) -> list[int]:
    """Montos CLP/mes explícitos en el texto (USD convertido; anuales ÷12 si el
    contexto cercano dice anual). Sin filtrar por plausibilidad salvo > 0."""
    out: list[int] = []
    for m in _RE_MONTO.finditer(texto or ""):
        raw = m.group("n") or m.group("n2") or m.group("n3")
        suf = (m.group("suf") or m.group("suf2") or m.group("suf3") or "").strip().lower()
        pre = (m.group("pre") or "").lower()
        n = _num(raw)
        if n is None or n <= 0:
            continue
        if suf.startswith(("mill", "mm")) or suf == "m":
            n *= 1_000_000
        elif suf in ("mil", "k"):
            n *= 1_000
        usd = pre in ("us$", "usd") or suf in ("usd", "dólares", "dolares")
        val = int(n * USD_CLP) if usd else int(n)
        ctx = texto[max(0, m.start() - 30): m.end() + 30]
        out.append(val)
        if _RE_ANUAL.search(ctx) and val // 12 > 0:
            out.append(val // 12)
    return out


def sueldo_respaldado(valor: int, *textos: str, tol: float = 0.02) -> bool:
    """True si `valor` (CLP/mes) coincide (±tol) con algún monto del texto.
    Rechaza valores fuera de la banda física [FLOOR, CEILING]."""
    if not valor or valor < FLOOR or valor > CEILING:
        return False
    montos = extraer_montos(" ".join(t for t in textos if t))
    return any(abs(m - valor) <= max(1, int(tol * valor)) for m in montos)


def extraer_sueldo_texto(*textos: str) -> int:
    """Sueldo mensual determinístico del texto: el monto mínimo plausible
    (en un rango declara el piso). 0 si no hay."""
    montos = [m for m in extraer_montos(" ".join(t for t in textos if t))
              if FLOOR <= m <= CEILING]
    return min(montos) if montos else 0
