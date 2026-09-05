"""Computrabajo: listing con badges (salario, fecha) + ficha."""
import re, time, urllib.request, urllib.parse
from html import unescape as _u
from datetime import datetime, timezone
from .linkedin import fetch
from ..channel import normalize_date

def jobs(queries, found_by_prefix="", on_query=None):
    out = []
    now = datetime.now(timezone.utc)
    for q in queries:
        if on_query:
            try:
                on_query(q, 1)
            except Exception:
                pass
        html_ = fetch(f"https://www.computrabajo.cl/empleos-de-{q}")
        if not html_: continue
        for card in re.split(r'<article class="box_offer', html_)[1:]:
            link = re.search(r'href="(/ofertas-de-trabajo/[^"]+)"', card)
            if not link: continue
            path = link.group(1)
            slug = path.split("/ofertas-de-trabajo/")[-1].split("#")[0]
            slug = re.sub(r"^oferta-de-trabajo-de-", "", slug)
            title = slug.rsplit("-en-", 1)[0].replace("-", " ").strip()[:150]
            loc_m = re.search(r"-en-([a-z0-9-]+)-[0-9A-F]{32}", slug)
            location = loc_m.group(1).replace("-", " ").title() if loc_m else ""
            if not title: continue
            sal_m = re.search(r'<span class="icon i_salary"></span>\s*([^<]+)<', card)
            salary = _u(sal_m.group(1)).strip()[:40] if sal_m else ""
            # F6: delega el parseo relativo a channel.normalize_date (única fuente
            # de verdad — entiende minutos/horas/días/semanas/meses/Hoy/Ayer)
            hace = re.search(r'Hace\s+[\d\s]+\s*\w+|Hoy|Ayer', card)
            date = normalize_date(hace.group(0), now) if hace else ""
            fb = f"{found_by_prefix}{q}"
            out.append({"title": title, "company": "", "location": location, "date": date,
                        "salary": salary, "url": "https://www.computrabajo.cl" + path,
                        "source": f"computrabajo:{q}", "found_by": fb})
        time.sleep(2)
    return out
