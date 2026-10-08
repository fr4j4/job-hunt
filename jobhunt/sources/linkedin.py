"""LinkedIn guest API (sin login)."""
import re, time, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

from ..logging_setup import get_logger
from . import errores

log = get_logger(__name__)

def fetch(url, retries=2, fuente="linkedin"):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries - 1:
                log.warning("fetch falló %s: %s", url, e)
                errores.registrar(fuente)
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

def fetch_jobs(queries, found_by_prefix="", on_query=None, max_pages=8,
               max_age_days=7):
    """LinkedIn guest API con ventana ancha + corte por antigüedad (v3).

    Estrategia medida en vivo (30-09-2026, /tmp/test_li_orden*.py):
    - f_TPR=r2592000 (30d) para que LinkedIn no corte a 7d server-side;
      el corte fino lo hace acá (max_age_days).
    - Las páginas NO vienen ordenadas por fecha (orden por relevancia): una
      misma página puede mezclar ofertas ≤7d con >7d. Por eso el corte es por
      PÁGINA (≥60% viejas → la cola ya no vale la pena), no por oferta.
    - start=0 puede venir vacío por rate-limit blando → 1 reintento.
    Sin solapamiento entre páginas (verificado: 0 duplicados en 7 páginas).
    """
    out = []
    corte = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).date()
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
                                             "start": pag * 25, "f_TPR": "r2592000"}))
            cards = parse_cards(fetch(url), f"linkedin:{q}")
            if not cards and pag == 0:
                # rate-limit blando: la primera puede venir vacía; 1 reintento
                time.sleep(4)
                cards = parse_cards(fetch(url), f"linkedin:{q}")
            nuevos = [j for j in cards if j["url"] and j["url"] not in seen]
            if not nuevos:
                break
            viejas = 0
            for j in nuevos:
                seen.add(j["url"])
                j["found_by"] = fb
                try:
                    if j.get("date") and datetime.fromisoformat(j["date"]).date() <= corte:
                        viejas += 1
                        continue      # fuera de la ventana: la query pide 30d pero se queda con max_age_days
                except (ValueError, TypeError):
                    pass
                out.append(j)
            # corte por página: si la mayoría ya es vieja, la cola no aporta
            if nuevos and viejas / len(nuevos) >= 0.6:
                break
            if len(cards) < 10:      # última página
                break
            time.sleep(2)
        time.sleep(2)
    return out


def fetch_description(url: str) -> dict:
    """Ficha guest de LinkedIn: descripción + metadatos estructurados.

    La ficha normal (/jobs/view/...) está auth-walled para requests anónimos
    (fetch_page → blocked), pero jobs/api/jobPosting/<id> responde 200 con el
    HTML de la ficha guest. De ahí se extraen, además de la descripción
    (div.show-more-less-html__markup), los criterios oficiales que LinkedIn
    muestra en 'Requisitos' (verificado 30-09-2026, offsets regex testeados):
    - seniority_oficial  (h3 'Seniority level')
    - employment_type    (h3 'Employment type')
    - industry           (h3 'Industries')
    - applicants_hint    ('Be among the first 25 applicants' / 'X applicants')
    """
    m = re.search(r"-(\d{8,})(?:\?|$)", url) or re.search(r"(\d{8,})", url)
    if not m:
        return {}
    html = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m.group(1)}")
    if not html:
        return {}
    out: dict = {}
    md = re.search(r'<div class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', html, re.S)
    if md:
        out["description"] = clean(md.group(1))[:4000]

    def _criteria(label: str) -> str:
        mc = re.search(label + r"\s*</h3>\s*<span[^>]*>\s*([^<]+)", html)
        return mc.group(1).strip() if mc else ""

    seniority = _criteria("Seniority level")
    if seniority and seniority.lower() != "not applicable":
        out["seniority_oficial"] = seniority
    emp = _criteria("Employment type")
    if emp:
        out["employment_type"] = emp
    ind = _criteria("Industries")
    if ind:
        out["industry"] = ind
    ma = re.search(r"Be among the first (\d+) applicants", html)
    if ma:
        out["applicants_hint"] = f"first {ma.group(1)}"
    else:
        ma = re.search(r"(\d[\d.,]*)\s*applicants", html)
        if ma:
            out["applicants_hint"] = ma.group(1).replace(".", "").replace(",", "")
    return out
