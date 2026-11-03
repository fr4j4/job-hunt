"""`ofertas.id`: entero estable y jamás reutilizado; group_id sigue siendo la clave de negocio (dedup)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jobhunt import db as database
from jobhunt.config import load_config
from jobhunt.web import auth
from jobhunt.web.app import crear_app

MISMO = {"Sec-Fetch-Site": "same-origin"}
ESCR = {**MISMO, "X-JH": "1"}
LARGO = "titulo muy largo " * 6   # 102 caracteres
EMPRESA_LARGA = "Empresa de nombre larguisimo"          # >100 caracteres: antes la API lo truncaba y no encontraba la oferta


@pytest.fixture
def ent(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.web.ui = "legacy"
    monkeypatch.setattr(type(cfg), "db_path", property(lambda self: tmp_path / "id.sqlite"), raising=False)
    conn = database.connect(cfg)
    database.init_db(conn)
    yield cfg, conn
    conn.close()


def _nueva(conn, titulo, empresa="Acme"):
    gid, _ = database.upsert(conn, {"title": titulo, "company": empresa, "url": f"https://x.cl/{abs(hash(titulo))}",
                                    "source": "laborum:q", "date": "2026-10-07"}, "2026-10-07T00:00:00+00:00")
    conn.commit()
    return gid


def _id(conn, gid):
    return conn.execute("SELECT id FROM ofertas WHERE group_id=?", (gid,)).fetchone()[0]


def test_ids_enteros_crecientes_y_group_id_unico(ent):
    _, conn = ent
    a, b = _nueva(conn, "Dev uno"), _nueva(conn, "Dev dos")
    assert (_id(conn, a), _id(conn, b)) == (1, 2)
    assert database.upsert(conn, {"title": "Dev uno", "company": "Acme", "url": "https://otra.cl/x", "source": "indeed:q"},
                           "2026-10-08T00:00:00+00:00") == (a, False)            # el dedup sigue por group_id
    assert conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0] == 2 and _id(conn, a) == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ofertas (group_id, title, first_seen, last_seen) VALUES (?,?,?,?)", (a, "x", "", ""))


def test_un_id_jamas_se_reutiliza_aunque_se_purgue(ent):
    _, conn = ent
    for t in ("Ingeniero de datos senior", "Analista QA automatizacion", "Desarrollador mobile flutter"):
        _nueva(conn, t, t[:6])
    conn.execute("DELETE FROM ofertas")                    # /db all
    conn.commit()
    nueva = _nueva(conn, "Arquitecto cloud kubernetes", "Zeta")
    assert _id(conn, nueva) == 4                           # no vuelve a 1: lo que referencie ids viejos no apunta a otra oferta


def test_asignar_id_no_cuenta_como_edicion(ent):
    _, conn = ent
    g = _nueva(conn, "Dev tres")
    antes = conn.execute("SELECT updated_at FROM ofertas WHERE group_id=?", (g,)).fetchone()[0]
    conn.execute("UPDATE ofertas SET score=77 WHERE group_id=?", (g,))
    assert conn.execute("SELECT updated_at FROM ofertas WHERE group_id=?", (g,)).fetchone()[0] == antes


def test_esquema_antiguo_se_rechaza_con_un_mensaje_claro(tmp_path):
    conn = sqlite3.connect(tmp_path / "vieja.sqlite")
    conn.execute("CREATE TABLE ofertas (group_id TEXT PRIMARY KEY, title TEXT)")
    with pytest.raises(database.EsquemaAntiguo, match="reset_ofertas"):
        database.init_db(conn)


def test_reset_ofertas_limpia_lo_dependiente_y_conserva_el_resto(ent):
    cfg, conn = ent
    from jobhunt.analytics.materializar import materializar
    g = _nueva(conn, "Dev cuatro")
    conn.execute("INSERT INTO scan_log (ts,total_seen,new_count,sources_summary) VALUES ('2026-10-08T00:00:00Z',1,1,'{}')")
    conn.execute("INSERT INTO channel_posts (message_id, group_id, kind, bucket, posted_at) VALUES (1,?,'offer',?, 'x')", (g, g))
    conn.commit()
    materializar(conn, cfg)
    assert database.reset_ofertas(conn)[0] == "ofertas"
    database.init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM oferta_eventos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM scan_log").fetchone()[0] == 1          # intacto
    assert conn.execute("SELECT COUNT(*) FROM channel_posts").fetchone()[0] == 1     # intacto: evita repostear
    assert _id(conn, _nueva(conn, "Dev cinco")) == 1                                 # el esquema nuevo parte de 1


def test_el_canal_no_republica_lo_ya_publicado_tras_una_limpieza(ent):
    cfg, conn = ent
    from jobhunt.channel import _GATE_SQL
    g = _nueva(conn, "Dev seis publicada")
    conn.execute("UPDATE ofertas SET market_score=95, date_canonical=date('now'), active=1 WHERE group_id=?", (g,))
    conn.commit()
    candidatas = lambda: [r["group_id"] for r in conn.execute(_GATE_SQL, {"min_score": 50, "max_age": 30})]   # noqa: E731
    assert candidatas() == [g]
    conn.execute("INSERT INTO channel_posts (message_id, group_id, kind, bucket, posted_at) VALUES (1,?,'offer',?, 'x')", (g, g))
    conn.commit()
    assert candidatas() == []        # notified_channel_at está vacío (fila nueva) pero ya se publicó


# ---------------- API / web ----------------

@pytest.fixture
def web(ent):
    cfg, conn = ent
    cli = TestClient(crear_app(cfg))
    tok = auth.crear_token_login(conn)
    assert cli.post("/login", data={"t": tok}, headers=MISMO, follow_redirects=False).status_code == 303
    return cfg, conn, cli


def test_api_acepta_id_numerico_y_group_id_largo(web):
    _, conn, cli = web
    g = _nueva(conn, LARGO, EMPRESA_LARGA)
    assert len(g) > 100
    oid = _id(conn, g)
    d = cli.get(f"/api/oferta/{oid}").json()
    assert d["id"] == oid and d["ref"] == g
    assert cli.get(f"/api/oferta/{g}").json()["id"] == oid                # enlaces viejos con el group_id (regresión del [:100])
    assert cli.get("/api/oferta/99999").status_code == 404 and cli.get("/api/oferta/no-existe").status_code == 404


def test_estado_por_id_y_por_group_id_apuntan_a_lo_mismo(web):
    _, conn, cli = web
    g = _nueva(conn, LARGO + "x", EMPRESA_LARGA + "2")
    oid = _id(conn, g)
    assert cli.put(f"/api/estado/{oid}", headers=ESCR, json={"estado": "guardada"}).json()["id"] == oid
    assert cli.put(f"/api/estado/{g}", headers=ESCR, json={"estado": "postulada"}).status_code == 200
    filas = cli.get("/api/estados").json()["estados"]
    assert [(f["oferta_id"], f["estado"]) for f in filas] == [(oid, "postulada")]
    assert cli.delete(f"/api/estado/{oid}", headers=ESCR).status_code == 204 and cli.get("/api/estados").json()["estados"] == []


def test_snapshot_y_busqueda_usan_ids_numericos(web):
    _, conn, cli = web
    g = _nueva(conn, "Backend con pasión única")
    conn.execute("UPDATE ofertas SET description='pasión única de verdad' WHERE group_id=?", (g,))
    conn.commit()
    d = cli.get("/api/snapshot").json()
    assert d["cols"]["id"] == [_id(conn, g)] and isinstance(d["cols"]["id"][0], int)
    assert cli.get("/api/buscar?q=pasión única").json()["ids"] == [_id(conn, g)]


def test_web_clasica_abre_por_id_y_por_group_id(web):
    _, conn, cli = web
    g = _nueva(conn, "Dev legado")
    oid = _id(conn, g)
    assert "Dev legado" in cli.get(f"/oferta/{oid}").text and "Dev legado" in cli.get(f"/oferta/{g}").text
    assert f'href="/oferta/{oid}"' in cli.get("/").text                    # los enlaces nuevos usan el id


def test_snapshot_con_la_base_vacia_trae_todas_las_columnas(web):
    """Regresión: tras limpiar la DB el snapshot devolvía cols={} y la interfaz fallaba con «No se pudo cargar»."""
    _, conn, cli = web
    vacio = cli.get("/api/snapshot").json()
    assert vacio["n"] == 0 and vacio["cols"] and all(v == [] for v in vacio["cols"].values())
    _nueva(conn, "Dev para comparar columnas")
    lleno = cli.get("/api/snapshot").json()
    assert set(vacio["cols"]) == set(lleno["cols"])


def test_el_etag_depende_de_la_revision_del_cuerpo(web, monkeypatch):
    """Regresión: arreglar el cuerpo del snapshot sin cambiar el ETag dejaba a los navegadores con la respuesta vieja (304)."""
    from jobhunt.web.api import snapshot
    _, conn, cli = web
    antes = cli.get("/api/snapshot").headers["etag"]
    monkeypatch.setattr(snapshot, "REV", snapshot.REV + 1)
    despues = cli.get("/api/snapshot", headers={"If-None-Match": antes})
    assert despues.status_code == 200 and despues.headers["etag"] != antes
