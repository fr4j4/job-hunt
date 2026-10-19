"""Trabajando.com (trabajando.cl) — API JSON del sitio (Nuxt), sin auth.

Listado: GET https://www.trabajando.cl/api/searchjob?palabraClave=<q>&pagina=<n>&orden=RANKING&tipoOrden=DESC
→ {cantidadPaginas, ofertas:[{idOferta, nombreCargo, nombreEmpresa, ubicacion, fechaPublicacion,
descripcionOferta (resumen), nombreJornada}]}, 15 por página.
Ficha: GET https://www.trabajando.cl/api/ofertas/<id> → descripcionOferta y requisitosMinimos (HTML),
nombreJornada ("Mixta (Teletrabajo + Presencial)"), sueldo/mostrarSueldo, ubicacion.{nombreRegion,
nombreComuna}, aniosExperiencia. Se pide solo para las primeras `max_detalles` ofertas del barrido.
Enlace público: https://www.trabajando.cl/trabajo-empleo/<id>."""
import time

from ..domain.texto import MAX_DESC
from . import _comun

_API = "https://www.trabajando.cl/api"
_PUBLICO = "https://www.trabajando.cl/trabajo-empleo/"


def _modalidad(jornada: str) -> str:
    j = (jornada or "").lower()
    if "mixta" in j or "híbrid" in j or "hibrid" in j:
        return "híbrido"
    if "teletrabajo" in j or "remot" in j:
        return "remoto"
    return ""


def jobs(queries: list[str], found_by_prefix: str = "", on_query=None, max_pages: int = 3,
         max_detalles: int = 60) -> list[dict]:
    out: dict[int, dict] = {}
    for q in queries:
        for pag in range(1, max(1, max_pages) + 1):
            if on_query:
                try:
                    on_query(q, pag)
                except Exception:
                    pass
            d = _comun.get("trabajando", f"{_API}/searchjob",
                           {"palabraClave": q, "pagina": pag, "orden": "RANKING", "tipoOrden": "DESC"})
            for o in d.get("ofertas") or []:
                oid, titulo = o.get("idOferta"), _comun.limpiar(o.get("nombreCargo"))[:150]
                if not oid or not titulo or oid in out:
                    continue
                out[oid] = {
                    "title": titulo, "company": o.get("nombreEmpresa") or "",
                    "location": o.get("ubicacion") or "",
                    "date": (o.get("fechaPublicacion") or "")[:10],
                    "url": f"{_PUBLICO}{oid}", "source": f"trabajando:{q}",
                    "found_by": f"{found_by_prefix}{q}", "modality": _modalidad(o.get("nombreJornada")),
                    "salary": "", "_desc": _comun.limpiar(o.get("descripcionOferta"))[:MAX_DESC],
                    "description_source": "trabajando-serp",
                    "_tb_id": oid,
                }
            if pag >= int(d.get("cantidadPaginas") or 1):
                break
            time.sleep(1)
        time.sleep(1)
    _completar_fichas(list(out.values()), max_detalles)
    for j in out.values():
        j.pop("_tb_id", None)
    return list(out.values())


def _completar_fichas(ofertas: list[dict], max_detalles: int) -> None:
    for j in ofertas[:max_detalles]:
        f = _comun.get("trabajando", f"{_API}/ofertas/{j['_tb_id']}")
        if not f or "idOferta" not in f:
            continue
        desc = _comun.limpiar(f"{f.get('descripcionOferta') or ''} {f.get('requisitosMinimos') or ''}")
        if desc:
            j["_desc"] = desc[:MAX_DESC]
            j["description_source"] = "trabajando-api"
        jornada = f.get("nombreJornada")
        mod = _modalidad(jornada) or _comun.modalidad_en_texto(desc)
        if mod:
            j["modality"] = mod
        if f.get("mostrarSueldo") and (f.get("sueldo") or 0) > 0 and "peso" in (f.get("nombreMoneda") or "").lower():
            j["salary"] = f"CLP {int(f['sueldo'])}"
        if jornada:
            j["employment_type"] = jornada
        ub = f.get("ubicacion") or {}
        loc = ", ".join(x for x in (ub.get("nombreComuna"), ub.get("nombreRegion")) if x)
        if loc:
            j["location"] = loc
        if f.get("aniosExperiencia") and f.get("nombreOperadorExperiencia"):
            j["_desc"] = (j["_desc"] + f" Experiencia {f['nombreOperadorExperiencia'].lower()} "
                          f"{f['aniosExperiencia']} años.")[:MAX_DESC]
        time.sleep(0.5)
