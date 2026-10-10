"""API JSON de la web v2: sesión, snapshot, historia, escrituras y seguridad."""
import gzip
import json

import pytest
from fastapi.testclient import TestClient

from jobhunt import db as database
from jobhunt.config import load_config
from jobhunt.web import auth
from jobhunt.web.app import COOKIE, crear_app

MISMO = {"Sec-Fetch-Site": "same-origin"}
ESCR = {**MISMO, "X-JH": "1"}


@pytest.fixture
def ent(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(type(cfg), "db_path", property(lambda self: tmp_path / "api.sqlite"), raising=False)
    conn = database.connect(cfg)
    database.init_db(conn)
    gids = []
    for i, (t, emp, fuente, extra) in enumerate([
        ("Dev <script>alert(1)</script> & Co", "Evil <b>Corp</b>", "linkedin", {"url": "javascript:alert(1)"}),
        ("Backend Python Senior", "Acme", "laborum", {"salary": "CLP 3000000", "rol_categoria": "Backend",
                                                      "seniority_real": "senior", "techs": "Py;AWS", "modality": "remoto",
                                                      "ai_encaje": "alto", "score": 80,
                                                      "description": "Buscamos backend con Python y mucha pasión"}),
        ("Data Engineer", "Beta", "indeed", {"techs": "Py;SQL", "rol_categoria": "Data"}),
    ]):
        gid, _ = database.upsert(conn, {"title": t, "company": emp, "url": extra.pop("url", f"https://x.cl/{i}"),
                                        "source": f"{fuente}:x", "date": "2026-10-07"}, "2026-10-07T00:00:00+00:00")
        if extra:
            conn.execute(f"UPDATE ofertas SET {', '.join(k + '=?' for k in extra)} WHERE group_id=?", (*extra.values(), gid))
        gids.append(gid)
    conn.execute("INSERT INTO scan_log (ts,total_seen,new_count,sources_summary) VALUES "
                 "('2026-10-08T00:00:00+00:00',3,3,?)", (json.dumps({"indeed": {"n": 0, "err": 2}, "laborum": {"n": 5, "err": 0}}),))
    conn.commit()
    cli = TestClient(crear_app(cfg))
    yield cfg, conn, cli, gids
    conn.close()


def _entrar(conn, cli):
    token = auth.crear_token_login(conn)
    assert cli.post("/login", data={"t": token}, headers=MISMO, follow_redirects=False).status_code == 303


RUTAS_GET = ["/api/snapshot", "/api/semantica", "/api/perfil", "/api/oferta/x", "/api/buscar?q=abc",
             "/api/historia/diaria", "/api/historia/techs", "/api/historia/eventos",
             "/api/historia/supervivencia", "/api/fuentes", "/api/estados", "/api/vistas"]


@pytest.mark.parametrize("ruta", RUTAS_GET)
def test_sin_sesion_401_json_sin_datos(ent, ruta):
    _, _, cli, _ = ent
    r = cli.get(ruta)
    assert r.status_code == 401 and r.json() == {"error": "sin_sesion"}
    assert "Acme" not in r.text


@pytest.mark.parametrize("metodo,ruta", [("PUT", "/api/estado/x"), ("DELETE", "/api/estado/x"),
                                         ("POST", "/api/vistas"), ("DELETE", "/api/vistas/1")])
def test_escrituras_sin_sesion_401(ent, metodo, ruta):
    _, _, cli, _ = ent
    assert cli.request(metodo, ruta, headers=ESCR, json={}).status_code == 401


def test_api_desconocida_es_json(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    r = cli.get("/api/no-existe")
    assert r.status_code == 404 and "error" in r.json()


def test_snapshot_columnar_con_diccionarios_y_sin_descripcion(ent):
    cfg, conn, cli, gids = ent
    _entrar(conn, cli)
    r = cli.get("/api/snapshot", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    d = r.json()
    assert d["v"] == 2 and d["n"] == 3 and not d["truncado"]
    c, dic = d["cols"], d["dicts"]
    assert all(len(v) == 3 for v in c.values())                   # columnas del mismo largo
    assert "descripcion" not in c and "description" not in json.dumps(d)
    assert "Buscamos backend" not in json.dumps(d)                # la descripción NO viaja
    i = c["id"].index(gids[1])
    assert dic["empresa"][c["empresa"][i]] == "Acme" and dic["fuente"][c["fuente"][i]] == "laborum"
    assert c["rol_familia"][i] == "Desarrollo" and c["seniority"][i] == "senior" and c["modalidad"][i] == "remoto"
    assert c["sueldo"][i] == 3000000 and c["sueldo_valido"][i] == 1
    assert sorted(dic["tech"][t] for t in c["techs"][i]) == ["AWS", "Python"]
    assert c["techs"][c["id"].index(gids[0])] is None             # techs desconocidas = null, no []
    # el título malicioso viaja como dato (el front lo pinta como texto)
    assert "<script>" in c["titulo"][c["id"].index(gids[0])]


def test_snapshot_etag_304_y_cambia_con_datos(ent):
    _, conn, cli, gids = ent
    _entrar(conn, cli)
    r = cli.get("/api/snapshot")
    tag = r.headers["etag"]
    assert cli.get("/api/snapshot", headers={"If-None-Match": tag}).status_code == 304
    conn.execute("UPDATE ofertas SET title='Otro titulo', updated_at='2099-01-01T00:00:00Z' WHERE group_id=?", (gids[2],))
    conn.commit()
    r2 = cli.get("/api/snapshot", headers={"If-None-Match": tag})
    assert r2.status_code == 200 and r2.headers["etag"] != tag


def test_snapshot_incluye_estado_y_posible_cierre(ent):
    _, conn, cli, gids = ent
    _entrar(conn, cli)
    assert cli.put(f"/api/estado/{gids[1]}", headers=ESCR, json={"estado": "guardada", "nota": "ver lunes"}).status_code == 200
    d = cli.get("/api/snapshot").json()
    assert d["cols"]["estado"][d["cols"]["id"].index(gids[1])] == "guardada"


def test_detalle_con_desglose_y_descripcion(ent):
    _, conn, cli, gids = ent
    _entrar(conn, cli)
    d = cli.get(f"/api/oferta/{gids[1]}").json()
    assert d["empresa"] == "Acme" and "Buscamos backend" in d["descripcion"]
    assert d["desglose"] and {"clave", "etiqueta", "valor"} <= set(d["desglose"][0])
    assert d["techs"] == ["AWS", "Python"] and any(e["tipo"] == "aparecida" for e in d["eventos"])
    mal = cli.get(f"/api/oferta/{gids[0]}").json()
    assert mal["url"] == ""                                       # javascript: nunca sale como enlace
    assert cli.get("/api/oferta/no-existe").status_code == 404


def test_buscar_en_descripcion(ent):
    _, conn, cli, gids = ent
    _entrar(conn, cli)
    assert cli.get("/api/buscar?q=pasión").json()["ids"] == [gids[1]]
    assert cli.get("/api/buscar?q=a").json()["ids"] == []         # demasiado corto
    assert cli.get("/api/buscar?q=%25").json()["ids"] == []       # % se escapa, no es comodín


def test_historia_diaria_whitelist_e_inyeccion(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    ok = cli.get("/api/historia/diaria?m=n_activas&por=rol_familia").json()
    assert ok["dias_historia"] >= 1 and {s["clave"] for s in ok["series"]} >= {"Desarrollo"}
    tot = cli.get("/api/historia/diaria?m=n_activas&por=*").json()
    assert tot["series"][0]["clave"] == "*" and tot["series"][0]["valores"][-1] == 3
    for mal in ("m=n_activas;DROP TABLE ofertas", "m=x", "m=n_activas&por=fuente%20OR%201=1", "m=n_activas&por=nope"):
        assert cli.get(f"/api/historia/diaria?{mal}").status_code == 400, mal
    assert conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0] == 3


def test_historia_techs_usa_base_con_techs_conocidas(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    d = cli.get("/api/historia/techs?techs=Python,AWS").json()
    py = next(s for s in d["series"] if s["tech"] == "Python")
    assert py["n_activas"][-1] == 2 and py["n_base"][-1] == 2 and py["demanda"][-1] == 1.0


def test_eventos_y_supervivencia_oculta_con_poca_muestra(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    ev = cli.get("/api/historia/eventos?tipo=aparecida").json()["eventos"]
    assert len(ev) == 3 and all(e["tipo"] == "aparecida" for e in ev)
    assert cli.get("/api/historia/eventos?tipo=x").status_code == 400
    assert cli.get("/api/historia/eventos?limite=9999").status_code == 422
    sv = cli.get("/api/historia/supervivencia").json()
    assert sv["oculto"] is True and sv["n"] == 3 and sv["cierres"] == 0


def test_fuentes_con_salud_y_cobertura(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    d = cli.get("/api/fuentes").json()
    assert {f["fuente"] for f in d["fuentes"]} == {"indeed", "laborum"}
    assert next(c for c in d["cobertura"] if c["fuente"] == "laborum")["sueldo"] == 1
    assert d["analytics"]["filas"] == 3


def test_perfil_y_semantica(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    assert "salary_min" in cli.get("/api/perfil").json()
    s = cli.get("/api/semantica").json()
    assert "rol_familia" in s["dimensiones"] and s["metricas"]["sueldo_p50"]["min_n"] == 10


# ---------------- escrituras ----------------

def test_estado_csrf_y_validacion(ent):
    _, conn, cli, gids = ent
    _entrar(conn, cli)
    url = f"/api/estado/{gids[1]}"
    cuerpo = {"estado": "postulada", "nota": "ok"}
    assert cli.put(url, headers=MISMO, json=cuerpo).status_code == 403                       # sin X-JH
    assert cli.put(url, headers={"Sec-Fetch-Site": "cross-site", "X-JH": "1"}, json=cuerpo).status_code == 403
    assert cli.put(url, headers={**ESCR, "Content-Type": "text/plain"}, content="x").status_code == 415
    assert cli.put(url, headers=ESCR, json={"estado": "inventado"}).status_code == 422
    assert cli.put(url, headers=ESCR, json={"estado": "oferta", "nota": "x" * 2001}).status_code == 422
    assert cli.put("/api/estado/no-existe", headers=ESCR, json=cuerpo).status_code == 404
    assert cli.put(url, headers=ESCR, json=cuerpo).status_code == 200
    assert cli.get("/api/estados").json()["estados"][0]["estado"] == "postulada"
    assert cli.delete(url, headers=ESCR).status_code == 204
    assert cli.get("/api/estados").json()["estados"] == []


def test_vistas_crud_y_limites(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    v = {"nombre": "Backend remoto", "tipo": "ofertas", "spec": {"f": {"rol": ["Backend"]}}}
    r = cli.post("/api/vistas", headers=ESCR, json=v)
    assert r.status_code == 201
    vid = r.json()["id"]
    assert cli.post("/api/vistas", headers=ESCR, json=v).status_code == 409                 # nombre repetido
    assert cli.post("/api/vistas", headers=ESCR, json={**v, "nombre": "x" * 61}).status_code == 422
    assert cli.post("/api/vistas", headers=ESCR, json={**v, "nombre": "y", "tipo": "otra"}).status_code == 422
    assert cli.post("/api/vistas", headers=ESCR, json={**v, "nombre": "z", "spec": {"a": "q" * 4100}}).status_code == 422
    assert cli.get("/api/vistas").json()["vistas"][0]["spec"] == v["spec"]
    for i in range(49):
        assert cli.post("/api/vistas", headers=ESCR, json={**v, "nombre": f"v{i}"}).status_code == 201
    assert cli.post("/api/vistas", headers=ESCR, json={**v, "nombre": "una-mas"}).status_code == 422
    assert cli.delete(f"/api/vistas/{vid}", headers=ESCR).status_code == 204


# ---------------- SPA / cabeceras ----------------

def test_spa_exige_sesion_y_sin_dist_responde_503(ent):
    _, conn, cli, _ = ent
    assert cli.get("/v2").status_code == 401 and cli.get("/v2/analisis").status_code == 401
    _entrar(conn, cli)
    r = cli.get("/v2/analisis")
    assert r.status_code in (200, 503)            # 503 si frontend/ aún no se construyó


def test_assets_no_permite_salir_del_directorio(ent):
    _, _, cli, _ = ent
    for ruta in ("/assets/../app.py", "/assets/%2e%2e/app.py", "/assets/..%2fapp.py", "/assets/nope.js"):
        assert cli.get(ruta).status_code == 404, ruta


def test_la_web_legacy_conserva_su_csp_estricta(ent):
    _, conn, cli, _ = ent
    _entrar(conn, cli)
    csp = cli.get("/").headers["content-security-policy"]
    assert "script-src" not in csp and "default-src 'none'" in csp
    assert "script-src 'self'" not in cli.get("/api/perfil").headers["content-security-policy"]
