"""Trabajando.com (trabajando.cl) — API JSON del sitio (Nuxt), sin auth.

Listado: GET https://www.trabajando.cl/api/searchjob?palabraClave=<q>&pagina=<n>&orden=RANKING&tipoOrden=DESC
→ {cantidadPaginas, ofertas:[{idOferta, nombreCargo, nombreEmpresa, ubicacion, fechaPublicacion,
descripcionOferta (resumen), nombreJornada}]}, 15 por página.
Ficha: GET https://www.trabajando.cl/api/ofertas/<id> → descripcionOferta y requisitosMinimos (HTML),
nombreJornada ("Mixta (Teletrabajo + Presencial)"), sueldo/mostrarSueldo, ubicacion.{nombreRegion,
nombreComuna}, aniosExperiencia, slug. Se pide para todas las ofertas del barrido (o las primeras `max_detalles`).
Enlace público: https://www.trabajando.cl/trabajo/<id>-<slug>. El listado NO trae enlace ni slug; la ficha
trae `slug` ("6143588-analista-senior-fullstack"), que es el canonical de la página. Por eso se pide la
ficha de TODAS las ofertas: el enlace sale siempre del sitio. Solo si la ficha falla se arma id + título
(el sitio resuelve por id: /trabajo/6143588-x muestra la misma oferta, verificado 03-11-2026).
/trabajo-empleo/<id> NO sirve: devuelve una página vacía (noindex, sin datos de la oferta)."""
import re
import time
import unicodedata

from ..domain.texto import MAX_DESC
from ..logging_setup import get_logger
from . import _comun

log = get_logger(__name__)

_API = "https://www.trabajando.cl/api"
_PUBLICO = "https://www.trabajando.cl/trabajo/"
# enlaces de la oferta: el actual (/trabajo/<id>-<slug>) y el antiguo roto (/trabajo-empleo/<id>)
RE_URL_OFERTA = re.compile(r"trabajando\.cl/trabajo(?:-empleo)?/(\d+)")


