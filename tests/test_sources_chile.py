"""Chiletrabajos y Trabajando.com (HTTP simulado) + modalidad_en_texto."""
from jobhunt.sources import _comun, chiletrabajos, trabajando

_LISTADO = """<html><body>
<div class="job-item with-thumb destacado no-hover ">
  <h2 class="title overflow-hidden"><a href="https://www.chiletrabajos.cl/trabajo/dev-full-stack-senior-111" class="font-weight-bold">Desarrollador Full Stack Golang + .NET…</a></h2>
  <h3 class="meta">
      Synergie,
      <a href="https://www.chiletrabajos.cl/ciudad/santiago.html">Santiago</a>
  </h3>
  <h3 class="meta"><a href='x'><i class="far fa-calendar"></i> 14 de  Octubre de 2026</a></h3>
  <p class="description" style="x">Resumen corto del cargo&#8230; <a href="x" class="ver-mas-btn">Ver más</a></p>
</div>
<div class="job-item no-hover ">
  <h2 class="title overflow-hidden"><a href="https://www.chiletrabajos.cl/trabajo/otro-222" class="font-weight-bold">Otro</a></h2>
  <h3 class="meta"><a href='x'><i class="far fa-calendar"></i> 1 de Septiembre de 2026</a></h3>
</div>
</body></html>"""

_FICHA = """<html><head><title>Desarrollador Full Stack Golang + .NET Senior  - Santiago | Chiletrabajos</title></head><body>
<table><tr><td>Tipo</td><td><a href="x">Full-time</a></td></tr></table>
<div class="botones-desc d-block"><span>hace 5 dias</span></div>
<div><p class="mb-0">Buscamos dev.<br />
Modalidad preferentemente remota.<br />Python y SQL.</p></div></body></html>"""


def test_modalidad_en_texto_conservadora():
    assert _comun.modalidad_en_texto("Modalidad preferentemente remota.") == "remoto"
    assert _comun.modalidad_en_texto("100% remoto desde Chile") == "remoto"
    assert _comun.modalidad_en_texto("Esquema híbrido, teletrabajo 2 días") == "híbrido"
    assert _comun.modalidad_en_texto("Acceso remoto a servidores del cliente") == ""
    assert _comun.modalidad_en_texto("") == ""


def test_chiletrabajos_parsea_listado_y_ficha(monkeypatch):
    def fake_get(fuente, url, params=None, retries=2, as_text=False):
        return _FICHA if "/trabajo/" in url else _LISTADO
    monkeypatch.setattr(_comun, "get", fake_get)
    r = chiletrabajos.jobs(["full stack"], "perfil:", max_pages=1)
    assert [j["url"].rsplit("/", 1)[1] for j in r] == ["dev-full-stack-senior-111", "otro-222"]
    a = r[0]
    assert a["company"] == "Synergie" and a["location"] == "Santiago" and a["date"] == "2026-10-14"
    assert a["title"] == "Desarrollador Full Stack Golang + .NET Senior"    # título completo de la ficha
    assert a["modality"] == "remoto" and a["description_source"] == "chiletrabajos-html"
    assert "Python y SQL" in a["_desc"] and a["employment_type"] == "Full-time"
    assert a["found_by"] == "perfil:full stack" and a["source"] == "chiletrabajos:full stack"


def test_chiletrabajos_pagina_con_offset_y_corta_en_pagina_incompleta(monkeypatch):
    pedidas = []

    def fake_get(fuente, url, params=None, retries=2, as_text=False):
        pedidas.append(url)
        return _LISTADO if "/trabajo/" not in url else ""
    monkeypatch.setattr(_comun, "get", fake_get)
    chiletrabajos.jobs(["python"], max_pages=3, max_detalles=0)
    assert len([u for u in pedidas if "encuentra-un-empleo" in u]) == 1     # 2 < 30: última página


