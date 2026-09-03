"""Laborum.cl — API searchV2 (POST) como fuente de ofertas.

Hallazgos de ingeniería inversa (bundle main.6c710da8.js + pruebas):
- endpoint: POST https://www.laborum.cl/api/avisos/searchV2?pageSize=N&page=P&sort=RELEVANTES
- header obligatorio: x-site-id: BMCL (grupo Bumeran: BMAR/BMEC/BMPE/BMMX/BMPA/BMVE/ZJAR)
- Cloudflare: requiere cookies __cf_bm de un GET previo a la home (sesión completa)
- filtros: [{"id": "<tipo>", "value": "<idSemantico>"}] — modalidad_trabajo remoto/hibrido/
  presencial, provincia "chile|region-XIV", localidad, tipo_trabajo full-time, nivel_laboral
  senior/semi-sr/junior, area tecnologia-sistemas-y-telecomunicaciones, dias_fecha_publicacion
- response: {total, content: [{id, titulo, detalle, empresa, fechaPublicacion,
  modalidadTrabajo, tipoTrabajo, localizacion, cantidadVacantes, ...}]}
- link público: https://www.laborum.cl/empleos/{id}
"""
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape as _u

import requests

from ..logging_setup import get_logger

log = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/131.0.0.0 Safari/537.36",
       "Accept-Language": "es-CL,es;q=0.9"}
_HEADERS = {"x-site-id": "BMCL", "Origin": "https://www.laborum.cl",
            "Referer": "https://www.laborum.cl/empleos/",
            "Content-Type": "application/json"}

_session: requests.Session | None = None
_session_ts: float = 0.0


def _get_session() -> requests.Session:
    """Sesión con cookies Cloudflare frescas (visita a la home primero)."""
    global _session, _session_ts
    now = time.time()
    if _session is None or now - _session_ts > 1800:
        s = requests.Session()
        s.headers.update(_UA)
        s.get("https://www.laborum.cl/", timeout=25)
        time.sleep(1)
        _session = s
        _session_ts = now
    return _session


def _search(query: str, filtros: list[dict] | None = None, page: int = 0,
            size: int = 20, retries: int = 2) -> dict:
    s = _get_session()
    for attempt in range(retries + 1):
        try:
            r = s.post("https://www.laborum.cl/api/avisos/searchV2",
                       params={"pageSize": size, "page": page, "sort": "RELEVANTES"},
                       json={"filtros": filtros or [], "query": query, "internacional": False},
                       headers=_HEADERS | {"Referer": "https://www.laborum.cl/empleos/"},
                       timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (403, 503):
                # challenge de Cloudflare → renovar sesión y reintentar
                global _session_ts
                _session_ts = 0
                s = _get_session()
                time.sleep(2 * (attempt + 1))
                continue
            log.warning("laborum search %s: HTTP %s", query[:30], r.status_code)
            time.sleep(2)
        except Exception as e:
            log.warning("laborum fetch falló (%s): %s", query[:30], e)
            time.sleep(2 * (attempt + 1))
    return {}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def jobs(queries: list[str], found_by_prefix: str = "") -> list[dict]:
    """Listado de ofertas por query. Combina modalidades (remoto/híbrido/presencial)
    para no perder ofertas en el corte de 20 por página."""
    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    modalidades = [None, {"id": "modalidad_trabajo", "value": "remoto"},
                   {"id": "modalidad_trabajo", "value": "hibrido"}]
    for q in queries:
        for filtro in modalidades:
            d = _search(q, filtros=[filtro] if filtro else None)
            if not d.get("content"):
                continue
            fb = f"{found_by_prefix}{q}" + (":remoto" if filtro else "")
            for a in d["content"]:
                aid = a.get("id")
                if not aid or aid in out:
                    continue
                # fecha: "02-09-2026" (DD-MM-YYYY)
                fecha = ""
                try:
                    fecha = datetime.strptime(a.get("fechaPublicacion", ""), "%d-%m-%Y").date().isoformat()
                except Exception:
                    fecha = (now - timedelta(days=1)).date().isoformat()
                # desc: el detalle del listado es resumido; descripción completa la trae el Anillo A
                desc = _clean(a.get("detalle") or "")[:2000]
                out[aid] = {
                    "title": _clean(a.get("titulo") or "")[:150],
                    "company": (a.get("empresa") or "").strip(),
                    "location": (a.get("localizacion") or "").strip(),
                    "date": fecha,
                    "url": f"https://www.laborum.cl/empleos/{aid}",
                    "source": f"laborum:{q}",
                    "found_by": fb,
                    "modality": (a.get("modalidadTrabajo") or "").strip().lower() or "",
                    "salary": "",          # el API no expone salario en el listado
                    "_desc": desc,
                    "description_source": "laborum-api",
                }
            if len(out) > 400:
                break
        time.sleep(3)
    return list(out.values())[:400]


def fetch_detail(aviso_id: str | int) -> dict:
    """Ficha completa de un aviso (para el Anillo A). Retorna info estilo extract_structured."""
    info: dict = {"description": "", "date_posted": "", "valid_through": "",
                  "employment_type": "", "years_official": None, "remote_official": 0,
                  "industry": "", "education": "", "applicant_region": "",
                  "company": "", "modality_badge": "", "salary": "",
                  "contrato": "", "jornada": "", "techs_desc": []}
    d = _search("", page=0, size=1)  # placeholder: la ficha individual es otro endpoint
    return info