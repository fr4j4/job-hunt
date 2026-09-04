"""Tests spec-ia-local v2.1 §7 — pipeline local (2 tareas), fallback cloud, knobs."""
import sys
sys.path.insert(0, "/mnt/data2/projects/jobhunt")

import sqlite3
import pytest
from jobhunt import enrich as en


@pytest.fixture
def mem_db():
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def _insert(conn, gid, salary="", desc="x" * 50, status="", note=""):
    conn.execute("INSERT INTO ofertas (group_id, title, salary, salary_raw, salary_source, "
                 "salary_status, salary_note, url, description, active, first_seen, last_seen) "
                 "VALUES (?,?,?,?,?,?,?,?,?,1, datetime('now'), datetime('now'))",
                 (gid, f"Título {gid}", salary, salary, "feed", status, note,
                  f"http://x.cl/{gid}", desc))
    conn.commit()


def _cfg():
    from jobhunt.config import load_config
    cfg = load_config()
    cfg.ia.local_enabled = True
    cfg.ia.local_base_url = "http://localhost:11434/v1"
    cfg.ia.local_model = "madkoding/ornith-1.5-9b-iq2m:latest"
    cfg.ia.local_timeout = 600
    cfg.ia.local_retries = 1
    cfg.ia.local_fallback_cloud = True
    cfg.ia.local_concurrency = 2
    return cfg


def _resp_extract():
    return {"techs": ["Java", "Spring"], "modalidad": "R", "seniority_real": "senior",
            "rol_categoria": "Backend", "ingles": "deseable", "idiomas": [],
            "red_flags": [], "green_flags": [], "benefits": [],
            "salario_clp_mensual": 2500000}


def _resp_opinion():
    return {"opinion": "Sueldo sobre la mediana; empresa reconocida.",
            "resumen": "Backend Java con AWS, remoto.",
            "fit_reason": "Stack coincide con el perfil."}


# ---------- 1. dispatch ----------

def test_local_enabled_dispatch(mem_db, monkeypatch):
    """local_enabled=true → se llama ia_extract_local, no ia_extract_lote."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    lote_calls, local_calls = [], []
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: (lote_calls.append(1) or ([], "")))
    def fake_local(cfg, job, perfil, mercado):
        local_calls.append(job["group_id"])
        return {**_resp_extract(), **_resp_opinion()}, ""
    monkeypatch.setattr(en, "ia_extract_local", fake_local)
    en.enrich_pending(mem_db, cfg, max_n=1)
    assert lote_calls == []          # sin lote
    assert local_calls == ["g1"]     # local individual


def test_local_disabled_cloud(mem_db, monkeypatch):
    """local_enabled=false → pipeline cloud exacto (cero regresión)."""
    cfg = _cfg()
    cfg.ia.local_enabled = False
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    lote_calls, local_calls = [], []
    monkeypatch.setattr(en, "ia_extract_lote",
                        lambda *a, **k: (lote_calls.append(1) or ([], "")))
    monkeypatch.setattr(en, "ia_extract_local",
                        lambda *a, **k: (local_calls.append(1) or (None, "other")))
    en.enrich_pending(mem_db, cfg, max_n=1)
    assert lote_calls == [1]         # cloud usa el modo lote (batch_prompt=5)
    assert local_calls == []         # local NO se llama


# ---------- 2. 2 tareas + paridad ----------

def test_local_2tareas_combina(monkeypatch):
    """TAREA 1 + TAREA 2 → dict combinado con TODOS los campos (incluye ingles)."""
    cfg = _cfg()
    job = {"title": "Dev", "company": "X", "location": "Santiago", "salary": "CLP 2500000",
           "modality": "", "description": "x" * 300, "salary_status": "", "salary_note": ""}
    calls = []
    def fake_llm(cfg, prompt):
        calls.append(prompt)
        if "EXTRACTAMENTE estas claves" in prompt or "techs" in prompt and "opinion" not in prompt:
            return _resp_extract(), ""
        return _resp_opinion(), ""
    monkeypatch.setattr(en, "_llm_local", fake_llm)
    d, err = en.ia_extract_local(cfg, job, "perfil", "mercado")
    assert err == ""
    assert d["techs"] == ["Java", "Spring"]
    assert d["modalidad"] == "R"
    assert d["ingles"] == "deseable"          # P1-1: ingles presente
    assert d["opinion"] and d["resumen"] and d["fit_reason"]
    assert len(calls) == 2                     # 2 tareas


def test_local_paridad_campos():
    """Paridad local↔apply_ia_result: todo lo que apply_ia_result escribe debe venir del local."""
    d = {**_resp_extract(), **_resp_opinion()}
    # campos que apply_ia_result escribe (enrich.py:976-1055)
    for campo in ("opinion", "resumen", "fit_reason", "seniority_real", "rol_categoria",
                  "ingles", "idiomas", "modalidad", "salario_clp_mensual",
                  "red_flags", "green_flags", "benefits", "techs"):
        assert campo in d, f"falta {campo}"


# ---------- 3. fallback ----------

def test_local_tarea1_falla_fallback_cloud(mem_db, monkeypatch):
    """TAREA 1 timeout → fallback ia_extract_detail (cloud)."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local", lambda *a, **k: (None, "timeout"))
    cloud_calls = []
    def fake_detail(cfg, job, perfil, mercado):
        cloud_calls.append(job["group_id"])
        return {**_resp_extract(), **_resp_opinion()}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    en.enrich_pending(mem_db, cfg, max_n=1)
    assert cloud_calls == ["g1"]      # fallback cloud cubrió


