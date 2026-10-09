"""Computrabajo: listing con badges (salario, fecha) + ficha."""
import re, time, urllib.request, urllib.parse
from html import unescape as _u
from datetime import datetime, timezone
from .linkedin import fetch
from ..channel import normalize_date
from ..domain.fechas import resolver_fecha

def jobs(queries, found_by_prefix="", on_query=None, max_pages=1):
    """Pagina con ?p=N hasta max_pages; corta si la página no trae ofertas nuevas."""
    out = []
    now = datetime.now(timezone.utc)
    for q in queries:
        vistos: set[str] = set()
        for pag in range(1, max_pages + 1):
            if on_query:
                try:
                    on_query(q, pag)
                except Exception:
                    pass
            base = f"https://www.computrabajo.cl/empleos-de-{q}"
            html_ = fetch(base if pag == 1 else f"{base}?p={pag}", fuente="computrabajo")
            if not html_:
                break
            n_antes = len(vistos)
            out += _parse_cards(html_, q, found_by_prefix, now, vistos)
            if len(vistos) == n_antes:
                break
            time.sleep(2)
    return out


def _parse_cards(html_, q, found_by_prefix, now, vistos):
    out = []
    for card in re.split(r'<article class="box_offer', html_)[1:]:
        link = re.search(r'href="(/ofertas-de-trabajo/[^"]+)"', card)
        if not link: continue
        path = link.group(1)
        slug = path.split("/ofertas-de-trabajo/")[-1].split("#")[0]
        slug = re.sub(r"^oferta-de-trabajo-de-", "", slug)
        title = slug.rsplit("-en-", 1)[0].replace("-", " ").strip()[:150]
        loc_m = re.search(r"-en-([a-z0-9-]+)-[0-9A-F]{32}", slug)
        location = loc_m.group(1).replace("-", " ").title() if loc_m else ""
        if not title or path in vistos: continue
        vistos.add(path)
        sal_m = re.search(r'<span class="icon i_salary"></span>\s*([^<]+)<', card)
        salary = _u(sal_m.group(1)).strip()[:40] if sal_m else ""
        # badge de empresa verificada (v2): señal de confianza del aviso
        verificada = 1 if 'i_verificada' in card else 0
        # modalidad: chip "Remoto"/"Híbrido" en la card (v2)
        modality = ""
        mmod = re.search(r'>\s*(Remoto|Híbrido|Hibrido|Presencial|Teletrabajo)\s*<', card)
        if mmod:
            modality = mmod.group(1).strip().lower()
            if modality == "teletrabajo":
                modality = "remoto"
        # F6: delega el parseo relativo a channel.normalize_date (única fuente
        # de verdad — entiende minutos/horas/días/semanas/meses/Hoy/Ayer)
        # fecha exacta = captura − offset; se conserva el texto original y la precisión
        # (date_posted_raw / date_precision) para mostrar "≈" en fechas gruesas
        hace = re.search(r'Hace\s+(?:\d+|un[ao]?|unos?|m[aá]s de\s+\d+)\s*\w+|\bHoy\b|\bAyer\b', card)
        date = normalize_date(hace.group(0), now) if hace else ""
        date_raw = hace.group(0) if hace else ""
        fb = f"{found_by_prefix}{q}"
        out.append({"title": title, "company": "", "location": location, "date": date,
                    "salary": salary, "url": "https://www.computrabajo.cl" + path,
                    "source": f"computrabajo:{q}", "found_by": fb,
                    "date_raw": date_raw, "date_precision": resolver_fecha(date_raw, now)[1],
                    "modality": modality,
                    "_cb_verificada": verificada})
    return out
