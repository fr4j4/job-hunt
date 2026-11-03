"""We Work Remotely — RSS de programación (sin auth, sin búsqueda: es el feed completo).

https://weworkremotely.com/categories/remote-programming-jobs.rss
<item>: title "Empresa: Cargo", region ("Anywhere in the World", "USA Only"…), pubDate RFC-822,
link, description (HTML). Solo se conservan las regiones elegibles desde Chile."""
import xml.etree.ElementTree as ET
from datetime import datetime

from ..domain.texto import MAX_DESC
from ..logging_setup import get_logger
from . import _comun, errores

log = get_logger(__name__)
_URL = "https://weworkremotely.com/categories/remote-programming-jobs.rss"


def jobs(queries: list[str] | None = None, found_by_prefix: str = "", on_query=None) -> list[dict]:
    """`queries` se ignora (feed completo); se acepta por uniformidad con las otras fuentes."""
    if on_query:
        try:
            on_query("rss", 1)
        except Exception:
            pass
    raw = _comun.get("weworkremotely", _URL, as_text=True)
    if not raw:
        return []
    try:
        items = list(ET.fromstring(raw).iter("item"))
    except ET.ParseError as e:
        log.warning("weworkremotely XML inválido: %s", e)
        errores.registrar("weworkremotely")
        return []
    out = []
    for it in items:
        region = it.findtext("region") or ""
        url = it.findtext("link") or ""
        if not _comun.ELEGIBLE_CL.search(region) or not url:
            continue
        empresa, sep, cargo = (it.findtext("title") or "").partition(": ")
        try:
            fecha = datetime.strptime(it.findtext("pubDate") or "",
                                      "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            fecha = ""
        out.append({
            "title": _comun.limpiar(cargo if sep else empresa)[:150],
            "company": empresa if sep else "",
            "location": f"Remoto ({region})"[:80], "date": fecha, "url": url,
            "source": "weworkremotely:programming", "found_by": f"{found_by_prefix}programming",
            "modality": "remoto", "salary": "",
            "_desc": _comun.limpiar(it.findtext("description"))[:MAX_DESC],
            "description_source": "weworkremotely-rss",
        })
    return out
