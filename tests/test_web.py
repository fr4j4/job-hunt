"""Web privada: nadie ve nada sin el enlace de un solo uso que da /web en el bot."""
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from jobhunt import db as database
from jobhunt.config import load_config
from jobhunt.web import auth
from jobhunt.web.app import COOKIE, crear_app

MISMO = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    cfg = load_config()
    cfg.web.ui = "legacy"        # estas pruebas cubren la web clásica en "/"
    monkeypatch.setattr(type(cfg), "db_path", property(lambda self: tmp_path / "w.sqlite"), raising=False)
    conn = database.connect(cfg)
    database.init_db(conn)
    gid, _ = database.upsert(conn, {"title": "Dev <script>alert(1)</script> & Co", "company": "Evil <b>Corp</b>",
                                    "url": "javascript:alert(1)", "source": "linkedin:x",
                                    "date": "2026-10-07"}, "2026-10-07T00:00:00")
    gid2, _ = database.upsert(conn, {"title": "Backend Python Senior", "company": "Acme",
                                     "url": "https://x.cl/2", "source": "laborum:x",
                                     "date": "2026-10-07"}, "2026-10-07T00:00:00")
    conn.execute("UPDATE ofertas SET score=80, ai_encaje='alto', modality='remoto', salary='CLP 3000000' "
                 "WHERE group_id=?", (gid2,))
    conn.execute("INSERT INTO scan_log (ts,total_seen,new_count,sources_summary) VALUES "
                 "('2026-10-08T00:00:00',1,1,?)", (json.dumps({"indeed": {"n": 0, "err": 2}}),))
    conn.commit()
    cli = TestClient(crear_app(cfg))
    yield cfg, conn, cli, gid, gid2
    conn.close()


def _entrar(conn, cli):
    token = auth.crear_token_login(conn)
    r = cli.post("/login", data={"t": token}, headers=MISMO, follow_redirects=False)
    assert r.status_code == 303
    return token


def test_sin_sesion_no_se_ve_nada(entorno):
    _, _, cli, gid, _ = entorno
    for ruta in ("/", f"/oferta/{gid}", "/fuentes", "/?q=Acme", "/no-existe"):
        r = cli.get(ruta)
        assert r.status_code in (401, 404), ruta
        assert "Acme" not in r.text and "Backend" not in r.text and "/web" in r.text


def test_cookie_inventada_no_sirve(entorno):
    _, _, cli, _, _ = entorno
    cli.cookies.set(COOKIE, "inventada")
    assert cli.get("/").status_code == 401


