"""Utilidades compartidas por las fuentes de APIs abiertas (getonboard, himalayas,
remotive, weworkremotely): GET con reintentos que registra el error por fuente, limpieza
de HTML y filtros de elegibilidad para un candidato en Chile."""
import re
import time
from datetime import datetime, timezone
from html import unescape as _u

import requests

from ..logging_setup import get_logger
from . import errores

log = get_logger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36", "Accept-Language": "es-CL,es;q=0.9"}

# ubicación elegible desde Chile: global, LatAm/Américas o Chile explícito
ELEGIBLE_CL = re.compile(r"worldwide|anywhere|latam|latin|americas|south america|chile", re.I)


def get(fuente: str, url: str, params: dict | None = None, retries: int = 2,
        as_text: bool = False):
    """GET → JSON (o texto). Devuelve {} / '' tras agotar reintentos y registra el error."""
    for intento in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=25)
            if r.status_code == 200:
                return r.text if as_text else r.json()
            log.warning("%s HTTP %s (%s)", fuente, r.status_code, url[:70])
        except Exception as e:
            log.warning("%s fetch falló: %s", fuente, e)
        errores.registrar(fuente)
        time.sleep(2 * (intento + 1))
    return "" if as_text else {}


def limpiar(s) -> str:
    return re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", str(s or "")))).strip()


def fecha_epoch(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return ""