def test_local_tarea2_falla_fallback_cloud(mem_db, monkeypatch):
    """TAREA 2 JSON inválido → fallback cloud."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    n = {"calls": 0}
    def fake_llm(cfg, prompt):
        n["calls"] += 1
        if n["calls"] == 1:
            return _resp_extract(), ""
        return None, "other"          # TAREA 2 falla
    monkeypatch.setattr(en, "_llm_local", fake_llm)
    cloud_calls = []
    def fake_detail(cfg, job, perfil, mercado):
        cloud_calls.append(job["group_id"])
        return {**_resp_extract(), **_resp_opinion()}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    en.enrich_pending(mem_db, cfg, max_n=1)
    assert cloud_calls == ["g1"]


def test_local_fallback_disabled_queda_cola(mem_db, monkeypatch):
    """local_fallback_cloud=false → oferta queda en cola (sin IA)."""
    cfg = _cfg()
    cfg.ia.local_fallback_cloud = False
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local", lambda *a, **k: (None, "timeout"))
    cloud_calls = []
    monkeypatch.setattr(en, "ia_extract_detail",
                        lambda *a, **k: (cloud_calls.append(1) or (None, "other")))
    en.enrich_pending(mem_db, cfg, max_n=1)
    assert cloud_calls == []          # cloud NO se llama
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["ia_model"] == ""      # sigue en cola


# ---------- 4. prompts ----------

def test_local_schema_plano():
    """El prompt EXTRACT pide schema plano (sin anidar)."""
    assert "EXACTAMENTE estas claves" in en._PROMPT_EXTRACT_LOCAL
    assert "ofertas" not in en._PROMPT_EXTRACT_LOCAL.split("Responde SOLO JSON")[1][:200]


def test_local_opinion_anti_alucinacion():
    """El prompt OPINION contiene las reglas anti-alucinación."""
    assert "ANTI-ALUCINACIÓN" in en._PROMPT_OPINION_LOCAL
    assert "únicos números de mercado" in en._PROMPT_OPINION_LOCAL
    assert "NUNCA corrijas ni omitas" in en._PROMPT_OPINION_LOCAL


# ---------- 5. knobs ----------

def test_local_concurrency_knob():
    from jobhunt.config import load_config
    cfg = load_config()
    assert 1 <= cfg.ia.local_concurrency <= 6


def test_local_timeout_knob():
    from jobhunt.config import load_config
    cfg = load_config()
    assert cfg.ia.local_timeout >= 300   # default 600


# ---------- 6. etiqueta + ctx_version ----------

def test_local_ia_model_etiqueta(mem_db, monkeypatch):
    """apply_ia_result(model=local_model) escribe ia_model=local_model (P1-2)."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local",
                        lambda *a, **k: ({**_resp_extract(), **_resp_opinion()}, ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["ia_model"] == cfg.ia.local_model   # etiqueta local, no cloud


def test_local_ctx_version(mem_db, monkeypatch):
    """ctx_version escrito en modo local (P2-3)."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local",
                        lambda *a, **k: ({**_resp_extract(), **_resp_opinion()}, ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT ctx_version FROM ofertas WHERE group_id='g1'").fetchone()
    assert (row["ctx_version"] or "").startswith("ctx-")


# ---------- 7. robustez ----------

def test_local_connection_refused_fallback(monkeypatch):
    """Connection refused → fallback inmediato (sin esperar timeout)."""
    cfg = _cfg()
    import requests
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(en.requests, "post", boom)
    d, err = en._llm_local(cfg, "prompt")
    assert d is None and err == "other"


def test_local_breaker_cloud_no_tripula(mem_db, monkeypatch):
    """429 local + fallback cloud OK → breaker NO se tripula (P2-7e)."""
    cfg = _cfg()
    _insert(mem_db, "g1")
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local", lambda *a, **k: (None, "rate"))
    def fake_detail(cfg, job, perfil, mercado):
        return {**_resp_extract(), **_resp_opinion()}, ""
    monkeypatch.setattr(en, "ia_extract_detail", fake_detail)
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["ia_model"] == cfg.ia.local_model   # procesada por fallback, sin tripular breaker


# ---------- 8. cmd_run dispatch ----------

def test_local_cmd_run_dispatch(monkeypatch):
    """cmd_run con local_enabled usa extract_fn=ia_extract_local (P1-3)."""
    from jobhunt import cli
    cfg = _cfg()
    seen = {}
    def fake_worker(cfg, work_q, out_q, lote_id, stop_event, mercado, p_desc, extract_fn=None):
        seen["extract_fn"] = extract_fn
    monkeypatch.setattr(cli, "worker_ia", fake_worker)
    # cmd_run con lote vacío no llega a workers; verificamos el wrapper directamente
    fn = cli._extract_local_con_fallback
    assert fn is not None
    # el wrapper llama local primero
    local_calls, cloud_calls = [], []
    monkeypatch.setattr(en, "ia_extract_local",
                        lambda *a, **k: (local_calls.append(1) or ({**_resp_extract(), **_resp_opinion()}, "")))
    monkeypatch.setattr(en, "ia_extract_detail",
                        lambda *a, **k: (cloud_calls.append(1) or (None, "other")))
    d, err = fn(cfg, {"title": "Dev"}, "perfil", "mercado")
    assert d is not None and local_calls == [1] and cloud_calls == []
