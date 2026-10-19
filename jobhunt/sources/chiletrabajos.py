"""Chiletrabajos.cl — listado HTML paginado + ficha (sin auth, sin Cloudflare).

Listado: GET https://www.chiletrabajos.cl/encuentra-un-empleo[/<offset>]?2=<consulta>
30 ofertas por página; el offset avanza de 30 en 30 (/30, /60, …).
Cada `div.job-item` trae título, empresa, ciudad, fecha ("14 de Octubre de 2026") y un resumen.
La ficha (/trabajo/<slug>-<id>) trae la descripción completa en `p.mb-0` tras `botones-desc`;
se pide solo para las primeras `max_detalles` ofertas del barrido (la descripción es lo que
permite decidir modalidad y sueldo)."""
import re
import time
import urllib.parse

from ..domain.texto import MAX_DESC
from . import _comun

_BASE = "https://www.chiletrabajos.cl/encuentra-un-empleo"
_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
          "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11,
          "diciembre": 12}


def _fecha(txt: str) -> str:
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", txt or "")
    mes = _MESES.get(m.group(2).lower()) if m else None
    return f"{m.group(3)}-{mes:02d}-{int(m.group(1)):02d}" if m and mes else ""


def parse_listado(html_: str) -> list[dict]:
    out = []
    for bloque in re.split(r'<div class="job-item', html_)[1:]:
        a = re.search(r'<h2 class="title[^>]*>\s*<a href="([^"]+/trabajo/[^"]+)"[^>]*>(.*?)</a>', bloque, re.S)
        if not a:
            continue
        meta = re.search(r'<h3 class="meta">\s*(.*?)\s*<a href="[^"]*/ciudad/[^"]*">(.*?)</a>', bloque, re.S)
        empresa = _comun.limpiar(meta.group(1)).rstrip(",").strip() if meta else ""
        ciudad = _comun.limpiar(meta.group(2)) if meta else ""
        fecha = re.search(r'<i class="far fa-calendar"></i>\s*([^<]+)<', bloque)
        resumen = re.search(r'<p class="description"[^>]*>(.*?)<a ', bloque, re.S)
        out.append({"url": a.group(1), "title": _comun.limpiar(a.group(2))[:150], "company": empresa,
                    "location": ciudad, "date": _fecha(fecha.group(1)) if fecha else "",
                    "resumen": _comun.limpiar(resumen.group(1)) if resumen else ""})
    return out


def parse_ficha(html_: str) -> dict:
    d = re.search(r'botones-desc.*?<p class="mb-0">(.*?)</p>', html_, re.S)
    tipo = re.search(r"<td>Tipo</td>\s*<td><a [^>]*>(.*?)</a>", html_, re.S)
    texto = _comun.limpiar(d.group(1)) if d else ""
    ti = re.search(r"<title>\s*(.*?)\s+-\s+[^<-]*\|\s*Chiletrabajos", html_, re.S)
    return {"description": texto, "employment_type": _comun.limpiar(tipo.group(1)) if tipo else "",
            "title": _comun.limpiar(ti.group(1))[:150] if ti else ""}


def jobs(queries: list[str], found_by_prefix: str = "", on_query=None, max_pages: int = 2,
         max_detalles: int = 60) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries:
        for pag in range(max(1, max_pages)):
            if on_query:
                try:
                    on_query(q, pag + 1)
                except Exception:
                    pass
            url = _BASE + (f"/{pag * 30}" if pag else "") + "?" + urllib.parse.urlencode({"2": q})
            html_ = _comun.get("chiletrabajos", url, as_text=True)
            items = parse_listado(html_) if html_ else []
            nuevos = [it for it in items if it["url"] not in out]
            for it in nuevos:
                out[it["url"]] = {
                    "title": it["title"], "company": it["company"], "location": it["location"],
                    "date": it["date"], "url": it["url"], "source": f"chiletrabajos:{q}",
                    "found_by": f"{found_by_prefix}{q}", "modality": "", "salary": "",
                    "_desc": it["resumen"][:MAX_DESC], "description_source": "chiletrabajos-serp",
                }
            if len(items) < 30 or not nuevos:      # última página o sin novedades
                break
            time.sleep(1)
        time.sleep(1)
    _completar_fichas(out, max_detalles)
    return list(out.values())


def _completar_fichas(out: dict, max_detalles: int) -> None:
    """Pide la ficha de las primeras `max_detalles` ofertas: descripción completa y modalidad."""
    for j in list(out.values())[:max_detalles]:
        html_ = _comun.get("chiletrabajos", j["url"], as_text=True)
        if not html_:
            continue
        f = parse_ficha(html_)
        if f["description"]:
            j["_desc"] = f["description"][:MAX_DESC]
            j["description_source"] = "chiletrabajos-html"
            j["modality"] = _comun.modalidad_en_texto(f["description"])
        if f["employment_type"]:
            j["employment_type"] = f["employment_type"]
        if f["title"] and j["title"].endswith("…"):      # el listado recorta los títulos largos
            j["title"] = f["title"]
        time.sleep(0.5)
