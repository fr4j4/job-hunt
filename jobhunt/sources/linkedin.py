"""LinkedIn guest API (sin login)."""
import re, time, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

def fetch(url, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries - 1:
                print(f"WARN fetch {url}: {e}", file=__import__("sys").stderr)
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

def fetch_jobs(queries, found_by_prefix=""):
    out = []
    for q in queries:
        url = ("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
               + urllib.parse.urlencode({"keywords": q, "location": "Chile", "start": 0, "f_TPR": "r604800"}))
        fb = f"{found_by_prefix}{q}"
        for j in parse_cards(fetch(url), f"linkedin:{q}"):
            j["found_by"] = fb
            out.append(j)
        time.sleep(2)
    return out
