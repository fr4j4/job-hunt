"""Normalizadores puros (sin DB) de los campos crudos de `ofertas`.

Versión de las reglas: `VERSION`. Subirla fuerza a `materializar` a re-derivar todo.
"""
from __future__ import annotations

import re

from ..domain.geo_cl import COMUNAS, PISTAS_REGION, REGIONES, REMOTO_PISTAS
from ..domain.texto import _norm

VERSION = "n2"

# ---------------- rol / seniority ----------------

FAMILIAS = ("Desarrollo", "Datos e IA", "Infra y seguridad", "QA", "No desarrollo")
_FAMILIA_POR_ROL = {
    "Full Stack": "Desarrollo", "Backend": "Desarrollo", "Frontend": "Desarrollo",
    "Mobile": "Desarrollo", "Software": "Desarrollo", "Tech Lead": "Desarrollo",
    "Data": "Datos e IA", "AI/ML": "Datos e IA",
    "DevOps/Cloud": "Infra y seguridad", "Seguridad": "Infra y seguridad", "Soporte/TI": "Infra y seguridad",
    "QA": "QA",
}


def norm_rol_familia(rol_categoria: str | None) -> str:
    """17 roles → 5 familias. Todo lo no listado (incl. vacío) es 'No desarrollo'."""
    return _FAMILIA_POR_ROL.get((rol_categoria or "").strip(), "No desarrollo")


SENIORITIES = ("trainee", "junior", "semi", "senior", "lead")
_SENIORITY = {
    "trainee": "trainee", "practicante": "trainee", "noviciado": "trainee", "intern": "trainee",
    "internship": "trainee", "entry level": "junior", "entry-level": "junior",
    "junior": "junior", "jr": "junior",
    "semi": "semi", "semi senior": "semi", "semisenior": "semi", "semi-senior": "semi",
    "ssr": "semi", "mid": "semi", "mid-level": "semi", "intermedio": "semi", "associate": "semi",
    "senior": "senior", "sr": "senior", "mid senior": "senior", "mid-senior level": "senior",
    "mid-senior": "senior",
    "lead": "lead", "principal": "lead", "staff": "lead", "arquitecto": "lead", "director": "lead",
}


def norm_seniority(real: str | None, oficial: str | None = None) -> str:
    """`seniority_real` (IA/título) manda; `seniority_oficial` solo si el anterior no sirve.
    Valores basura ('deseable', 'no', …) → ''."""
    for v in (real, oficial):
        k = _norm(v or "").strip()
        if k in _SENIORITY:
            return _SENIORITY[k]
    return ""


# ---------------- modalidad / jornada / postulantes ----------------

def norm_modalidad(texto: str | None) -> str:
    t = _norm(texto or "")
    if "hibrid" in t or "hybrid" in t:
        return "hibrido"
    if "remot" in t or "teletrabajo" in t:
        return "remoto"
    if "presencial" in t or "on-site" in t or "onsite" in t:
        return "presencial"
    return ""


_RE_HIB = re.compile(r"hibrid|hybrid|\d\s*dias?\s*(?:en|de)\s*(?:la\s*)?oficina")
_RE_REM = re.compile(r"100\s*%\s*remot|full\s*remote|remoto\s*total|trabajo\s*remoto|work\s*from\s*home|teletrabajo")
_RE_PRE = re.compile(r"presencial|on-?site|en\s*oficina")


def inferir_modalidad(titulo: str | None, descripcion: str | None, remote_official: int | None = None) -> str:
    """Fallback cuando `modality` está vacío. Reglas contradictorias → '' (no adivinar)."""
    t = _norm(f"{titulo or ''} {(descripcion or '')[:1500]}")
    hits = {m for m, rx in (("hibrido", _RE_HIB), ("remoto", _RE_REM), ("presencial", _RE_PRE)) if rx.search(t)}
    if remote_official:
        hits.add("remoto")
    return hits.pop() if len(hits) == 1 else ""


