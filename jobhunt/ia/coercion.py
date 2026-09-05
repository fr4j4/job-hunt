"""Coerción de tipos de las respuestas IA (hoja: puro)."""
from __future__ import annotations

from ..domain.texto import _norm


def _coerce_salario(v) -> int:
    """salario_clp_mensual → int (F4/IA-4): float 2500000.0 → 2500000 (stats
    parseaba 'CLP 2500000.0' como 25000000); '2.500.000' → 2500000; bool/basura → 0."""
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else 0
    try:
        return max(0, int(float(str(v).replace(".", "").replace(",", "").strip())))
    except ValueError:
        return 0


def _normalizar_extract_local(d: dict) -> dict:
    """Coerción de tipos post-respuesta (P2-6): un 7B puede emitir strings
    donde el schema pide listas/ints. Defensa de segundo nivel."""
    out = dict(d)
    # salario: string → int/0
    out["salario_clp_mensual"] = _coerce_salario(out.get("salario_clp_mensual"))
    # listas: string → []
    for campo in ("techs", "red_flags", "green_flags", "benefits"):
        v = out.get(campo)
        if isinstance(v, str):
            out[campo] = [v]
        elif not isinstance(v, list):
            out[campo] = []
    # idiomas: string → [] (apply_ia_result normaliza dicts)
    if isinstance(out.get("idiomas"), str):
        out["idiomas"] = []
    elif not isinstance(out.get("idiomas"), list):
        out["idiomas"] = []
    # modalidad: palabra completa → código (defensa P1-1)
    mod = _norm(str(out.get("modalidad") or ""))
    if "remot" in mod:
        out["modalidad"] = "R"
    elif "híbrid" in mod or "hibrid" in mod:
        out["modalidad"] = "H"
    elif "presencial" in mod:
        out["modalidad"] = "P"
    elif mod not in ("r", "h", "p", "?"):
        out["modalidad"] = "?"
    # ingles: boolean → nivel (defensa P1-1)
    ing = out.get("ingles")
    if isinstance(ing, bool):
        out["ingles"] = "requerido" if ing else "desconocido"
    elif isinstance(ing, str) and ing.lower() not in ("no", "deseable", "requerido", "desconocido"):
        out["ingles"] = "desconocido"
    return out
