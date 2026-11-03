"""Get on Board — bolsa tech LatAm, API pública v0 (sin auth).

GET https://www.getonbrd.com/api/v0/search/jobs?query=&per_page=&page=&expand=["company"]
- data[].attributes: title, countries (["Chile"] | ["Remote"] | …), remote_modality
  (fully_remote|remote_local|hybrid|no_remote), min_salary/max_salary (USD mensual), published_at
  (epoch), description/functions/desirable (HTML), company.data.attributes.name
- link público en data[].links.public_url
Se quedan las ofertas de Chile o remotas (countries vacío = sin restricción).
`countries` viene localizado según Accept-Language: con es-CL el remoto llega como "Remoto", con
otro idioma como "Remote" → se comparan ambas grafías. La API pagina (`total_pages` en meta);
se leen hasta `max_pages` páginas por consulta."""
import time

from ..domain.texto import MAX_DESC
from . import _comun

_PAISES_OK = {"chile", "remote", "remoto"}   # grafías localizadas del mismo "elegible desde Chile"
_MODALIDAD = {"fully_remote": "remoto", "remote_local": "remoto", "hybrid": "híbrido",
              "no_remote": "presencial"}


def jobs(queries: list[str], found_by_prefix: str = "", per_page: int = 30, on_query=None,
         max_pages: int = 3) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries:
        for pag in range(1, max(1, max_pages) + 1):
            if on_query:
                try:
                    on_query(q, pag)
                except Exception:
                    pass
            d = _comun.get("getonboard", "https://www.getonbrd.com/api/v0/search/jobs",
                           {"query": q, "per_page": per_page, "page": pag, "expand": '["company"]'})
            _agregar(out, d, q, found_by_prefix)
            if pag >= int((d.get("meta") or {}).get("total_pages") or 1):
                break
            time.sleep(1)
        time.sleep(1)
    return list(out.values())


def _agregar(out: dict, d: dict, q: str, found_by_prefix: str) -> None:
    for it in d.get("data") or []:
        a = it.get("attributes") or {}
        paises = a.get("countries") or []
        if paises and not ({p.strip().lower() for p in paises} & _PAISES_OK):
            continue
        url = (it.get("links") or {}).get("public_url") or ""
        titulo = _comun.limpiar(a.get("title"))[:150]
        if not url or not titulo or url in out:
            continue
        mod = _MODALIDAD.get(a.get("remote_modality") or "", "")
        sal = ""
        if a.get("min_salary") and a.get("max_salary"):
            sal = f"USD {a['min_salary']}-{a['max_salary']} mensual"
        comp = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name", "")
        desc = " ".join(_comun.limpiar(a.get(k)) for k in
                        ("description", "functions", "desirable", "benefits")).strip()
        out[url] = {
            "title": titulo, "company": comp,
            "location": ", ".join(paises) or "Chile",
            "date": _comun.fecha_epoch(a.get("published_at")),
            "url": url, "source": f"getonboard:{q}", "found_by": f"{found_by_prefix}{q}",
            "modality": mod, "salary": sal, "_desc": desc[:MAX_DESC],
            "description_source": "getonboard-api",
        }
