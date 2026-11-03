"""Himalayas — ofertas remotas globales, API pública (sin auth).

GET https://himalayas.app/jobs/api/search?q=&country=CL&sort=recent
`country=CL` deja solo las ofertas cuya restricción geográfica incluye Chile (o sin restricción).
Campos: title, companyName, minSalary/maxSalary/currency/salaryPeriod, pubDate (epoch),
employmentType, seniority[], description (HTML), applicationLink, guid.
La API devuelve 20 por página (`page=N`, `totalCount` con el total): se leen hasta `max_pages`."""
import time

from ..domain.texto import MAX_DESC
from . import _comun


def jobs(queries: list[str], found_by_prefix: str = "", limit: int = 20, on_query=None,
         max_pages: int = 3) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries:
        for pag in range(1, max(1, max_pages) + 1):
            if on_query:
                try:
                    on_query(q, pag)
                except Exception:
                    pass
            d = _comun.get("himalayas", "https://himalayas.app/jobs/api/search",
                           {"q": q, "country": "CL", "sort": "recent", "page": pag})
            n = _agregar(out, (d.get("jobs") or [])[:limit], q, found_by_prefix)
            if n < limit:      # última página
                break
            time.sleep(1)
        time.sleep(1)
    return list(out.values())


def _agregar(out: dict, jobs_: list, q: str, found_by_prefix: str) -> int:
    """Suma las ofertas de una página a `out`; devuelve cuántas trajo la página (antes de dedup)."""
    for j in jobs_:
        url = j.get("applicationLink") or j.get("guid") or ""
        titulo = _comun.limpiar(j.get("title"))[:150]
        if not url or not titulo or url in out:
            continue
        sal = ""
        if j.get("minSalary") and j.get("maxSalary"):
            sal = f"{j.get('currency') or ''} {j['minSalary']}-{j['maxSalary']} {j.get('salaryPeriod') or ''}".strip()
        out[url] = {
            "title": titulo, "company": j.get("companyName") or "",
            "location": "Remoto (elegible desde Chile)",
            "date": _comun.fecha_epoch(j.get("pubDate")),
            "url": url, "source": f"himalayas:{q}", "found_by": f"{found_by_prefix}{q}",
            "modality": "remoto", "salary": sal[:60],
            "_desc": _comun.limpiar(j.get("description"))[:MAX_DESC],
            "description_source": "himalayas-api",
            "employment_type": j.get("employmentType") or "",
        }
    return len(jobs_)
