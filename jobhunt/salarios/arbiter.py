"""Árbitro de salario — decisión PURA (sin DB) de enrich._aplicar_ficha (§1.3).

Qué queda aquí: regla anual (/año → /12), arbitraje feed vs texto de la ficha,
leave-one-out del pool y la regla §2.5 (salario llegó después de la IA).

Qué NO queda aquí: la llamada a classify_salary (los tests la parchean sobre
jobhunt.stats y el paquete nuevo no puede importar la capa compat) ni el UPDATE.
El llamador clasifica con Decision.extra["pool_loo"] y pisa status/note.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import stats as _st

# opiniones de IA que dicen "sin salario" (§2.5)
_SIN_SALARIO_RE = re.compile(
    r"sin sueldo|sin salario|no declara|no se declara|carece de datos monetarios|"
    r"no informa salario|sin información salarial|no menciona el sueldo", re.I)


@dataclass
class Decision:
    salary: str | None = None
    salary_source: str | None = None
    salary_status: str | None = None
    salary_note: str | None = None
    unmark_ia: bool = False
    extra: dict = field(default_factory=dict)


class SalaryArbitrator:
    """cfg se acepta por simetría con el resto del pipeline; hoy no se usa."""

    def __init__(self, cfg=None):
        self.cfg = cfg

    def decide(self, row: dict, info: dict, pool: list[int]) -> Decision:
        """row: la oferta (incluye db_salary/db_source/db_status leídos del MAIN).
        info: la ficha ya fetcheada. pool: salarios del mercado (sin LOO)."""
        d = Decision()
        sal_actual = row.get("db_salary") or ""
        sal_source = row.get("db_source") or ""
        sal_status = row.get("db_status") or ""
        crudo = (info.get("salary") or "").strip()
        val = _st.parse_salary_clp(crudo) if crudo else 0
        if val > 0:
            # F2/S4: JSON-LD con unit YEAR ("/año") viene en anual — dividir por 12
            # si el resultado mensual es plausible (>=FLOOR), no solo cuando el
            # valor anual crudo supera CEILING (ej: 18M/año no superaba CEILING y
            # quedaba mal clasificado como mensual). salary_raw (texto crudo) no
            # se toca — solo el valor numérico usado para el árbitro/clasificación.
            if re.search(r"/año|/year|anual", crudo, re.I) and val // 12 >= _st.FLOOR:
                val = val // 12
                d.salary_note = "annual_likely"
            feed_val = _st.parse_salary_clp(sal_actual)
            feed_implausible = sal_status == "implausible" or \
                (feed_val > 0 and (feed_val < _st.FLOOR or feed_val > _st.CEILING))
            coincide = feed_val > 0 and abs(feed_val - val) <= max(1, int(0.01 * val))
            if feed_implausible or sal_source in ("", "feed") and feed_val == 0:
                d.salary, d.salary_source, d.salary_status = crudo[:40], "text", "trusted"
                d.salary_note = d.salary_note or ("text_wins" if feed_implausible else "text_confirms")
            elif coincide:
                d.salary_source, d.salary_status = "text", "trusted"
                d.salary_note = d.salary_note or "text_confirms"
            else:
                d.salary, d.salary_source, d.salary_status = crudo[:40], "text", "trusted"
                d.salary_note = "text_wins"
        elif sal_status == "implausible":
            d.salary, d.salary_source = "", "feed"
            d.salary_status, d.salary_note = "implausible", "source_unverifiable"
        # leave-one-out para la clasificación estadística (§4): quita UNA instancia
        if d.salary:
            v = _st.parse_salary_clp(d.salary)
            pool_loo = list(pool)
            if v in pool_loo:
                pool_loo.remove(v)
            d.extra = {"value": v, "pool_loo": pool_loo}
        # §2.5: la oferta no tenía salario, ahora sí, y la opinion IA decía
        # "sin salario" → desmarcar para re-enriquecer (auto-curativo).
        d.unmark_ia = bool(not row.get("salary") and d.salary and row.get("ia_model")
                           and _SIN_SALARIO_RE.search(row.get("ai_opinion") or ""))
        return d
