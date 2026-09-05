"""Tests spec-enrich-lotes v2 §8 — modo lote IA, fetch paralelo, pool cacheado, rescate/fallback."""
import sys
sys.path.insert(0, "/mnt/data2/projects/jobhunt")

import sqlite3
import pytest
from jobhunt import enrich as en
from jobhunt import stats as _st


@pytest.fixture
def mem_db():
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _insert(conn, gid, salary="", url="http://x.cl/1", desc="x" * 50, status="", note=""):
    conn.execute("INSERT INTO ofertas (group_id, title, salary, salary_raw, salary_source, "
                 "salary_status, salary_note, url, description, active, first_seen, last_seen) "
                 "VALUES (?,?,?,?,?,?,?,?,?,1, datetime('now'), datetime('now'))",
                 (gid, f"Título {gid}", salary, salary, "feed", status, note, url, desc))
    conn.commit()


def _cfg():
    from jobhunt.config import load_config
    c = load_config()
    c.ia.batch_prompt = 5
    c.ia.local_enabled = False   # tests del modo lote cloud — independientes del .env
    return c


def _mock_detail(monkeypatch):
    """Mock de ia_extract_detail con dict válido (evita HTTP real en fallbacks)."""
    def fake_detail(cfg, r, perfil, mercado):
        return {"opinion": "x", "resumen": "r", "fit_reason": "f", "seniority_real": "s",
                "rol_categoria": "Backend", "ingles": "B2", "idiomas": [], "modalidad": "R",
                "salario_clp_mensual": 0, "red_flags": [], "green_flags": [], "benefits": []}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)


# ---------- 1. prompt de lote ----------
def test_lote_prompt_bloques_idx():
    rows = [{"title": "A", "salary": "", "salary_status": "", "salary_note": "",
             "salary_raw": "", "description": "x" * 300},
            {"title": "B", "salary": "", "salary_status": "", "salary_note": "",
             "salary_raw": "", "description": "y" * 300}]
    p = en._lote_prompt(rows, "perfil", "mercado")
    assert "--- OFERTA 1 ---" in p and "--- OFERTA 2 ---" in p
    assert "idx 1..N" in p
    assert "salario_clp_mensual = 0" in p


def test_lote_nota_anomalia_por_oferta():
    rows = [{"title": "A", "salary": "CLP 66496000", "salary_status": "implausible",
             "salary_note": "above_ceiling", "salary_raw": "CLP 66496000",
             "description": "x" * 300},
            {"title": "B", "salary": "", "salary_status": "", "salary_note": "",
             "salary_raw": "", "description": "y" * 300}]
    p = en._lote_prompt(rows, "perfil", "mercado")
    assert "anómalo" in p and "probable cifra anual" in p
    # la anomalía aparece SOLO en el bloque 1, no en el 2
    bloque2 = p.split("--- OFERTA 2 ---")[1]
    assert "anómalo" not in bloque2


# ---------- 2. schema ----------
def test_schema_estructura():
    s = en._LOTE_SCHEMA
    item = s["properties"]["ofertas"]["items"]
    props = item["properties"]
    assert props["idx"]["type"] == "integer"
    assert "fit_reason" in props and "ingles" in props and "benefits" in props
    assert "techs" in props          # v2: la IA detecta techs y se persisten
    assert item["additionalProperties"] is False
    assert "idiomas" in item["required"]


def test_lote_paridad_campos():
    """Paridad lote vs individual: todo lo que apply_ia_result escribe debe estar en el schema."""
    s = en._LOTE_SCHEMA
    props = s["properties"]["ofertas"]["items"]["properties"]
    # campos que apply_ia_result escribe (enrich.py:760-792)
    for campo in ("opinion", "resumen", "fit_reason", "seniority_real", "rol_categoria",
                  "ingles", "idiomas", "modalidad", "salario_clp_mensual",
                  "red_flags", "green_flags", "benefits"):
        assert campo in props, f"falta {campo} en el schema"


# ---------- 3. mapeo idx ----------
def test_lote_mapeo_idx_correcto():
    rows = [{"group_id": f"g{i}"} for i in range(1, 6)]
    arr = [{"idx": i, "opinion": f"op{i}"} for i in range(1, 6)]
    idxs = {}
    for item in arr:
        k = int(item.get("idx") or 0)
        if k not in idxs:
            idxs[k] = item
    for i, r in enumerate(rows, 1):
        assert idxs[i]["opinion"] == f"op{i}"


