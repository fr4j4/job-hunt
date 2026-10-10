"""Fuentes de APIs abiertas: getonboard, himalayas, remotive, weworkremotely (HTTP simulado)."""
import pytest

from jobhunt.sources import _comun, errores, getonboard, himalayas, remotive, weworkremotely

REQUIRED = {"title", "company", "location", "date", "url", "source", "found_by",
            "modality", "salary", "_desc", "description_source"}


class _Resp:
    def __init__(self, data=None, text="", status=200):
        self._d, self.text, self.status_code = data, text, status

    def json(self):
        return self._d


def _http(monkeypatch, respuesta):
    monkeypatch.setattr(_comun.requests, "get", lambda *a, **k: respuesta)


def test_getonboard_filtra_pais_y_mapea_campos(monkeypatch):
    def item(slug, paises, **extra):
        return {"links": {"public_url": f"https://www.getonbrd.com/jobs/{slug}"},
                "attributes": {"title": f"Dev {slug}", "countries": paises, "published_at": 1791547528,
                               "remote_modality": "hybrid", "description": "<p>Python</p>",
                               "company": {"data": {"attributes": {"name": "Acme"}}}, **extra}}
    _http(monkeypatch, _Resp({"data": [
        item("cl", ["Chile"], min_salary=2000, max_salary=2200),
        item("rem", ["Remote"]),
        item("co", ["Colombia"]),            # fuera: ni Chile ni Remote
        item("cl", ["Chile"]),               # URL repetida → dedup
    ]}))
    r = getonboard.jobs(["python"], "perfil:")
    assert [j["url"].rsplit("/", 1)[1] for j in r] == ["cl", "rem"]
    assert REQUIRED <= r[0].keys()
    assert r[0]["company"] == "Acme" and r[0]["modality"] == "híbrido"
    assert r[0]["salary"] == "USD 2000-2200 mensual" and r[0]["date"] == "2026-10-09"
    assert r[0]["found_by"] == "perfil:python" and r[0]["description_source"] == "getonboard-api"


def test_himalayas_mapea_campos(monkeypatch):
    _http(monkeypatch, _Resp({"jobs": [
        {"title": "Senior Python Dev", "companyName": "Foo", "minSalary": 50000, "maxSalary": 70000,
         "currency": "USD", "salaryPeriod": "annual", "pubDate": 1790075996,
         "applicationLink": "https://himalayas.app/x", "description": "<b>desc</b>"},
        {"title": "Sin link"},
    ]}))
    r = himalayas.jobs(["python"])
    assert len(r) == 1 and REQUIRED <= r[0].keys()
    assert r[0]["salary"] == "USD 50000-70000 annual" and r[0]["modality"] == "remoto"
    assert r[0]["_desc"] == "desc"


def test_remotive_filtra_ubicacion_y_categoria(monkeypatch):
    def j(n, loc, cat):
        return {"title": f"T{n}", "url": f"https://remotive.com/{n}", "company_name": "C",
                "candidate_required_location": loc, "category": cat,
                "publication_date": "2026-10-07T01:11:09", "description": "<p>x</p>"}
    _http(monkeypatch, _Resp({"jobs": [
        j(1, "Worldwide", "Software Development"), j(2, "USA", "Software Development"),
        j(3, "LATAM, Europe", "DevOps / Sysadmin"), j(4, "Worldwide", "Writing")]}))
    r = remotive.jobs(["python"])
    assert [x["title"] for x in r] == ["T1", "T3"] and r[0]["date"] == "2026-10-07"


_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Dremio: Software Engineer</title><region>Anywhere in the World</region>
<pubDate>Mon, 28 Sep 2026 22:12:45 +0000</pubDate><link>https://weworkremotely.com/remote-jobs/a</link>
<description>&lt;p&gt;Hola&lt;/p&gt;</description></item>
<item><title>Acme: Dev</title><region>USA Only</region><link>https://weworkremotely.com/remote-jobs/b</link></item>
</channel></rss>"""


def test_weworkremotely_rss(monkeypatch):
    _http(monkeypatch, _Resp(text=_RSS))
    r = weworkremotely.jobs()
    assert len(r) == 1 and REQUIRED <= r[0].keys()
    assert (r[0]["company"], r[0]["title"], r[0]["date"]) == ("Dremio", "Software Engineer", "2026-09-28")
    assert r[0]["_desc"] == "Hola"


def test_weworkremotely_xml_invalido_registra_error(monkeypatch):
    errores.reset()
    _http(monkeypatch, _Resp(text="<html>no xml"))
    assert weworkremotely.jobs() == []
    assert errores.tomar("weworkremotely") == 1


@pytest.mark.parametrize("mod,fuente", [(getonboard, "getonboard"), (himalayas, "himalayas"),
                                         (remotive, "remotive")])
def test_http_error_devuelve_vacio_y_registra(monkeypatch, mod, fuente):
    errores.reset()
    _http(monkeypatch, _Resp(status=403))
    assert mod.jobs(["python"]) == []
    assert errores.tomar(fuente) == 3      # 1 intento + 2 reintentos
