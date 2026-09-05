"""Tests de regresion — auditoria integral P1 (commit 69cb261).

Cubre: enrich.apply_ia_result (fila fresca: guard A3, fosiles, coercion salario,
_ia_model), enrich.ia_extract_lote (item basura), enrich.ia_extract_local_con_fallback
(modelo real), enrich.fetch_page (5xx -> error), scoring.compute_score (word
boundaries tech/red_keyword, ingles via IA, salario anual), channel._categorias_dev
(word boundaries), cli.cmd_run (evento por lote, no rebindea stop_event global).
"""
import inspect
import json
import sqlite3
import threading

import pytest

from jobhunt import enrich as en
from jobhunt import scoring as sc
from jobhunt import channel as ch
from jobhunt import cli


@pytest.fixture
def mem_db():
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _cfg():
    from jobhunt.config import load_config
    cfg = load_config()
    cfg.ia.enabled = True
    cfg.ia.api_key = "k"
    cfg.ia.model = "cloud-model"
    return cfg


def _insert(conn, gid, salary="", salary_source="", ia_model="", ai_opinion=""):
    conn.execute(
        "INSERT INTO ofertas (group_id, title, salary, salary_source, ia_model, "
        "ai_opinion, url, description, active, first_seen, last_seen) "
        "VALUES (?,?,?,?,?,?,?,?,1, datetime('now'), datetime('now'))",
        (gid, f"Titulo {gid}", salary, salary_source, ia_model, ai_opinion,
         f"http://x.cl/{gid}", "x" * 50))
    conn.commit()


# ---------------- 1. apply_ia_result lee fila fresca ----------------

def test_apply_ia_result_guard_a3_no_pisa_salary_source(mem_db):
    """Oferta con salary_source='feed' y salary='' en DB; r dict sin salary_source
    (simula SELECT viejo) — guard A3 debe leer la fila fresca y NO escribir salary."""
    _insert(mem_db, "g1", salary="", salary_source="feed")
    r = {"group_id": "g1", "title": "Titulo g1"}   # sin salary_source
    parsed = {"salario_clp_mensual": 1500000, "opinion": "x"}
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed)
    assert ok
    row = mem_db.execute("SELECT salary, salary_source FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["salary"] == ""          # guard A3: no escribio salary
    assert row["salary_source"] == "feed"


def test_apply_ia_result_sanitiza_fosil(mem_db):
    """Fila con ia_model='' y ai_opinion='vieja' (fosil de otra era); parsed sin
    opinion nueva -> ai_opinion queda limpio ('')."""
    _insert(mem_db, "g2", ia_model="", ai_opinion="vieja")
    r = {"group_id": "g2", "title": "Titulo g2"}
    parsed = {"seniority_real": "senior"}   # sin opinion
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed)
    assert ok
    row = mem_db.execute("SELECT ai_opinion FROM ofertas WHERE group_id='g2'").fetchone()
    assert row["ai_opinion"] == ""


@pytest.mark.parametrize("val,expected", [
    (2500000.0, "CLP 2500000"),
    ("2.500.000", "CLP 2500000"),
])
def test_apply_ia_result_coerce_salario(mem_db, val, expected):
    gid = f"g3-{val}"
    _insert(mem_db, gid, salary="", salary_source="")
    r = {"group_id": gid, "title": f"Titulo {gid}"}
    parsed = {"salario_clp_mensual": val}
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed)
    assert ok
    row = mem_db.execute("SELECT salary FROM ofertas WHERE group_id=?", (gid,)).fetchone()
    assert row["salary"] == expected


def test_apply_ia_result_coerce_salario_bool_no_escribe(mem_db):
    _insert(mem_db, "g4", salary="", salary_source="")
    r = {"group_id": "g4", "title": "Titulo g4"}
    parsed = {"salario_clp_mensual": True, "seniority_real": "senior"}
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed)
    assert ok   # escribe seniority, pero no salary
    row = mem_db.execute("SELECT salary, salary_source FROM ofertas WHERE group_id='g4'").fetchone()
    assert row["salary"] == ""
    assert row["salary_source"] == ""


def test_apply_ia_result_ia_model_desde_parsed(mem_db):
    """parsed['_ia_model']='x' sin model= explicito -> ia_model='x'."""
    _insert(mem_db, "g5")
    r = {"group_id": "g5", "title": "Titulo g5"}
    parsed = {"seniority_real": "senior", "_ia_model": "x"}
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed)
    assert ok
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g5'").fetchone()
    assert row["ia_model"] == "x"


def test_apply_ia_result_model_explicito_gana(mem_db):
    _insert(mem_db, "g6")
    r = {"group_id": "g6", "title": "Titulo g6"}
    parsed = {"seniority_real": "senior", "_ia_model": "x"}
    ok = en.apply_ia_result(mem_db, _cfg(), r, parsed, model="cloud-model")
    assert ok
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g6'").fetchone()
    assert row["ia_model"] == "cloud-model"


# ---------------- 2. ia_extract_lote: item basura no aborta ----------------

