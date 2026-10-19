"""Staffy Jobs (portal propio): listado, ficha y elegibilidad desde Chile (HTTP simulado)."""
from jobhunt.sources import _comun, staffy


def _card(eyebrow, titulo, slug):
    return (f'<article class="job-card"><div><p class="eyebrow">{eyebrow}</p><h2>{titulo}</h2>'
            f'<p>Extracto de {titulo}</p></div><a class="button" href="/positions/{slug}">Ver vacante</a></article>')


_LISTADO = "<main>" + "".join([
    _card("Chile · Remoto", "Tech Lead &amp; Co", "tech-lead-1"),
    _card("LATAM · Remoto", "Backend Go", "backend-go-2"),
    _card("Chile (Santiago) · Hibrido (3 veces por semana)", "BI Engineer", "bi-3"),
    _card("Argentina (Buenos Aires) · Remoto", "Analista SAP", "sap-4"),
    _card("USA - East Cost · Remote", "Architect US", "arch-5"),
    _card("AR, CL, PY · Remoto", "Analytics Engineer", "analytics-6"),
    _card("UY, AR, PE, PY · Remoto", "Salesforce Dev", "sf-7"),
]) + "</main>"

_FICHA = """<main><article class="position-content"><a class="back-link" href="/">Volver</a>
<h1>Tech Lead Fullstack</h1><div class="metadata"><span>Argentina o Chile</span><span>Remoto</span><span>Tech Lead</span></div>
<h2>Sobre el rol</h2><p>Liderar técnicamente un equipo.</p><h2>Requisitos</h2><ul><li>7+ años con Java</li><li>Scrum</li></ul>
</article><aside><form><input name="phone_number"/></form></aside></main>"""


def test_listado_parsea_tarjetas():
    r = staffy.parse_listado(_LISTADO)
    assert len(r) == 7 and r[0]["url"] == "https://jobs.wearestaffy.com/positions/tech-lead-1"
    assert r[0]["title"] == "Tech Lead & Co" and r[0]["location"] == "Chile" and r[0]["modality_txt"] == "Remoto"


def test_ficha_extrae_descripcion_y_metadatos_sin_el_formulario():
    f = staffy.parse_ficha(_FICHA)
    assert f["meta"] == ["Argentina o Chile", "Remoto", "Tech Lead"]
    assert "Liderar técnicamente un equipo." in f["description"] and "7+ años con Java" in f["description"]
    assert "phone_number" not in f["description"] and "Volver" not in f["description"]


def test_jobs_filtra_elegibles_desde_chile_y_completa_con_la_ficha(monkeypatch):
    monkeypatch.setattr(_comun, "get", lambda fuente, url, params=None, retries=2, as_text=False:
                        _LISTADO if url.endswith("/vacantes") else _FICHA)
    r = staffy.jobs(None, "perfil:")
    assert [j["url"].rsplit("/", 1)[1] for j in r] == ["tech-lead-1", "backend-go-2", "bi-3", "analytics-6"]
    a = r[0]
    assert a["company"] == "Staffy" and a["modality"] == "remoto" and a["location"] == "Argentina o Chile"
    assert a["description_source"] == "staffy-html" and "Scrum" in a["_desc"]
    assert a["source"] == "staffy:vacantes" and a["found_by"] == "perfil:vacantes"
    assert r[2]["modality"] == "remoto"        # la ficha de prueba manda: metadatos ('Remoto')


def test_fetch_ficha_y_ficha_vacia(monkeypatch):
    monkeypatch.setattr(_comun, "get", lambda *a, **k: _FICHA)
    i = staffy.fetch_ficha("https://jobs.wearestaffy.com/positions/x-1?source=Victoria")
    assert i["_access"] == "ok" and i["modality_badge"] == "remoto" and "Scrum" in i["description"]
    monkeypatch.setattr(_comun, "get", lambda *a, **k: "")
    assert staffy.fetch_ficha("https://jobs.wearestaffy.com/positions/x-1")["_access"] == "blocked"


def test_enrich_enruta_las_fichas_de_staffy(monkeypatch):
    from jobhunt import enrich as en
    monkeypatch.setattr(staffy, "fetch_ficha", lambda url: {"_access": "ok", "description": "sf"})
    r = en._fetch_ficha({"group_id": "g", "title": "t", "url": "https://jobs.wearestaffy.com/positions/x-1"})
    assert r["description"] == "sf"