def test_lote_idx_fuera_rango():
    arr = [{"idx": 99, "opinion": "x"}]
    idxs = {}
    for item in arr:
        k = int(item.get("idx") or 0)
        if k not in idxs:
            idxs[k] = item
    # idx 99 no mapea a ninguna fila 1..N → se cuenta como faltante
    assert 99 not in {1, 2, 3, 4, 5}


def test_lote_idx_duplicado():
    arr = [{"idx": 1, "opinion": "primero"}, {"idx": 1, "opinion": "segundo"}]
    idxs = {}
    for item in arr:
        k = int(item.get("idx") or 0)
        if k not in idxs:
            idxs[k] = item
    assert idxs[1]["opinion"] == "primero"   # gana el primero


# ---------- 4. rescate y fallback ----------
def test_lote_array_incompleto_rescate(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 6):
        _insert(mem_db, f"g{i}")
    # mock: lote devuelve 4 de 5 (falta idx 3)
    def fake_lote(cfg, rows, perfil, mercado):
        return [{"idx": 1, "opinion": "a", "resumen": "r", "fit_reason": "f",
                 "seniority_real": "s", "rol_categoria": "Backend", "ingles": "B2",
                 "idiomas": [], "modalidad": "R", "salario_clp_mensual": 0,
                 "red_flags": [], "green_flags": [], "benefits": []},
                {"idx": 2, "opinion": "b", "resumen": "r", "fit_reason": "f",
                 "seniority_real": "s", "rol_categoria": "Backend", "ingles": "B2",
                 "idiomas": [], "modalidad": "R", "salario_clp_mensual": 0,
                 "red_flags": [], "green_flags": [], "benefits": []},
                {"idx": 4, "opinion": "d", "resumen": "r", "fit_reason": "f",
                 "seniority_real": "s", "rol_categoria": "Backend", "ingles": "B2",
                 "idiomas": [], "modalidad": "R", "salario_clp_mensual": 0,
                 "red_flags": [], "green_flags": [], "benefits": []},
                {"idx": 5, "opinion": "e", "resumen": "r", "fit_reason": "f",
                 "seniority_real": "s", "rol_categoria": "Backend", "ingles": "B2",
                 "idiomas": [], "modalidad": "R", "salario_clp_mensual": 0,
                 "red_flags": [], "green_flags": [], "benefits": []}], ""
    monkeypatch.setattr(en, "ia_extract_lote", fake_lote)
    # la oferta 3 (faltante) se reprocesa individual
    monkeypatch.setattr(en, "ia_extract_detail",
                        lambda cfg, r, perfil, mercado: ({"opinion": "c", "resumen": "r",
                                                          "fit_reason": "f", "seniority_real": "s",
                                                          "rol_categoria": "Backend", "ingles": "B2",
                                                          "idiomas": [], "modalidad": "R",
                                                          "salario_clp_mensual": 0,
                                                          "red_flags": [], "green_flags": [],
                                                          "benefits": []}, ""))
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=5)
    assert done == 5
    ops = [r["ai_opinion"] for r in mem_db.execute(
        "SELECT ai_opinion FROM ofertas ORDER BY group_id")]
    assert ops == ["a", "b", "c", "d", "e"]


def test_lote_fallback_individual(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 4):
        _insert(mem_db, f"g{i}")
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: (None, "timeout"))
    calls = []
    def fake_detail(cfg, r, perfil, mercado):
        calls.append(r["group_id"])
        return {"opinion": "x", "resumen": "r", "fit_reason": "f", "seniority_real": "s",
                "rol_categoria": "Backend", "ingles": "B2", "idiomas": [], "modalidad": "R",
                "salario_clp_mensual": 0, "red_flags": [], "green_flags": [], "benefits": []}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=3)
    assert done == 3
    assert len(calls) == 3   # fallback individual para las 3


def test_lote_array_vacio_fallback(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 3):
        _insert(mem_db, f"g{i}")
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    calls = []
    def fake_detail(cfg, r, perfil, mercado):
        calls.append(r["group_id"])
        return {"opinion": "x", "resumen": "r", "fit_reason": "f", "seniority_real": "s",
                "rol_categoria": "Backend", "ingles": "B2", "idiomas": [], "modalidad": "R",
                "salario_clp_mensual": 0, "red_flags": [], "green_flags": [], "benefits": []}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=2)
    assert done == 2
    assert len(calls) == 2


def test_lote_rate_no_fragmenta(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 3):
        _insert(mem_db, f"g{i}")
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: (None, "rate"))
    calls = []
    monkeypatch.setattr(en, "ia_extract_detail",
                        lambda cfg, r, perfil, mercado: (calls.append(r["group_id"]) or
                                                         ({"opinion": "x"}, "")))
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=2)
    assert done == 2          # fichas aplicadas
    assert calls == []        # NO fragmenta en individuales (rate)


