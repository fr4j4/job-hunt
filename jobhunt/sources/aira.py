"""Fuentes AIRA — feeds JSON públicos de empleadores (portal airavirtual).

Cada feed es el catálogo completo de ofertas de una empresa, refrescado por
ellos mismos (updated_at / ETag). Sin scraping: 1 GET por feed.

Feeds = lista configurable en .env (AIRA_FEEDS). El gate de relevancia
(relevance.py) decide qué ofertas entran al pool — así el bot solo indexa
lo que calza con las queries/perfil configurados, sea tech u otro sector.

Formatos conocidos:
  A: {updated_at, offers[], companies{}}
  B: {data: {offers[]}} o {data: [...]} (tottus/entel/cencosud genérico)
"""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

from ..logging_setup import get_logger

log = get_logger(__name__)

_H = {"User-Agent": "curl/8.5.0"}
_BASE = "https://gcs-storage.airavirtual.com/public/feeds"

def _clean(s) -> str:
    if isinstance(s, list):
        s = " ".join(str(x) for x in s)
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _extract_offers(d: dict) -> list[dict]:
    """Maneja los 2 formatos de feed AIRA."""
    if isinstance(d, dict) and isinstance(d.get("offers"), list):
        return d["offers"]                                   # formato A
    data = d.get("data") if isinstance(d, dict) else None
    if isinstance(data, dict) and isinstance(data.get("offers"), list):
        return data["offers"]                                # formato B dict
    if isinstance(data, list) and data:
        return data                                          # formato B lista
    return []


def _parse_offer(a: dict, feed: str) -> dict | None:
    """Normaliza un offer del feed (formato A o B) al job estándar."""
    name = _clean(a.get("name") or a.get("title"))
    if not name:
        return None
    # company: owner text (formato B) o companies[owner_company] (formato A)
    company = _clean(a.get("owner") or a.get("company") or "")
    city = _clean(a.get("city") or "")
    city = re.sub(r"^chile##[a-z]+##", "", city).replace("##", ", ") if city else ""
    region = _clean((a.get("region") or "").replace("##", ", ").replace("chile##", "").title())
    location = city or region
    # modality: remote_work estructurado
    rw = str(a.get("remote_work") or a.get("remoteType") or "").upper()
    modality = ("remoto" if "REMOTE" in rw and "NO_REMOTE" not in rw
                else "híbrido" if "HYBRID" in rw.upper()
                else "presencial" if "NO_REMOTE" in rw or "ONSITE" in rw.upper()
                else "")
    # fecha: publication_days o updated_at del feed como fallback
    fecha = ""
    pdays = a.get("publication_days")
    if isinstance(pdays := pdays, int) and pdays >= 0:
        fecha = (datetime.now(timezone.utc) - timedelta(days=pdays)).date().isoformat()
    url = a.get("link") or ""
    if not url and a.get("id"):
        url = f"https://login.airavirtual.com/postula/{a['id']}"
    return {
        "title": name[:150],
        "company": company or "Confidencial",
        "location": location[:120],
        "date": fecha,
        "url": url,
        "source": f"aira:{feed}",
        "found_by": f"{feed}",
        "salary": "",
        "modality": modality,
        "_desc": _clean(a.get("description") or a.get("snippet") or "")[:2000],
        "description_source": "aira-feed",
        "_aira_area": _clean(a.get("area") or a.get("area_text") or ""),
    }


def jobs(feeds: list[str], found_by_prefix: str = "", on_feed=None) -> list[dict]:
    """Descarga y parsea los feeds AIRA configurados. Sin filtro de relevancia
    acá — el gate lo aplica relevance.py en el barrido (una sola fuente de verdad)."""
    out: dict[str, dict] = {}
    s = requests.Session()
    s.headers.update({"User-Agent": "curl/8.5.0"})
    for feed in feeds:
        fname = feed if feed.startswith("aira_") else f"aira_{feed}"
        try:
            r = s.get(f"https://gcs-storage.airavirtual.com/public/feeds/{fname}.json", timeout=20)
            if r.status_code != 200:
                log.warning("aira %s: HTTP %s", fname, r.status_code)
                continue
            d = r.json()
        except Exception as e:
            log.warning("aira %s falló: %s", fname, e)
            continue
        offers = _extract_offers(d)
        if on_feed:
            try:
                on_feed(fname, len(offers))
            except Exception:
                pass
        for a in offers:
            j = _parse_offer(a, feed)
            if not j:
                continue
            uid = f"{fname}:{a.get('id')}"
            out[uid] = {**j, "source": f"aira:{feed}", "found_by": f"{found_by_prefix}{feed}"}
        time.sleep(1)
    return list(out.values())