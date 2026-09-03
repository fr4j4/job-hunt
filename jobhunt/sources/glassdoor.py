"""Glassdoor Chile: tls_client + CSRF + GraphQL — self-contained, sin jobspy.

Constantes en sources/constants/ extraídas del fork de jobspy (SCRAPING_CHILE.md §4).
Requiere tls-client en requirements.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tls_client

from ..logging_setup import get_logger

log = get_logger(__name__)

CONST_DIR = Path(__file__).parent / "constants"
_HEADERS = json.loads((CONST_DIR / "glassdoor_headers.json").read_text())
_QUERY_TEMPLATE = (CONST_DIR / "glassdoor_query.graphql").read_text()

_LOCATION_ID_CHILE = 49  # COUNTRY (ver SCRAPING_CHILE.md §4.3)


def _session():
    """Nueva sesión con cookies CF + token CSRF. (session, token)"""
    s = tls_client.Session(client_identifier="chrome_131", random_tls_extension_order=True)
    s.headers.update(_HEADERS)
    # warm-up cookies (obligatorio: gdsid, gdId, asst, __cf_bm)
    s.get("https://www.glassdoor.com/index.htm", timeout_seconds=25)
    page = s.get("https://www.glassdoor.com/job-listing/", timeout_seconds=25)
    toks = re.findall(r'"token":\s*"([^"]+)"', page.text)
    if not toks:
        raise RuntimeError("glassdoor: challenge Cloudflare o sin token CSRF")
    return s, toks[0]


def jobs(queries: list[str], found_by_prefix: str = "", max_pages: int = 2, on_query=None) -> list[dict]:
    """Busca ofertas en Glassdoor Chile (locationId=49, COUNTRY). Pagina hasta max_pages
    ( pageNumber es un arg real del GraphQL; páginas >1 traen ofertas distintas)."""
    out: dict[str, dict] = {}
    try:
        s, token = _session()
    except Exception as e:
        log.warning("glassdoor sesión falló: %s", e)
        return out

    for q in queries:
        for pag in range(1, max_pages + 1):
            if on_query:
                try:
                    on_query(q, pag)
                except Exception:
                    pass
            payload = {
                "operationName": "JobSearchResultsQuery",
                "variables": {
                    "excludeJobListingIds": [],
                    "filterParams": [{"filterKey": "fromAge", "values": "30"}],
                    "keyword": q, "numJobsToShow": 20,
                    "locationType": "COUNTRY", "locationId": _LOCATION_ID_CHILE,
                    "pageNumber": pag, "originalPageUrl": "",
                    "parameterUrlInput": "", "seoUrl": False,
                },
                "query": _QUERY_TEMPLATE,
            }
            try:
                r = s.post("https://www.glassdoor.com/graph",
                           headers={"gd-csrf-token": token, "content-type": "application/json"},
                           json=payload, timeout_seconds=30)
                d = r.json()
                if isinstance(d, list):
                    d = d[0]
                jl = (d.get("data") or {}).get("jobListings") or {}
                listings = jl.get("jobListings") or []
                if not listings:
                    break
                for it in listings:
                    jv = it.get("jobview") or {}
                    hdr = jv.get("header") or {}
                    job = jv.get("job") or {}
                    age = hdr.get("ageInDays")
                    date = (datetime.now(timezone.utc) - timedelta(days=int(age))).date().isoformat() if age is not None else ""
                    lid = job.get("listingId")
                    if not lid or lid in out:
                        continue
                    # desc ya viene en la respuesta GraphQL (job.description) — limpiar HTML
                    desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", job.get("description") or "")).strip()
                    out[lid] = {
                        "title": (hdr.get("jobTitleText") or "")[:150],
                        "company": hdr.get("employerNameFromSearch") or "",
                        "location": hdr.get("locationName") or "",
                        "date": date,
                        "url": f"https://www.glassdoor.com/job-listing/j?jl={lid}",
                        "source": f"glassdoor:{q}",
                        "found_by": f"{found_by_prefix}{q}",
                        "_desc": desc[:4000],
                    }
            except Exception as e:
                log.warning("glassdoor query '%s' p%s falló: %s", q, pag, e)
                break
            time.sleep(4)
    return list(out.values())