# ---------- 5. fetch paralelo ----------
def test_fetch_paralelo_grupo(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 6):
        _insert(mem_db, f"g{i}")
    seen = []
    def fake_fetch(r):
        seen.append(r["group_id"])
        return {"description": f"desc {r['group_id']}", "_access": "ok"}
    monkeypatch.setattr(en, "_fetch_ficha", fake_fetch)
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    _mock_detail(monkeypatch)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=5)
    assert done == 5
    assert len(seen) == 5


def test_fetch_paralelo_threads_no_db(mem_db, monkeypatch):
    """Los threads del fetch NO tocan la DB — _fetch_ficha es HTTP puro."""
    cfg = _cfg()
    for i in range(1, 3):
        _insert(mem_db, f"g{i}")
    import threading
    main_tid = threading.get_ident()
    tids = []
    def fake_fetch(r):
        tids.append(threading.get_ident())
        return {"description": "x" * 500, "_access": "ok"}
    monkeypatch.setattr(en, "_fetch_ficha", fake_fetch)
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    _mock_detail(monkeypatch)
    en.enrich_pending(mem_db, cfg, max_n=2)
    assert len(tids) == 2
    assert any(t != main_tid for t in tids)   # al menos uno corrió en otro thread


# ---------- 6. pool cacheado ----------
def test_pool_cacheado_una_vez(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 4):
        _insert(mem_db, f"g{i}", salary=f"CLP {1500000 + i * 100000}")
    calls = []
    orig = en.get_salary_pool
    def counting(conn):
        calls.append(1)
        return orig(conn)
    monkeypatch.setattr(en, "get_salary_pool", counting)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    _mock_detail(monkeypatch)
    en.enrich_pending(mem_db, cfg, max_n=3)
    assert len(calls) == 1   # UNA vez por invocación