def test_enlace_de_un_solo_uso_y_flags_de_cookie(entorno):
    _, conn, cli, _, _ = entorno
    token = auth.crear_token_login(conn)
    # el GET (vista previa de Telegram, escáneres) NO canjea el token
    assert "Entrar" in cli.get(f"/login?t={token}").text
    r = cli.post("/login", data={"t": token}, headers=MISMO, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "max-age" in sc
    assert cli.get("/").status_code == 200 and "Backend Python Senior" in cli.get("/").text
    # segundo uso del mismo enlace: rechazado
    otro = TestClient(cli.app)
    r2 = otro.post("/login", data={"t": token}, headers=MISMO, follow_redirects=False)
    assert r2.status_code == 403 and otro.get("/").status_code == 401


def test_token_vencido(entorno):
    _, conn, cli, _, _ = entorno
    token = auth.crear_token_login(conn, minutos=10, ahora=1000.0)
    assert auth.canjear_token(conn, token, ahora=1000.0 + 601) is None


def test_login_cross_site_rechazado(entorno):
    _, conn, cli, _, _ = entorno
    token = auth.crear_token_login(conn)
    r = cli.post("/login", data={"t": token}, headers={"Sec-Fetch-Site": "cross-site"}, follow_redirects=False)
    assert r.status_code == 403
    r = cli.post("/login", data={"t": token}, headers={"Origin": "https://atacante.com"}, follow_redirects=False)
    assert r.status_code == 403


def test_login_sin_sec_fetch_ni_origin(entorno):
    """HTTP en LAN: el navegador no manda Sec-Fetch-* ni siempre Origin.
    Sin ninguna cabecera de origen el POST debe entrar (no 403)."""
    _, conn, cli, _, _ = entorno
    token = auth.crear_token_login(conn)
    r = cli.post("/login", data={"t": token}, follow_redirects=False)
    assert r.status_code == 303


def test_login_con_referer_mismo_host(entorno):
    """Fallback a Referer: mismo host → entra; host ajeno → 403."""
    _, conn, cli, _, _ = entorno
    t1 = auth.crear_token_login(conn)
    r = cli.post("/login", data={"t": t1},
                 headers={"Referer": "http://testserver/login"}, follow_redirects=False)
    assert r.status_code == 303
    t2 = auth.crear_token_login(conn)
    r = cli.post("/login", data={"t": t2},
                 headers={"Referer": "https://atacante.com/x"}, follow_redirects=False)
    assert r.status_code == 403


def test_db_no_guarda_secretos_en_claro(entorno):
    _, conn, cli, _, _ = entorno
    token = _entrar(conn, cli)
    sesion = cli.cookies.get(COOKIE)
    volcado = " ".join(str(v) for t in ("web_tokens", "web_sesiones")
                       for r in conn.execute(f"SELECT * FROM {t}").fetchall() for v in r)
    assert token not in volcado and sesion not in volcado


def test_escapa_html_y_no_enlaza_javascript(entorno):
    _, conn, cli, gid, _ = entorno
    _entrar(conn, cli)
    for ruta in ("/", f"/oferta/{gid}"):
        html = cli.get(ruta).text
        assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
        assert "<b>Corp</b>" not in html
        assert 'href="javascript:' not in html


def test_cabeceras_de_seguridad(entorno):
    _, conn, cli, _, _ = entorno
    for r in (cli.get("/"), cli.get("/login")):
        assert "default-src 'none'" in r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["cache-control"] == "no-store"
        assert "server" not in {k.lower() for k in r.headers.keys()} or "uvicorn" not in r.headers.get("server", "")


def test_salir_y_revocar(entorno):
    _, conn, cli, _, _ = entorno
    _entrar(conn, cli)
    assert cli.post("/salir", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403  # CSRF
    assert cli.get("/").status_code == 200
    cli.post("/salir", headers=MISMO, follow_redirects=False)
    assert cli.get("/").status_code == 401
    _entrar(conn, cli)
    assert auth.revocar_todo(conn) == 1
    assert cli.get("/").status_code == 401


def test_limite_de_intentos(entorno):
    _, _, cli, _, _ = entorno
    codigos = [cli.post("/login", data={"t": f"malo{i}"}, headers=MISMO).status_code for i in range(25)]
    assert codigos[0] == 403 and codigos[-1] == 429


def test_filtros_orden_y_paginas(entorno):
    _, conn, cli, gid, gid2 = entorno
    _entrar(conn, cli)
    assert "Backend Python Senior" in cli.get("/?encaje=alto").text
    assert "Backend Python Senior" not in cli.get("/?encaje=ninguno").text
    assert "Backend Python Senior" in cli.get("/?mod=remoto&sueldo=1&min=70").text
    assert cli.get("/?orden=score;DROP TABLE ofertas&dir=asc").status_code == 200   # orden fuera de whitelist
    assert conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0] == 2
    assert cli.get("/?q=%25").status_code == 200 and cli.get("/?page=999").status_code == 200
    det = cli.get(f"/oferta/{gid2}").text
    assert "Por qué tiene ese puntaje" in det and "Ver y postular" in det and 'href="https://x.cl/2"' in det
    assert "Indeed" in cli.get("/fuentes").text


def test_comando_web_manda_enlace_sin_vista_previa(entorno, monkeypatch):
    import jobhunt.bot as bot
    cfg, conn, cli, _, _ = entorno
    cfg.telegram.allowed_chats = (1,)
    enviados = []
    monkeypatch.setattr(bot, "_tg_api", lambda c, m, p, retries=2: enviados.append(p) or {"ok": True})
    monkeypatch.setattr(bot.database, "connect", lambda c: sqlite3.connect(c.db_path))
    bot._handle_command(cfg, {"chat": {"id": 1}, "text": "/web"}, {})
    p = enviados[-1]
    assert p["disable_web_page_preview"] is True and p["link_preview_options"] == {"is_disabled": True}
    token = re.search(r"/login\?t=([\w-]+)", p["text"]).group(1)
    r = cli.post("/login", data={"t": token}, headers=MISMO, follow_redirects=False)
    assert r.status_code == 303
    # un chat fuera del allowlist no recibe nada
    enviados.clear()
    bot._handle_command(cfg, {"chat": {"id": 999}, "text": "/web"}, {})
    assert enviados == []


def test_detalle_con_barras_y_caracteres_raros_en_el_id(entorno):
    _, conn, cli, gid, _ = entorno
    _entrar(conn, cli)
    gid3, _ = database.upsert(conn, {"title": "Dev Python/AWS #1 ¿remoto? 100%", "company": "A&B",
                                     "url": "https://x.cl/3", "source": "linkedin:x", "date": "2026-10-07"},
                              "2026-10-07T00:00:00")
    conn.commit()
    lista = cli.get("/?q=python/aws").text
    enlace = re.search(r'href="(/oferta/[^"]+)"', lista).group(1)
    r = cli.get(enlace.replace("&amp;", "&"))
    assert r.status_code == 200 and "Dev Python/AWS #1" in r.text
