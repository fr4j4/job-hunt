"""Validación determinística de lo que devuelve la IA contra el texto de la oferta.

Hoja pura (sin DB ni red). Un LLM local pequeño rellena huecos con lo que conoce
(el stack del perfil, techs genéricos); aquí se descarta todo lo que el texto no
respalda — misma filosofía que salarios/texto.py para el sueldo.
"""
from __future__ import annotations

import html
import re
import unicodedata

MIN_TEXTO_IA = 200          # chars de descripción para que la IA tenga algo que leer
FRASE_SIN_TEXTO = "Sin descripción disponible para analizar la oferta."
FRASE_NO_RESPALDADA = "Sin análisis automático confiable: el comentario generado no se apoyaba en el texto de la oferta."

_RUIDO_FLAGS = {"no declarado", "no declarada", "no especificado", "no especificada",
                "n/a", "na", "ninguno", "ninguna", "sin datos", "desconocido"}
_CONECTORES = {"de", "del", "la", "el", "los", "las", "para", "en", "y", "con", "por", "a", "al"}
_NEGACION = re.compile(r"\bno\s+(coincid|calz|encaj|cuadr)|ajeno|distint[oa]\s+al\s+perfil|"
                       r"fuera\s+del\s+perfil|no\s+(es|corresponde)", re.I)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", html.unescape(str(s or "")))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def texto_suficiente(descripcion: str | None) -> bool:
    """True si la descripción tiene texto real para que la IA analice la oferta."""
    return len((descripcion or "").strip()) >= MIN_TEXTO_IA


def _en_texto(termino: str, texto_norm: str) -> bool:
    t = _norm(termino).strip()
    if not t:
        return False
    return re.search(r"(?<![a-z0-9+#.])" + re.escape(t) + r"(?![a-z0-9+#])", texto_norm) is not None


def _forma_de_tech(t: str) -> bool:
    """Una tecnología es un nombre corto: ≤3 palabras, sin conectores de frase
    ('integración de sistemas', 'simuladores de vuelo' son conceptos, no techs)."""
    palabras = t.lower().split()
    return bool(t) and len(t) <= 24 and len(palabras) <= 3 and not (set(palabras) & _CONECTORES)


def tech_respaldada(tech: str, texto: str) -> bool:
    """La tecnología figura literal en el texto. Descarta frases largas (no son techs)."""
    t = str(tech or "").strip()
    if not _forma_de_tech(t):
        return False
    return _en_texto(t, _norm(texto))


def limpiar_techs(techs: list, texto: str, alias: dict[str, tuple[str, ...]] | None = None) -> list[str]:
    """Conserva solo las techs respaldadas por el texto. alias: abreviatura → formas
    largas aceptables (p.ej. 'ts' → ('typescript',)); la abreviatura vale si aparece
    alguna de sus formas en el texto."""
    out: list[str] = []
    tn = _norm(texto)
    for t in techs or []:
        t = str(t or "").strip()
        if not _forma_de_tech(t):
            continue
        ok = _en_texto(t, tn) or any(_en_texto(a, tn) for a in (alias or {}).get(_norm(t), ()))
        if ok and t not in out:
            out.append(t)
    return out


def limpiar_flags(flags: list) -> list[str]:
    """Quita relleno tipo 'no declarado' y duplicados."""
    out: list[str] = []
    for f in flags or []:
        s = re.sub(r"\s+", " ", str(f or "")).strip()
        if s and _norm(s).strip(" .") not in _RUIDO_FLAGS and s not in out:
            out.append(s)
    return out


def quitar_duplicados(benefits: list, otros: list) -> list[str]:
    """benefits sin los elementos que ya están en `otros` (green_flags)."""
    ya = {_norm(x) for x in otros or []}
    return [b for b in benefits or [] if _norm(b) not in ya]


def texto_cita_perfil_ajeno(texto_ia: str, oferta: str, techs_perfil: list[str]) -> bool:
    """True si el comentario de la IA menciona una tecnología del PERFIL que la oferta
    no contiene (el modelo proyecta el stack del candidato sobre la oferta).
    Si el comentario lo dice en negativo ('no coincide con el perfil…') es válido."""
    if not texto_ia or _NEGACION.search(texto_ia):
        return False
    tn_ia, tn_of = _norm(texto_ia), _norm(oferta)
    return any(_en_texto(t, tn_ia) and not _en_texto(t, tn_of) for t in techs_perfil or [])


_RE_ING = re.compile(r"ingl[eé]s|english", re.I)
_RE_ING_NIVEL = re.compile(r"avanzad|intermedi|fluid|b2|c1|c2|advanced|fluent|upper|conversacional|"
                           r"excluyente|obligatori|requerid|required|must", re.I)
_RE_ING_DESEABLE = re.compile(r"deseable|valorad|plus|ideal|se valora|nice to have|preferibl", re.I)


def ingles_desde_texto(texto: str) -> str:
    """'requerido'|'deseable'|'' según frases explícitas del texto (respaldo de la IA)."""
    for m in _RE_ING.finditer(texto or ""):
        ctx = texto[max(0, m.start() - 60): m.end() + 70]
        if _RE_ING_DESEABLE.search(ctx):
            return "deseable"
        if _RE_ING_NIVEL.search(ctx):
            return "requerido"
    return ""
