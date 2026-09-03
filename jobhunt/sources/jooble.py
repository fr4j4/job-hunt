"""Jooble — fuente vía API REST oficial (regional cl.jooble.org).

Requiere JOOBLE_API_KEY en .env (gratuita: formulario en cl.jooble.org/api/about,
llega por email en 1-2 días hábiles; 500 requests lifetime por key).
Endpoint: POST https://cl.jooble.org/api/{api_key}
Body: {"keywords": str, "location": str, "page": 1..N}
Response: {"totalCount": N, "jobs": [{title, location, snippet, salary, source, link, company, updated, id}]}
"""
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape as _u

import requests

from ..logging_setup import get_logger

log = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
       "Content-Type": "application/json"}


def jobs(queries: list[str], api_key: str, found_by_prefix: str = "",
         location: str = "Chile") -> list[dict]:
    """Busca ofertas en Jooble Chile vía API oficial. Dedup por id."""
    if not api_key:
        log.warning("jooble: sin JOOBLE_API_KEY — fuente deshabilitada")
        return []
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    for q in queries:
        page = 1
        while page <= 2:                     # 2 páginas × 20 = 40 por query (ahorra quota)
            try:
                r = requests.post(f"https://cl.jooble.org/api/{api_key}",
                                  json={"keywords": q, "location": location, "page": page},
                                  headers=_UA, timeout=25)
                if r.status_code != 200:
                    log.warning("jooble %s p%s: HTTP %s — %s", q[:25], page, r.status_code,
                                r.text[:80])
                    break
                d = r.json()
            except Exception as e:
                log.warning("jooble fetch falló (%s): %s", q[:30], e)
                break
            for a in d.get("jobs") or []:
                jid = str(a.get("id") or "")
                if not jid or jid in out:
                    continue
                # updated: "2026-09-01T12:33:02.0387335+00:00" o similar
                fecha = ""
                try:
                    fecha = (a.get("updated") or "")[:19].replace("T", " ")
                    fecha = datetime.fromisoformat(fecha).date().isoformat()
                except Exception:
                    fecha = now.date().isoformat()
                desc = re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", a.get("snippet") or ""))).strip()[:2000]
                out[jid] = {
                    "title": _clean(a.get("title") or "")[:150],
                    "company": _clean(a.get("company") or ""),
                    "location": _clean(a.get("location") or ""),
                    "date": fecha,
                    "url": a.get("link") or "",
                    "source": f"jooble:{q}",
                    "found_by": f"{found_by_prefix}{q}",
                    "salary": _clean(a.get("salary") or ""),
                    "modality": "",
                    "_desc": desc,
                    "description_source": "jooble-api",
                }
            total = d.get("totalCount") or 0
            if page * 20 >= total or not d.get("jobs"):
                break
            page += 1
            time.sleep(2)
        time.sleep(3)
    return list(out.values())


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _u(s or "")).strip()