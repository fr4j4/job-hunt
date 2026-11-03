"""Remotive — ofertas remotas, API pública (sin auth).

GET https://remotive.com/api/remote-jobs?search=&limit=
`candidate_required_location` ("Worldwide", "LATAM", "USA, Canada"…): solo se conservan las
elegibles desde Chile. La API mezcla categorías no técnicas → se filtra por categoría software."""
import time

from ..domain.texto import MAX_DESC
from . import _comun

_CATEGORIAS_TECH = ("software", "devops", "data", "qa", "sysadmin")


def jobs(queries: list[str], found_by_prefix: str = "", limit: int = 50, on_query=None) -> list[dict]:
    out: dict[str, dict] = {}
    for q in queries:
        if on_query:
            try:
                on_query(q, 1)
            except Exception:
                pass
        d = _comun.get("remotive", "https://remotive.com/api/remote-jobs",
                       {"search": q, "limit": limit})
        for j in d.get("jobs") or []:
            loc = j.get("candidate_required_location") or ""
            cat = (j.get("category") or "").lower()
            url = j.get("url") or ""
            titulo = _comun.limpiar(j.get("title"))[:150]
            if (not _comun.ELEGIBLE_CL.search(loc) or not any(c in cat for c in _CATEGORIAS_TECH)
                    or not url or not titulo or url in out):
                continue
            out[url] = {
                "title": titulo, "company": j.get("company_name") or "",
                "location": f"Remoto ({loc})"[:80],
                "date": (j.get("publication_date") or "")[:10],
                "url": url, "source": f"remotive:{q}", "found_by": f"{found_by_prefix}{q}",
                "modality": "remoto", "salary": (j.get("salary") or "")[:60],
                "_desc": _comun.limpiar(j.get("description"))[:MAX_DESC],
                "description_source": "remotive-api",
                "employment_type": (j.get("job_type") or "").replace("_", " "),
            }
        time.sleep(1)
    return list(out.values())
