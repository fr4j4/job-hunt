"""Glassdoor: tls_client + CSRF + GraphQL. Corre en subprocess (~/.venvs/buscador)."""
import json, subprocess, time
from pathlib import Path

_VENV_PY = Path.home() / ".venvs" / "buscador" / "bin" / "python"
_SEARCH = r'''
import re, json, sys, tls_client
from jobspy.glassdoor.constant import query_template, headers as gd_headers
keyword = sys.argv[1]
s = tls_client.Session(client_identifier="chrome_131", random_tls_extension_order=True)
s.headers.update(gd_headers)
s.get("https://www.glassdoor.com/index.htm", timeout_seconds=25)
page = s.get("https://www.glassdoor.com/job-listing/", timeout_seconds=25)
toks = re.findall(r'"token":\s*"([^"]+)"', page.text)
if not toks:
    print(json.dumps({"error": "no csrf"})); sys.exit(0)
token = toks[0]
jobs = []
for page_num in (1, 2):
    payload = {"operationName": "JobSearchResultsQuery",
        "variables": {"excludeJobListingIds": [], "filterParams": [{"filterKey": "fromAge", "values": "30"}],
            "keyword": keyword, "numJobsToShow": 20, "locationType": "COUNTRY", "locationId": 49,
            "pageNumber": page_num, "originalPageUrl": "", "parameterUrlInput": "", "seoUrl": False},
        "query": query_template}
    r = s.post("https://www.glassdoor.com/graph",
               headers={"gd-csrf-token": token, "content-type": "application/json"},
               json=payload, timeout_seconds=30)
    d = r.json()
    if isinstance(d, list): d = d[0]
    jl = ((d.get("data") or {}).get("jobListings") or {})
    items = jl.get("jobListings") or []
    if not items: break
    for it in items:
        jv = it.get("jobview") or {}
        hdr = jv.get("header") or {}
        job = jv.get("job") or {}
        age = hdr.get("ageInDays")
        from datetime import datetime, timedelta, timezone
        date = (datetime.now(timezone.utc) - timedelta(days=int(age))).date().isoformat() if age is not None else ""
        lid = job.get("listingId")
        jobs.append({"title": (hdr.get("jobTitleText") or "")[:150],
            "company": hdr.get("employerNameFromSearch") or "",
            "location": hdr.get("locationName") or "",
            "date": date, "url": f"https://www.glassdoor.com/job-listing/j?jl={lid}",
            "source": f"glassdoor:{keyword}", "_listing_id": lid})
    curs = jl.get("paginationCursors") or []
    if not any(c.get("pageNumber") == page_num + 1 for c in curs): break
print(json.dumps({"jobs": jobs}, ensure_ascii=False))
'''

def jobs(queries, found_by_prefix="", venv_py=None):
    out = []
    tmp = Path("/tmp/_jobhunt_gd.py")
    tmp.write_text(_SEARCH)
    for q in queries:
        try:
            proc = subprocess.run([str(_VENV_PY), str(tmp), q], capture_output=True, text=True, timeout=120)
            res = json.loads(proc.stdout) if proc.returncode == 0 else {}
            for j in res.get("jobs", []):
                j["found_by"] = f"{found_by_prefix}{q}"
                out.append(j)
        except Exception as e:
            print(f"WARN glassdoor '{q}': {e}", file=__import__("sys").stderr)
        time.sleep(4)
    for j in out:
        j.pop("_listing_id", None)
    return out