def test_trabajando_listado_y_ficha(monkeypatch):
    lista = {"cantidadPaginas": 1, "ofertas": [
        {"idOferta": 7, "nombreCargo": "Dev FullStack", "nombreEmpresa": "Acme",
         "ubicacion": "Providencia, RM", "fechaPublicacion": "2026-10-19 09:02",
         "descripcionOferta": "resumen...", "nombreJornada": "Jornada Completa"},
        {"idOferta": 8, "nombreCargo": "Operador", "nombreEmpresa": "X", "ubicacion": "Antofagasta",
         "fechaPublicacion": "2026-10-14 12:33", "descripcionOferta": "", "nombreJornada": "Mixta (Teletrabajo + Presencial)"}]}
    ficha = {"idOferta": 7, "descripcionOferta": "<p>Python y <b>Angular</b></p>",
             "requisitosMinimos": "<p>3 años</p>", "nombreJornada": "Teletrabajo",
             "mostrarSueldo": True, "sueldo": 2500000, "nombreMoneda": "Pesos Chilenos",
             "ubicacion": {"nombreComuna": "Providencia", "nombreRegion": "Metropolitana de Santiago"},
             "aniosExperiencia": 3, "nombreOperadorExperiencia": "Desde"}

    def fake_get(fuente, url, params=None, retries=2, as_text=False):
        return lista if url.endswith("/searchjob") else (ficha if url.endswith("/7") else {})
    monkeypatch.setattr(_comun, "get", fake_get)
    r = trabajando.jobs(["fullstack"], "perfil:")
    a, b = r
    assert a["url"] == "https://www.trabajando.cl/trabajo-empleo/7" and a["date"] == "2026-10-19"
    assert a["modality"] == "remoto" and a["salary"] == "CLP 2500000"
    assert a["location"] == "Providencia, Metropolitana de Santiago"
    assert a["description_source"] == "trabajando-api" and "Python y Angular" in a["_desc"]
    assert "Experiencia desde 3 años" in a["_desc"]
    assert b["modality"] == "híbrido" and b["description_source"] == "trabajando-serp"   # ficha vacía
    assert "_tb_id" not in a and "_tb_id" not in b


def test_fetch_ficha_chiletrabajos_y_trabajando(monkeypatch):
    monkeypatch.setattr(_comun, "get", lambda fuente, url, params=None, retries=2, as_text=False: (
        _FICHA.replace("</title>", "</title><table><tr><td>Fecha</td><td><div>2026-10-14 10:34:37</div></td></tr></table>")
        if as_text else {"idOferta": 7, "descripcionOferta": "<p>Backend Java</p>", "nombreJornada": "Mixta (Teletrabajo + Presencial)",
                         "fechaPublicacionFormatoIngles": "2026-10-19",
                         "ubicacion": {"nombreComuna": "Las Condes", "nombreRegion": "Metropolitana de Santiago"}}))
    c = chiletrabajos.fetch_ficha("https://www.chiletrabajos.cl/trabajo/dev-111")
    assert c["_access"] == "ok" and c["modality_badge"] == "remoto" and c["date_posted"] == "2026-10-14"
    t = trabajando.fetch_ficha("https://www.trabajando.cl/trabajo-empleo/7")
    assert t["_access"] == "ok" and t["modality_badge"] == "híbrido" and t["location"] == "Las Condes, Metropolitana de Santiago"
    assert t["date_posted"] == "2026-10-19" and "Backend Java" in t["description"]
    monkeypatch.setattr(_comun, "get", lambda *a, **k: "" if k.get("as_text") else {})
    assert chiletrabajos.fetch_ficha("https://www.chiletrabajos.cl/trabajo/x-1")["_access"] == "blocked"
    assert trabajando.fetch_ficha("https://www.trabajando.cl/trabajo-empleo/9")["_access"] == "blocked"


def test_enrich_enruta_las_fichas_de_las_fuentes_chilenas(monkeypatch):
    from jobhunt import enrich as en
    monkeypatch.setattr(chiletrabajos, "fetch_ficha", lambda url: {"_access": "ok", "description": "ct"})
    monkeypatch.setattr(trabajando, "fetch_ficha", lambda url: {"_access": "ok", "description": "tb"})
    r = {"group_id": "g", "title": "t"}
    assert en._fetch_ficha({**r, "url": "https://www.chiletrabajos.cl/trabajo/dev-1"})["description"] == "ct"
    assert en._fetch_ficha({**r, "url": "https://www.trabajando.cl/trabajo-empleo/5"})["description"] == "tb"
