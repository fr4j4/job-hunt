"""Indeed Chile: GraphQL app móvil."""
import json, re, ssl, time, urllib.request

_CTX = ssl._create_unverified_context()
_HEADERS = {
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "accept": "application/json", "indeed-locale": "en-US",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Indeed App 193.1",
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
    "indeed-co": "CL",
}
_QUERY = """query GetJobData {
  jobSearch(what: "%(what)s" location: {where: "Chile", radius: 50, radiusUnit: MILES}
            limit: 20 sort: RELEVANCE filters: {date: {field: "dateOnIndeed", start: "168h"}}) {
    results { job { key title datePublished description { html }
      location { countryName formatted { short long } }
      compensation { baseSalary { unitOfWork range { ... on Range { min max } } } currencyCode }
      employer { name } } }
  }
}"""

import sys  # noqa: F401
from ..logging_setup import get_logger

log = get_logger(__name__)

def _iso(v):
    from datetime import datetime, timezone
    if v is None: return ""
    if isinstance(v, (int, float)):
        try: return datetime.fromtimestamp(float(v), tz=timezone.utc).date().isoformat()
        except Exception: return ""
    return str(v)[:10]

def jobs(queries, found_by_prefix=""):
    out = []
    for q in queries:
        body = {"query": _QUERY % {"what": q}}
        req = urllib.request.Request("https://apis.indeed.com/graphql",
            data=json.dumps(body).encode(), headers=_HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
                d = json.loads(r.read())
        except Exception as e:
            log.warning("indeed query '%s' falló: %s", q, e); continue
        for r2 in (d.get("data", {}).get("jobSearch", {}) or {}).get("results", []):
            j = r2.get("job") or {}
            if not j.get("title"): continue
            loc = j.get("location") or {}
            comp = j.get("compensation") or {}
            base = comp.get("baseSalary") or {}
            rng = base.get("range") or {}
            salary = ""
            if rng.get("min") or rng.get("max"):
                salary = f"{comp.get('currencyCode','')} {rng.get('min','?')}-{rng.get('max','?')} {(base.get('unitOfWork') or '').lower()}".strip()[:40]
            desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", (j.get("description") or {}).get("html") or "")).strip()
            fb = f"{found_by_prefix}{q}"
            out.append({"title": re.sub(r"\s+", " ", j["title"])[:150],
                        "company": (j.get("employer") or {}).get("name") or "",
                        "location": (loc.get("formatted") or {}).get("long") or loc.get("countryName") or "",
                        "date": _iso(j.get("datePublished")), "url": f"https://cl.indeed.com/viewjob?jk={j.get('key','')}",
                        "source": f"indeed:{q}", "salary": salary, "_desc": desc[:4000], "found_by": fb})
        time.sleep(2)
    return out
