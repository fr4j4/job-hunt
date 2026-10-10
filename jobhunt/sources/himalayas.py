"""Himalayas — ofertas remotas globales, API pública (sin auth).

GET https://himalayas.app/jobs/api/search?q=&country=CL&sort=recent
`country=CL` deja solo las ofertas cuya restricción geográfica incluye Chile (o sin restricción).
Campos: title, companyName, minSalary/maxSalary/currency/salaryPeriod, pubDate (epoch),
employmentType, seniority[], description (HTML), applicationLink, guid."""
import time

from ..domain.texto import MAX_DESC
from . import _comun


def jobs(queries: list[str], found_by_prefix: str = "", limit: int = 20, on_query=None) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries:
        if on_query:
            try:
                on_query(q, 1)
            except Exception:
                pass
        d = _comun.get("himalayas", "https://himalayas.app/jobs/api/search",
                       {"q": q, "country": "CL", "sort": "recent"})
        for j in (d.get("jobs") or [])[:limit]:
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
        time.sleep(1)
    return list(out.values())
