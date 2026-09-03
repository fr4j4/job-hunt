"""Accenture — fuente directa vía /api/accenture/elastic/findjobs (multipart POST).

Sin Cloudflare agresivo: un GET a la página de jobsearch para cookies basta.
searchType=vectorSearch con minScore 0.6 (búsqueda semántica del propio Elastic de Accenture).
Campos ricos: title, location, remoteType (Hybrid El/Remote/Onsite), careerLevel,
yearsOfExperience (ej "5-10"), mustHaveSkills, jobDescriptionClean, jobDetailUrl.
El jobDetailUrl trae placeholder {0} → reemplazar por countrySite (cl-es).
"""
import json
import re
import time
from datetime import datetime, timezone
from html import unescape as _u

import requests

from ..logging_setup import get_logger

log = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
       "Accept-Language": "es-CL,es;q=0.9"}
_URL = "https://www.accenture.com/api/accenture/elastic/findjobs"


def _clean(s) -> str:
    if isinstance(s, list):
        s = " ".join(str(x) for x in s)
    return re.sub(r"\s+", " ", _u(str(s or ""))).strip()


def _search_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_UA)
    try:
        s.get("https://www.accenture.com/cl-es/careers/jobsearch", timeout=25)
    except Exception as e:
        log.warning("accenture home falló: %s", e)
    return s


def jobs(queries: list[str], found_by_prefix: str = "", max_pages: int = 2) -> list[dict]:
    """Ofertas de Accenture Chile por keywords. Sin API key — endpoint público."""
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    s = _search_session()
    for q in queries:
        for page in range(max_pages):
            try:
                r = s.post(_URL,
                           files={"startIndex": (None, str(page * 12)),
                                  "maxResultSize": (None, "12"),
                                  "jobKeyword": (None, q),
                                  "jobCountry": (None, "Chile"),
                                  "jobLanguage": (None, "es"),
                                  "countrySite": (None, "cl-es"),
                                  "sortBy": (None, "0"),
                                  "searchType": (None, "vectorSearch"),
                                  "enableQueryBoost": (None, "true"),
                                  "minScore": (None, "0.6"),
                                  "score": (None, "true"),
                                  "totalHits": (None, "true"),
                                  "debugQuery": (None, "false"),
                                  "jobFilters": (None, "[]")},
                           headers={"Origin": "https://www.accenture.com",
                                    "Referer": "https://www.accenture.com/cl-es/careers/jobsearch"},
                           timeout=25)
                d = r.json()
            except Exception as e:
                log.warning("accenture fetch falló (%s p%s): %s", q[:30], page, e)
                break
            data = d.get("data") or []
            if not data:
                break
            for a in data:
                guid = a.get("guid") or a.get("requisitionId") or ""
                url_raw = a.get("jobDetailUrl") or ""
                url = url_raw.replace("{0}", "cl-es")
                uid = guid or url_raw
                if not uid or uid in out:
                    continue
                # postedDateText: "Publicado el 27-08-2026" o similar
                m = re.search(r"(\d{2,4}-\d{1,2}-\d{1,4})", str(a.get("postedDateText") or ""))
                if m:
                    fecha = m.group(1)
                    try:
                        parts = fecha.split("-")
                        fecha = (parts[2] if len(parts[2]) == 4 else "20" + parts[2]) + "-" + \
                                parts[1].zfill(2) + "-" + parts[0].zfill(2)
                    except Exception:
                        fecha = now.date().isoformat()
                else:
                    fecha = now.date().isoformat()
                # remoteType: "Hybrid El..." / "Remote..." / "Onsite..."
                remote = (a.get("remoteType") or "").strip()
                modality = ("híbrido" if remote.lower().startswith("hybrid")
                            else "remoto" if remote.lower().startswith("remote")
                            else "presencial" if remote.lower().startswith(("onsite", "on-site", "presencial"))
                            else "")
                desc = _clean(a.get("jobDescriptionClean") or a.get("jobDescription") or "")[:2000]
                out[uid] = {
                    "title": _clean(a.get("title"))[:150],
                    "company": "Accenture",
                    "location": _clean(a.get("location") or ""),
                    "date": fecha,
                    "url": url,
                    "source": f"accenture:{q}",
                    "found_by": f"{found_by_prefix}{q}",
                    "salary": "",
                    "modality": modality,
                    "_desc": desc,
                    "description_source": "accenture-api",
                }
            total = (d.get("totalHits") or {}).get("total") or 0
            if (page + 1) * 12 >= int(total or 0):
                break
            time.sleep(2)
        time.sleep(3)
    return list(out.values())