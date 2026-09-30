"""LinkedIn guest API (sin login)."""
import re, time, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

from ..logging_setup import get_logger

log = get_logger(__name__)

def fetch(url, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries - 1:
                log.warning("fetch falló %s: %s", url, e)
                return ""
            time.sleep(3)
    return ""

def clean(s): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).replace("&amp;","&").strip()

def parse_cards(html, source):
    jobs = []
    for card in re.split(r'<div class="base-card', html)[1:]:
        t = re.search(r'base-search-card__title">\s*(.*?)\s*</h3>', card, re.S)
        if not t: continue
        link = re.search(r'href="(https://[^"]+/jobs/view/[^"]+)"', card)
        date = re.search(r'datetime="([^"]+)"', card)
        comp = re.search(r'base-search-card__subtitle">\s*(.*?)\s*</h4>', card, re.S)
        loc = re.search(r'job-search-card__location">\s*(.*?)\s*</span>', card, re.S)
        jobs.append({"title": clean(t.group(1))[:150],
                     "company": clean(comp.group(1)) if comp else "",
                     "location": clean(loc.group(1)) if loc else "",
                     "date": date.group(1) if date else "",
                     "url": link.group(1).split("?")[0] if link else "",
                     "source": source})
    return jobs

def fetch_jobs(queries, found_by_prefix="", on_query=None, max_pages=3):
    """LinkedIn guest API. Pagina hasta max_pages (start=25*i — verificado: sin
    solapamiento entre páginas, ~10 ofertas nuevas por página)."""
    out = []
    for q in queries:
        if on_query:
            try:
                on_query(q, 1)
            except Exception:
                pass
        fb = f"{found_by_prefix}{q}"
        seen = set()
        for pag in range(max_pages):
            url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
                   + urllib.parse.urlencode({"keywords": q, "location": "Chile",
                                             "start": pag * 25, "f_TPR": "r604800"}))
            cards = parse_cards(fetch(url), f"linkedin:{q}")
            nuevos = [j for j in cards if j["url"] and j["url"] not in seen]
            if not nuevos:
                break
            for j in nuevos:
                seen.add(j["url"])
                j["found_by"] = fb
                out.append(j)
            if len(cards) < 10:      # última página
                break
            time.sleep(2)
        time.sleep(2)
    return out


def fetch_description(url: str) -> str:
    """Descripción completa de una oferta LinkedIn vía endpoint guest de detalle.

    La ficha normal (/jobs/view/...) está auth-walled para requests anónimos
    (fetch_page → blocked), pero jobs/api/jobPosting/<id> responde 200 con el
    HTML de la ficha guest, que trae la desc en div.show-more-less-html__markup.
    """
    m = re.search(r"-(\d{8,})(?:\?|$)", url) or re.search(r"(\d{8,})", url)
    if not m:
        return ""
    html = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
    if not html:
        return ""
    md = re.search(r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', html, re.S)
    if not md:
        return ""
    return clean(md.group(1))[:4000]