def norm_empleo(texto: str | None) -> str:
    t = _norm(texto or "").replace("_", " ").replace("-", " ")
    if not t.strip():
        return ""
    if "part" in t or "parcial" in t or "media jornada" in t:
        return "parcial"
    if "full" in t or "completa" in t or "indefinid" in t:
        return "completa"
    if "contract" in t or "contrat" in t or "plazo fijo" in t or "temporal" in t or "honorario" in t:
        return "contrato"
    if "intern" in t or "practic" in t:
        return "practica"
    return "otro"


def norm_applicants(texto: str | None) -> int | None:
    m = re.search(r"\d+", texto or "")
    return int(m.group()) if m else None


# ---------------- ubicación / empresa ----------------

_RE_REGION_HOMONIMA = re.compile(
    r"\b(?:(?:valparaiso|antofagasta|coquimbo|arica|tarapaca|biobio) region|region de (?:valparaiso|antofagasta|coquimbo|arica|tarapaca|biobio))\b")


def norm_ubicacion(texto: str | None) -> tuple[str, str]:
    """→ (región, comuna). Remoto → ('remoto',''). Sin match → ('desconocida','')."""
    t = _norm(texto or "").replace("_", " ")
    t = re.sub(r"\d{5,}", " ", t)
    if not t.strip():
        return "desconocida", ""
    if any(p in t for p in REMOTO_PISTAS):
        return "remoto", ""
    # 'Valparaiso Region' / 'Region de Antofagasta' nombran la REGIÓN, no la comuna homónima
    t_com = _RE_REGION_HOMONIMA.sub(" ", t)
    cand = [k for k in COMUNAS if re.search(rf"(?<![a-z]){re.escape(k)}(?![a-z])", t_com)]
    if len(cand) > 1 and "santiago" in cand:
        cand.remove("santiago")      # 'Santiago Ñuñoa' = Ñuñoa; 'Santiago' suelto = la comuna Santiago
    mejor = max(cand, key=len) if cand else None
    if mejor:
        reg, bonito = COMUNAS[mejor]
        return REGIONES[reg], bonito
    for pista, reg in PISTAS_REGION:
        if pista in t:
            return REGIONES[reg], ""
    return "desconocida", ""


_SUFIJOS = re.compile(r"\b(spa|s\.?a\.?|ltda|limitada|chile|sa|inc|llc|corp|group|grupo)\b\.?")


def norm_empresa(texto: str | None, alias: dict[str, str] | None = None) -> str:
    """Clave de agrupación. '' para anónimas ('Confidencial')."""
    t = _norm(texto or "")
    t = re.sub(r"\s+[-–|]\s+(santiago|chile|region metropolitana)\b.*$", "", t)   # 'X - Santiago'
    t = t.replace("®️", "").replace("®", "")
    t = _SUFIJOS.sub(" ", t)
    t = re.sub(r"[^a-z0-9ñ ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t in ("", "confidencial", "empresa confidencial", "anonimo", "anonima"):
        return ""
    return (alias or {}).get(t, t)


# ---------------- sueldo ----------------

_RE_RANGO = re.compile(r"([\d.,]{5,})\s*(?:-|–|a|hasta|y)\s*\$?\s*([\d.,]{5,})", re.I)


def sueldo_rango(salary: str | None, description: str | None) -> tuple[int | None, int | None, str]:
    """→ (min, max, origen). Delega SIEMPRE el parseo numérico en scoring (fuente única).
    Rango solo con patrón explícito 'N - M'; si algo falla → (valor, valor)."""
    from ..scoring import _salary_to_clp_monthly
    sal, desc = salary or "", description or ""
    valor = _salary_to_clp_monthly(sal, desc)
    if valor is None:
        return None, None, ""
    origen = "salary" if re.search(r"\d", sal) else "description"
    m = _RE_RANGO.search(sal)
    if m:
        a = _salary_to_clp_monthly(m.group(1), "")
        b = _salary_to_clp_monthly(m.group(2), "")
        if a and b:
            return min(a, b), max(a, b), origen
    return valor, valor, origen
