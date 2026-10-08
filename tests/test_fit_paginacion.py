"""Gate de encaje con el perfil + paginación de fuentes."""
import sqlite3

from jobhunt import db as database
from jobhunt.config import load_config
from jobhunt.domain.roles import fit_ok
from jobhunt.enrich import apply_ia_result
from jobhunt.scoring import compute_score
from jobhunt.sources import computrabajo, indeed


def _cfg():
    c = load_config()
    c.channel.require_fit = True
    c.channel.min_fit_score = 45
    return c


def test_fit_ok_bloquea_score_bajo_y_encaje_bajo():
    c = _cfg()
    assert fit_ok({"score": 60, "ai_encaje": "alto"}, c)
    assert fit_ok({"score": 60, "ai_encaje": ""}, c)             # sin veredicto IA: manda el score
    assert not fit_ok({"score": 60, "ai_encaje": "bajo"}, c)
    assert not fit_ok({"score": 60, "ai_encaje": "Ninguno"}, c)
    assert not fit_ok({"score": 40, "ai_encaje": "alto"}, c)     # sin señales de perfil
    assert not fit_ok({"score": 0, "ai_encaje": "alto"}, c)      # red keyword
    c.channel.require_fit = False
    assert fit_ok({"score": 0, "ai_encaje": "ninguno"}, c)


def test_score_bonus_rol_del_perfil():
    c = _cfg()
    c.profile.roles = ["backend"]
    c.profile.techs = []
    c.profile.red_keywords = []
    base, _ = compute_score({"title": "Ingeniero de procesos", "location": "Santiago"}, c)
    con_rol, bd = compute_score({"title": "Desarrollador Backend", "location": "Santiago"}, c)
    assert con_rol == base + c.scoring.role_profile and "role_profile" in bd


def test_apply_ia_result_guarda_encaje():
    c = _cfg()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    gid, _ = database.upsert(conn, {"title": "Cajero", "company": "X", "url": "https://x/1",
                                    "source": "t", "date": "2026-10-01"}, "2026-10-01T00:00:00")
    apply_ia_result(conn, c, {"group_id": gid}, {"encaje": "Ninguno", "resumen": "r"})
    assert conn.execute("SELECT ai_encaje FROM ofertas WHERE group_id=?", (gid,)).fetchone()[0] == "ninguno"
    apply_ia_result(conn, c, {"group_id": gid}, {"encaje": "inventado", "resumen": "r2"})
    assert conn.execute("SELECT ai_encaje FROM ofertas WHERE group_id=?", (gid,)).fetchone()[0] == "ninguno"


def _card(i):
    return (f'<article class="box_offer"><a href="/ofertas-de-trabajo/oferta-de-trabajo-de-dev-{i}'
            f'-en-santiago-{i:032X}">x</a></article>')


def test_computrabajo_pagina_y_corta_sin_nuevas(monkeypatch):
    paginas = {"https://www.computrabajo.cl/empleos-de-python": _card(1) + _card(2),
               "https://www.computrabajo.cl/empleos-de-python?p=2": _card(3),
               "https://www.computrabajo.cl/empleos-de-python?p=3": _card(3)}   # repetida → corta
    pedidas = []
    monkeypatch.setattr(computrabajo, "fetch", lambda u: pedidas.append(u) or paginas.get(u, ""))
    out = computrabajo.jobs(["python"], "t:", max_pages=5)
    assert len(out) == 3 and len(pedidas) == 3


def test_indeed_pagina_por_cursor(monkeypatch):
    def _res(k):
        return {"job": {"key": k, "title": f"Dev {k}", "description": {"html": ""}}}
    resp = {"": {"data": {"jobSearch": {"pageInfo": {"nextCursor": "c2"}, "results": [_res("a"), _res("b")]}}},
            "c2": {"data": {"jobSearch": {"pageInfo": {"nextCursor": None}, "results": [_res("c")]}}}}
    monkeypatch.setattr(indeed, "_page", lambda q, cursor="": resp[cursor])
    out = indeed.jobs(["python"], "t:", max_pages=5)
    assert [o["url"][-1] for o in out] == ["a", "b", "c"]