class _FakeResp:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_ia_extract_lote_ignora_item_no_dict(monkeypatch):
    cfg = _cfg()
    ofertas = [{"idx": 0, "opinion": "ok"}, "basura", {"idx": 1, "opinion": "ok2"}]
    payload = {"choices": [{"message": {"content": json.dumps({"ofertas": ofertas})}}]}

    def fake_post(*a, **k):
        return _FakeResp(payload)

    monkeypatch.setattr(en.requests, "post", fake_post)
    rows = [{"group_id": "g1", "title": "T1"}, {"group_id": "g2", "title": "T2"}]
    arr, err = en.ia_extract_lote(cfg, rows, "perfil")
    assert err == ""
    assert arr is not None
    assert len(arr) == 2
    assert all(isinstance(i, dict) for i in arr)


# ---------------- 3. ia_extract_local_con_fallback: modelo real ----------------

def test_local_con_fallback_cloud_etiqueta_modelo_cloud(monkeypatch):
    cfg = _cfg()
    cfg.ia.local_enabled = True
    cfg.ia.local_fallback_cloud = True
    cfg.ia.local_model = "local-model"
    monkeypatch.setattr(en, "ia_extract_local", lambda *a, **k: (None, "other"))
    monkeypatch.setattr(en, "ia_extract_detail", lambda *a, **k: ({"opinion": "x"}, ""))
    parsed, err, modelo = en.ia_extract_local_con_fallback(cfg, {"group_id": "g1"}, "perfil")
    assert parsed == {"opinion": "x"}
    assert modelo == cfg.ia.model


def test_local_con_fallback_local_ok_etiqueta_modelo_local(monkeypatch):
    cfg = _cfg()
    cfg.ia.local_enabled = True
    cfg.ia.local_model = "local-model"
    monkeypatch.setattr(en, "ia_extract_local", lambda *a, **k: ({"opinion": "x"}, ""))
    parsed, err, modelo = en.ia_extract_local_con_fallback(cfg, {"group_id": "g1"}, "perfil")
    assert parsed == {"opinion": "x"}
    assert modelo == cfg.ia.local_model


# ---------------- 4. fetch_page: 5xx -> error ----------------

class _FakeGetResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def test_fetch_page_5xx_es_error(monkeypatch):
    monkeypatch.setattr(en.requests, "get", lambda *a, **k: _FakeGetResp(503, "x" * 600))
    html, access = en.fetch_page("http://x.cl/oferta")
    assert access == "error"


# ---------------- 5. scoring.compute_score ----------------

def _mkcfg(red_keywords=None, techs=None):
    cfg = _cfg()
    cfg.profile.red_keywords = red_keywords or []
    cfg.profile.techs = techs or []
    return cfg


def test_compute_score_red_keyword_no_mata_falso_positivo():
    cfg = _mkcfg(red_keywords=["intern"])
    job = {"title": "Desarrollador internacional", "description": "x" * 100}
    score, breakdown = sc.compute_score(job, cfg)
    assert "rejected_by" not in breakdown


def test_compute_score_tech_java_no_matchea_javascript_ni_location():
    cfg = _mkcfg(techs=["java"])
    job1 = {"title": "Javascript developer", "description": "x" * 100}
    _, bd1 = sc.compute_score(job1, cfg)
    assert "techs" not in bd1
    job2 = {"title": "Backend developer", "location": "Java, Indonesia", "description": "x" * 100}
    _, bd2 = sc.compute_score(job2, cfg)
    assert "techs" not in bd2


def test_compute_score_tech_spring_boot_matchea_titulo():
    cfg = _mkcfg(techs=["spring boot"])
    job = {"title": "Spring Boot dev", "description": "x" * 100}
    _, bd = sc.compute_score(job, cfg)
    assert "techs" in bd


def test_compute_score_ingles_desde_ia_excluyente():
    cfg = _mkcfg()
    job = {"title": "Backend dev", "description": "sin mencion de idiomas aqui " * 5,
           "ai_idiomas": json.dumps([{"idioma": "ingles", "nivel": "c1", "excluyente": True}])}
    _, bd = sc.compute_score(job, cfg)
    assert "english" in bd
    assert "excluyente" in bd["english"]


def test_salary_to_clp_monthly_anual_a_mensual():
    assert sc._salary_to_clp_monthly("CLP 18000000 /ano", "") == 1500000


# ---------------- 6. channel._categorias_dev ----------------

@pytest.mark.parametrize("rc", ["Retail", "Full Time", "Data Entry", "Team Lead Ventas"])
def test_categorias_dev_no_falsos_positivos(rc):
    assert ch._categorias_dev(rc) == set()


@pytest.mark.parametrize("rc,expected", [
    ("Backend Developer", "Backend"),
    ("AI Engineer", "AI/ML"),
    ("Tech Lead", "Tech Lead"),
    ("Ingeniero de Datos", "Data"),
])
def test_categorias_dev_positivos(rc, expected):
    assert expected in ch._categorias_dev(rc)


# ---------------- 7. cli.cmd_run: lote_stop (CONC-1) ----------------

def test_cmd_run_no_rebindea_stop_event_global():
    src = inspect.getsource(cli.cmd_run)
    assert "stop_event = threading.Event()" not in src
    assert "lote_stop" in src
