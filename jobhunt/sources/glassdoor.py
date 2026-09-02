"""Glassdoor Chile: tls_client + CSRF + GraphQL — self-contained, sin jobspy.

Constantes (query_template, headers) viven en sources/constants/ extraídas del
fork de jobspy (SCAPING_CHILE.md §4). Requiere `tls-client` en requirements.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tls_client

from .linkedin import fetch  # reusar el fetch genérico

CONST_DIR = Path(__file__).parent / "constants"
_HEADERS = json.loads((CONST_DIR / "glassdoor_headers.json").read_text())
_QUERY_TEMPLATE = (CONST_DIR / "glassdoor_query.graphql").read_text()

_LOCATION_ID_CHILE = 49  # COUNTRY (ver SCRAPING_CHILE.md §4.3)


def _session() -> "tls_client.Session":
    s = tls_client.Session(client_identifier="chrome_131", random_tls_extension_order=True)
    s.headers.update(_HEADERS)
    # warm-up cookies (obligatorio: gdsid, gdId, asst, __cf_bm)
    s.get("https://www.glassdoor.com/index.htm", timeout_seconds=25)
    page = s.get("https://www.glassdoor.com/job-listing/", timeout_seconds=25)
    toks = re.findall(r'"token":\s*"([^"]+)"', page.text)
    if not toks:
        raise RuntimeError("glassdoor: challenge Cloudflare o sin token CSRF")
    return s, toks[0]


def jobs(queries: list[str], found_by_prefix: str = "") -> list[dict]:
    """Busca ofertas en Glassdoor Chile (locationId=49, COUNTRY)."""
    out: list[dict] = []
    try:
        s, token = _session()
    except Exception as e:
        print(f"WARN glassdoor session: {e}", file=__import__("sys").stderr)
        return out

    for q in queries:
        payload = {
            "operationName": "JobSearchResultsQuery",
            "variables": {
                "excludeJobListingIds": [],
                "filterParams": [{"filterKey": "fromAge", "values": "30"}],
                "keyword": q, "numJobsToShow": 20,
                "locationType": "COUNTRY", "locationId": _LOCATION_ID_CHILE,
                "pageNumber": 1, "originalPageUrl": "",
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
            for it in jl.get("jobListings") or []:
                jv = it.get("jobview") or {}
                hdr = jv.get("header") or {}
                job = jv.get("job") or {}
                age = hdr.get("ageInDays")
                date = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=int(age))).date().isoformat() if age is not None else ""
                lid = job.get("listingId")
                if not lid:
                    continue
                out.append({
                    "title": (hdr.get("jobTitleText") or "")[:150],
                    "company": hdr.get("employerNameFromSearch") or "",
                    "location": hdr.get("locationName") or "",
                    "date": date,
                    "url": f"https://www.glassdoor.com/job-listing/j?jl={lid}",
                    "source": f"glassdoor:{q}",
                    "found_by": f"{found_by_prefix}{q}",
                })
        except Exception as e:
            print(f"WARN glassdoor '{q}': {e}", file=__import__("sys").stderr)
        import time
        time.sleep(4)
    return out


def _QUERY() -> str:
    return (CONST_DIR / "glassdoor_query.graphql").read_text()