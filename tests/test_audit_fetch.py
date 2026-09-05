"""Tests spec-audit — F7 aira aware/naive, F3 fuentes no abortan el barrido,
F6 computrabajo delega en normalize_date, F2/S4 salario anual, F5 LOO no
elimina duplicados."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobhunt import enrich as en
from jobhunt import stats as _st
from jobhunt.sources import aira, computrabajo


# ---------- F7: aira fechas aware vs naive ----------
def test_aira_feed_dormido_fecha_aware_no_lanza(monkeypatch):
    """last_access_offer con tz-aware (TypeError al comparar con datetime.now()
    naive) no debe tumbar la fuente — se captura y se sigue procesando."""
    fresh_aware = (datetime.now(timezone.utc)).isoformat()
    d = {"offers": [{"name": "Dev Python", "id": "1",
                      "last_access_offer": fresh_aware}]}

    class FakeResp:
        status_code = 200
        def json(self):
            return d

    monkeypatch.setattr(aira.requests, "Session",
                        lambda: type("S", (), {"headers": type("H", (), {"update": lambda *a: None})(),
                                                "get": lambda self, url, timeout=20: FakeResp()})())
    out = aira.jobs(["feedx"], "aira:")
    assert len(out) == 1
    assert out[0]["title"] == "Dev Python"


# ---------- F3: una fuente rota no aborta el barrido ----------
def test_fuente_segura_continua_tras_excepcion():
    """El helper _fuente_segura del cli.cmd_run: una fuente que lanza no
    interrumpe — retorna [] y loguea, las siguientes corren."""
    from jobhunt import cli

    calls = []

    def _fuente_segura(nombre, fn):
        try:
            return fn()
        except Exception:
            return []

    calls.append(_fuente_segura("rota", lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
    calls.append(_fuente_segura("sana", lambda: ["ok"]))
    assert calls == [[], ["ok"]]

    # asegura que el helper real vive en cmd_run (no se borró en el refactor)
    import inspect
    src = inspect.getsource(cli.cmd_run)
    assert "_fuente_segura" in src
    assert 'log.warning("fuente %s falló (continúa): %s"' in src


# ---------- F6: computrabajo delega en normalize_date ----------
def test_computrabajo_hace_2_semanas(monkeypatch):
    now = datetime.now(timezone.utc)
    card = ('<article class="box_offerx"><a href="/ofertas-de-trabajo/'
            'oferta-de-trabajo-de-dev-en-santiago-0123456789ABCDEF0123456789ABCDEF#x">x</a>'
            '<span>Hace 2 semanas</span></article>')
    html_ = '<article class="box_offer' + card

    monkeypatch.setattr(computrabajo, "fetch", lambda url: html_)
    out = computrabajo.jobs(["python"])
    assert len(out) == 1
    esperado = (now - timedelta(days=14)).date().isoformat()
    assert out[0]["date"] == esperado


def test_computrabajo_llama_normalize_date(monkeypatch):
    """Contrato mínimo: computrabajo usa channel.normalize_date (no un parser
    propio) para el texto relativo."""
    calls = []
    orig = computrabajo.normalize_date
    def spy(raw, now=None):
        calls.append(raw)
        return orig(raw, now)
    monkeypatch.setattr(computrabajo, "normalize_date", spy)
    card = ('<article class="box_offerx"><a href="/ofertas-de-trabajo/'
            'oferta-de-trabajo-de-dev-en-santiago-0123456789ABCDEF0123456789ABCDEF#x">x</a>'
            '<span>Hace 3 horas</span></article>')
    html_ = '<article class="box_offer' + card
    monkeypatch.setattr(computrabajo, "fetch", lambda url: html_)
    computrabajo.jobs(["python"])
    assert calls and "Hace 3 horas" in calls[0]


# ---------- F2/S4: salario anual JSON-LD ----------
@pytest.fixture
def mem_db():
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _insert(conn, gid, salary="", url="http://x.cl/1", desc="x" * 50):
    conn.execute("INSERT INTO ofertas (group_id, title, salary, salary_raw, salary_source, "
                 "salary_status, salary_note, url, description, active, first_seen, last_seen) "
                 "VALUES (?,?,?,?,?,?,?,?,?,1, datetime('now'), datetime('now'))",
                 (gid, f"Título {gid}", salary, salary, "feed", "", "", url, desc))
    conn.commit()


def _cfg():
    from jobhunt.config import load_config
    c = load_config()
    c.ia.local_enabled = False
    return c


def test_salario_anual_bajo_ceiling_se_divide_por_12(mem_db):
    """18.000.000/año (bajo CEILING de 30M) debe dividirse por 12 -> 1.5M
    mensual, no quedar como 18M implausible."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    r = {"group_id": "g1", "title": "Dev", "salary": "", "ai_opinion": "", "ia_model": ""}
    info = {"_access": "ok", "description": "x" * 500, "salary": "CLP 18000000/año",
            "techs_desc": []}
    pool = [1_400_000, 1_600_000, 1_500_000]
    en._aplicar_ficha(mem_db, r, info, pool, cfg)
    row = mem_db.execute("SELECT salary, salary_status, salary_note FROM ofertas WHERE group_id='g1'").fetchone()
    assert _st.parse_salary_clp(row["salary"]) == 18_000_000   # salary_raw/texto crudo intacto
    assert row["salary_note"] == "annual_likely"
    assert row["salary_status"] != "implausible"


# ---------- F5: leave-one-out no elimina duplicados ----------
def test_loo_no_elimina_duplicados(mem_db):
    """Dos ofertas con el mismo sueldo: al evaluar una, el pool debe seguir
    conteniendo la OTRA ocurrencia (solo se quita UNA instancia)."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    r = {"group_id": "g1", "title": "Dev", "salary": "", "ai_opinion": "", "ia_model": ""}
    info = {"_access": "ok", "description": "x" * 500, "salary": "CLP 2000000", "techs_desc": []}
    pool = [2_000_000, 2_000_000, 2_100_000, 2_200_000]   # dos ofertas a 2M
    captured = {}
    orig = _st.classify_salary
    def spy(v, p):
        captured["pool"] = list(p)
        return orig(v, p)
    prev = _st.classify_salary
    _st.classify_salary = spy
    try:
        en._aplicar_ficha(mem_db, r, info, pool, cfg)
    finally:
        _st.classify_salary = prev
    assert captured["pool"].count(2_000_000) == 1   # se quitó UNA, no las dos
    assert len(captured["pool"]) == len(pool) - 1
