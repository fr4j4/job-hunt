"""Staffy Jobs (jobs.wearestaffy.com) — portal propio de una consultora de staffing IT (sin auth).

Listado: GET /vacantes → todas las vacantes activas en una sola página, cada una un
`article.job-card` con 'País · Modalidad', título, extracto y enlace /positions/<slug>-<id>.
Ficha: GET /positions/<slug>-<id> → h1, `div.metadata` (ubicación, modalidad, nivel) y la descripción
completa dentro de `article.position-content`.
El portal no publica empresa cliente, sueldo ni fecha. Se conservan las vacantes elegibles desde Chile
(Chile, LATAM, remoto global); las de otros países con presencia obligatoria se descartan.
Staffy es intermediario: company = 'Staffy'."""
import re
import time

from ..domain.texto import MAX_DESC
from . import _comun

_BASE = "https://jobs.wearestaffy.com"
_CARD = re.compile(r'<article class="job-card">\s*<div>\s*<p class="eyebrow">(.*?)</p>\s*<h2>(.*?)</h2>'
                   r'\s*<p>(.*?)</p>.*?href="(/positions/[^"?#]+)"', re.S)
# elegible desde Chile: la ubicación nombra Chile/CL, LATAM o global; "AR, PE, UY" o "USA" no
_ELEGIBLE = re.compile(_comun.ELEGIBLE_CL.pattern + r"|\bcl\b|global", re.I)


def _modalidad(txt: str) -> str:
    t = (txt or "").lower()
    if "híbrid" in t or "hibrid" in t or "hybrid" in t:
        return "híbrido"
    if "remot" in t:
        return "remoto"
    if "presencial" in t or "on-site" in t or "onsite" in t:
        return "presencial"
    return ""


def parse_listado(html_: str) -> list[dict]:
    out = []
    for eyebrow, titulo, extracto, path in _CARD.findall(html_ or ""):
        lugar, _, mod = _comun.limpiar(eyebrow).partition("·")
        out.append({"url": _BASE + path, "title": _comun.limpiar(titulo)[:150],
                    "location": lugar.strip(), "modality_txt": mod.strip(),
                    "resumen": _comun.limpiar(extracto)})
    return out


def parse_ficha(html_: str) -> dict:
    art = re.search(r'<article class="position-content">(.*?)</article>', html_ or "", re.S)
    if not art:
        return {"description": "", "meta": []}
    cuerpo = art.group(1)
    meta = re.search(r'<div class="metadata">(.*?)</div>', cuerpo, re.S)
    spans = [_comun.limpiar(s) for s in re.findall(r"<span>(.*?)</span>", meta.group(1), re.S)] if meta else []
    resto = cuerpo[meta.end():] if meta else cuerpo
    resto = re.sub(r"</(?:h2|p|li)>", ". ", resto)
    return {"description": _comun.limpiar(resto).replace(" .", ".").replace("..", ".")[:MAX_DESC], "meta": spans}


def jobs(queries=None, found_by_prefix: str = "", on_query=None, max_detalles: int = 80) -> list[dict]:
    """`queries` se ignora (el portal no tiene búsqueda: se lee el catálogo completo)."""
    if on_query:
        try:
            on_query("vacantes", 1)
        except Exception:
            pass
    items = parse_listado(_comun.get("staffy", f"{_BASE}/vacantes", as_text=True))
    out: dict[str, dict] = {}
    for it in items:
        if not _ELEGIBLE.search(it["location"]) or it["url"] in out:
            continue
        out[it["url"]] = {
            "title": it["title"], "company": "Staffy", "location": it["location"], "date": "",
            "url": it["url"], "source": "staffy:vacantes", "found_by": f"{found_by_prefix}vacantes",
            "modality": _modalidad(it["modality_txt"]), "salary": "",
            "_desc": it["resumen"][:MAX_DESC], "description_source": "staffy-serp",
        }
    for j in list(out.values())[:max_detalles]:
        f = parse_ficha(_comun.get("staffy", j["url"], as_text=True))
        if f["description"]:
            j["_desc"] = f["description"]
            j["description_source"] = "staffy-html"
            if f["meta"]:
                j["location"] = f["meta"][0] or j["location"]
                j["modality"] = _modalidad(" ".join(f["meta"][1:])) or j["modality"]
        time.sleep(0.5)
    return list(out.values())


def fetch_ficha(url: str) -> dict:
    """Ficha para el enriquecimiento (enrich._fetch_ficha)."""
    f = parse_ficha(_comun.get("staffy", url.split("?")[0], as_text=True))
    if not f["description"]:
        return {"_access": "blocked", "error": "staffy sin ficha"}
    return {"_access": "ok", "description": f["description"],
            "modality_badge": _modalidad(" ".join(f["meta"][1:])), "location": f["meta"][0] if f["meta"] else ""}