def test_pool_loo(mem_db, monkeypatch):
    """Leave-one-out: _aplicar_ficha excluye el valor evaluado del pool al clasificar."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="CLP 2000000")
    _insert(mem_db, "g2", salary="CLP 2100000")
    _insert(mem_db, "g3", salary="CLP 2200000")
    captured = {}
    orig = _st.classify_salary
    def spy(v, pool):
        captured["v"] = v
        captured["pool"] = list(pool)
        return orig(v, pool)
    monkeypatch.setattr(_st, "classify_salary", spy)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok",
                                     "salary": "CLP 2000000"})
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    _mock_detail(monkeypatch)
    en.enrich_pending(mem_db, cfg, max_n=3)
    # el pool usado NO contiene el valor evaluado (2M)
    assert captured["v"] == 2_000_000
    assert 2_000_000 not in captured["pool"]
    assert 2_100_000 in captured["pool"] and 2_200_000 in captured["pool"]


# ---------- 7. recarga N' ----------
def test_recarga_n_menor(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 4):
        _insert(mem_db, f"g{i}")
    # g2 expira en el fetch → recarga devuelve 2 (g1, g3) → prompt con idx 1..2
    def fake_fetch(r):
        if r["group_id"] == "g2":
            return {"_access": "not_found"}
        return {"description": "x" * 500, "_access": "ok"}
    monkeypatch.setattr(en, "_fetch_ficha", fake_fetch)
    seen = []
    def fake_lote(cfg, rows, perfil, mercado):
        seen.append([r["group_id"] for r in rows])
        return [], ""
    monkeypatch.setattr(en, "ia_extract_lote", fake_lote)
    _mock_detail(monkeypatch)
    done = en.enrich_pending(mem_db, cfg, max_n=3)
    assert done == 3
    assert seen and seen[0] == ["g1", "g3"]   # N'=2, sin la expirada


# ---------- 8. stop y ctx_version ----------
def test_stop_entre_grupos(mem_db, monkeypatch):
    cfg = _cfg()
    for i in range(1, 6):
        _insert(mem_db, f"g{i}")
    import threading
    ev = threading.Event()
    ev.set()   # stop ya solicitado
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote", lambda *a, **k: ([], ""))
    _mock_detail(monkeypatch)
    done = en.enrich_pending(mem_db, cfg, max_n=5, stop_event=ev)
    assert done == 0   # corte ANTES del primer grupo


def test_ctx_version_enrich(mem_db, monkeypatch):
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: ([{"idx": 1, "opinion": "x", "resumen": "r",
                                           "fit_reason": "f", "seniority_real": "s",
                                           "rol_categoria": "Backend", "ingles": "B2",
                                           "idiomas": [], "modalidad": "R",
                                           "salario_clp_mensual": 0,
                                           "red_flags": [], "green_flags": [],
                                           "benefits": []}], ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    cv = mem_db.execute("SELECT ctx_version FROM ofertas WHERE group_id='g1'").fetchone()[0]
    assert cv.startswith("ctx-") and len(cv) == 12


# ---------- 9. knob ----------
def test_knob_batch_prompt_1(mem_db, monkeypatch):
    cfg = _cfg()
    cfg.ia.batch_prompt = 1   # modo individual
    for i in range(1, 3):
        _insert(mem_db, f"g{i}")
    lote_calls = []
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: (lote_calls.append(1) or ([], "")))
    _mock_detail(monkeypatch)
    calls = []
    def fake_detail(cfg, r, perfil, mercado):
        calls.append(r["group_id"])
        return {"opinion": "x", "resumen": "r", "fit_reason": "f", "seniority_real": "s",
                "rol_categoria": "Backend", "ingles": "B2", "idiomas": [], "modalidad": "R",
                "salario_clp_mensual": 0, "red_flags": [], "green_flags": [], "benefits": []}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    done = en.enrich_pending(mem_db, cfg, max_n=2)
    assert done == 2
    assert lote_calls == []      # sin llamadas de lote
    assert len(calls) == 2       # individuales


# ---------- 10. salario 0 no escribe ----------
def test_lote_salario_null_no_escribe(mem_db, monkeypatch):
    cfg = _cfg()
    _insert(mem_db, "g1", salary="")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: ([{"idx": 1, "opinion": "x", "resumen": "r",
                                           "fit_reason": "f", "seniority_real": "s",
                                           "rol_categoria": "Backend", "ingles": "B2",
                                           "idiomas": [], "modalidad": "R",
                                           "salario_clp_mensual": 0,   # centinela
                                           "red_flags": [], "green_flags": [],
                                           "benefits": []}], ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    sal = mem_db.execute("SELECT salary FROM ofertas WHERE group_id='g1'").fetchone()[0]
    assert sal == ""   # 0 no escribe salary


# ---------- 11. techs de la IA se persisten (cobertura 15%→~80%) ----------
def test_lote_techs_ia_se_persisten(mem_db, monkeypatch):
    cfg = _cfg()
    _insert(mem_db, "g1", salary="")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: ([{"idx": 1, "opinion": "x", "resumen": "r",
                                           "fit_reason": "f", "seniority_real": "s",
                                           "rol_categoria": "Backend", "ingles": "B2",
                                           "idiomas": [], "modalidad": "R",
                                           "salario_clp_mensual": 0,
                                           "techs": ["Python", "Kubernetes", "AWS"],
                                           "red_flags": [], "green_flags": [],
                                           "benefits": []}], ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT techs, ia_fields FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["techs"] == "Py;K8s;AWS"      # normalizadas a abreviaturas
    assert "techs" in (row["ia_fields"] or "")


def test_lote_techs_ia_refresca_existente(mem_db, monkeypatch):
    """La IA REFRESCA techs aunque la columna tenga valor (run anterior pudo fallar)."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="")
    mem_db.execute("UPDATE ofertas SET techs='Java;Spring' WHERE group_id='g1'")
    mem_db.commit()
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: ([{"idx": 1, "opinion": "x", "resumen": "r",
                                           "fit_reason": "f", "seniority_real": "s",
                                           "rol_categoria": "Backend", "ingles": "B2",
                                           "idiomas": [], "modalidad": "R",
                                           "salario_clp_mensual": 0,
                                           "techs": ["Python", "AWS"],
                                           "red_flags": [], "green_flags": [],
                                           "benefits": []}], ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    techs = mem_db.execute("SELECT techs FROM ofertas WHERE group_id='g1'").fetchone()[0]
    assert techs == "Py;AWS"   # refrescado por la IA


def test_lote_techs_ia_vacia_no_toca(mem_db, monkeypatch):
    """Si la IA no detecta techs ([]), la columna existente se preserva."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="")
    mem_db.execute("UPDATE ofertas SET techs='Java;Spring' WHERE group_id='g1'")
    mem_db.commit()
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: ([{"idx": 1, "opinion": "x", "resumen": "r",
                                           "fit_reason": "f", "seniority_real": "s",
                                           "rol_categoria": "Backend", "ingles": "B2",
                                           "idiomas": [], "modalidad": "R",
                                           "salario_clp_mensual": 0,
                                           "techs": [],
                                           "red_flags": [], "green_flags": [],
                                           "benefits": []}], ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    techs = mem_db.execute("SELECT techs FROM ofertas WHERE group_id='g1'").fetchone()[0]
    assert techs == "Java;Spring"   # intacto