def _slug_titulo(titulo: str) -> str:
    """'Ingeniero/a Junior de Soluciones IA' → 'ingeniero-a-junior-de-soluciones-ia' (mismo patrón que
    los slugs del sitio; es solo cosmético: el sitio resuelve por id)."""
    t = unicodedata.normalize("NFKD", titulo or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def url_oferta(oid, titulo: str = "", slug: str = "") -> str:
    """Enlace público de la oferta. `slug` es el de la ficha (canonical); sin él (ficha caída), id + título."""
    slug = (slug or "").strip().strip("/")
    if slug and slug.split("-", 1)[0] == str(oid):
        return f"{_PUBLICO}{slug}"
    s = _slug_titulo(titulo)
    return f"{_PUBLICO}{oid}-{s}" if s else f"{_PUBLICO}{oid}"


def id_de_url(url: str) -> str:
    m = RE_URL_OFERTA.search(url or "")
    return m.group(1) if m else ""


def _modalidad(jornada: str) -> str:
    j = (jornada or "").lower()
    if "mixta" in j or "híbrid" in j or "hibrid" in j:
        return "híbrido"
    if "teletrabajo" in j or "remot" in j:
        return "remoto"
    return ""


def jobs(queries: list[str], found_by_prefix: str = "", on_query=None, max_pages: int = 3,
         max_detalles: int | None = None) -> list[dict]:
    """max_detalles=None → ficha de todas (el enlace real solo viene en la ficha)."""
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
                    "url": url_oferta(oid, titulo), "source": f"trabajando:{q}",
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
    sin_slug = sum(1 for j in out.values() if not j.pop("_tb_slug", False))
    if sin_slug:
        log.warning("trabajando: %d ofertas sin ficha → enlace armado con id + título", sin_slug)
    for j in out.values():
        j.pop("_tb_id", None)
    return list(out.values())


def _ficha_a_info(f: dict) -> dict:
    """Ficha de /api/ofertas/<id> → campos que consume el enriquecimiento."""
    desc = _comun.limpiar(f"{f.get('descripcionOferta') or ''} {f.get('requisitosMinimos') or ''}")
    if f.get("aniosExperiencia") and f.get("nombreOperadorExperiencia"):
        desc = (desc + f" Experiencia {f['nombreOperadorExperiencia'].lower()} {f['aniosExperiencia']} años.")
    jornada = f.get("nombreJornada") or ""
    ub = f.get("ubicacion") or {}
    salary = ""
    if f.get("mostrarSueldo") and (f.get("sueldo") or 0) > 0 and "peso" in (f.get("nombreMoneda") or "").lower():
        salary = f"CLP {int(f['sueldo'])}"
    return {"description": desc[:MAX_DESC], "modality": _modalidad(jornada) or _comun.modalidad_en_texto(desc),
            "employment_type": jornada, "salary": salary,
            "location": ", ".join(x for x in (ub.get("nombreComuna"), ub.get("nombreRegion")) if x),
            "date_posted": f.get("fechaPublicacionFormatoIngles") or ""}


def fetch_ficha(url: str) -> dict:
    """Ficha para el enriquecimiento (enrich._fetch_ficha) a partir del enlace público."""
    oid = id_de_url(url)
    f = _comun.get("trabajando", f"{_API}/ofertas/{oid}") if oid else {}
    if not f or "idOferta" not in f:
        return {"_access": "blocked", "error": "trabajando sin ficha"}
    i = _ficha_a_info(f)
    if not i["description"]:
        return {"_access": "blocked", "error": "ficha sin descripción"}
    return {"_access": "ok", "description": i["description"], "modality_badge": i["modality"],
            "employment_type": i["employment_type"], "salary": i["salary"], "location": i["location"],
            "date_posted": i["date_posted"]}


def _completar_fichas(ofertas: list[dict], max_detalles: int) -> None:
    for j in (ofertas if max_detalles is None else ofertas[:max_detalles]):
        f = _comun.get("trabajando", f"{_API}/ofertas/{j['_tb_id']}")
        if not f or "idOferta" not in f:
            continue
        if f.get("slug"):
            j["url"] = url_oferta(j["_tb_id"], j["title"], f["slug"])
            j["_tb_slug"] = True
        i = _ficha_a_info(f)
        if i["description"]:
            j["_desc"] = i["description"]
            j["description_source"] = "trabajando-api"
        if i["modality"]:
            j["modality"] = i["modality"]
        if i["salary"]:
            j["salary"] = i["salary"]
        if i["employment_type"]:
            j["employment_type"] = i["employment_type"]
        if i["location"]:
            j["location"] = i["location"]
        time.sleep(0.5)


def reparar_urls(conn, pausa: float = 0.5) -> dict:
    """Ofertas ya indexadas con el enlace antiguo /trabajo-empleo/<id>: pide la ficha y guarda el enlace
    real (slug del sitio). Si la ficha ya no existe (oferta cerrada), deja id + título, que el sitio
    resuelve por id; nunca deja el enlace roto. Idempotente."""
    filas = conn.execute("SELECT group_id, title, url FROM ofertas "
                         "WHERE url LIKE '%trabajando.cl/trabajo-empleo/%'").fetchall()
    res = {"total": len(filas), "slug_sitio": 0, "id_titulo": 0}
    for gid, titulo, url in filas:
        oid = id_de_url(url)
        if not oid:
            continue
        f = _comun.get("trabajando", f"{_API}/ofertas/{oid}", retries=1)
        slug = f.get("slug") if isinstance(f, dict) and str(f.get("idOferta")) == oid else ""
        conn.execute("UPDATE ofertas SET url=? WHERE group_id=?", (url_oferta(oid, titulo, slug or ""), gid))
        res["slug_sitio" if slug else "id_titulo"] += 1
        time.sleep(pausa)
    conn.commit()
    return res
