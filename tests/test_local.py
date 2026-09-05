"""Tests spec-ia-local v2.1 §7 — pipeline local (2 tareas), fallback cloud, knobs."""
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
    """El prompt EXTRACT pide schema plano (sin anidar) y NO lista tecnologías (anti-halluc)."""
    assert "FORMATO EXACTO DE SALIDA" in en._PROMPT_EXTRACT_LOCAL
    assert "salario_clp_mensual" in en._PROMPT_EXTRACT_LOCAL
    # V2: sin lista de 30 tecnologías (la lista del V1 era fuente de alucinaciones)
    assert "Py, Java, TS, JS" not in en._PROMPT_EXTRACT_LOCAL
    assert "NUNCA agregues otras" in en._PROMPT_EXTRACT_LOCAL


def test_local_opinion_anti_alucinacion():
    """El prompt OPINION contiene las reglas anti-alucinación."""
    assert "ANTI-ALUCINACIÓN" in en._PROMPT_OPINION_LOCAL
    assert "únicos números de mercado" in en._PROMPT_OPINION_LOCAL
    assert "NUNCA corrijas ni omitas" in en._PROMPT_OPINION_LOCAL


def test_local_opinion_anti_relleno():
    """El prompt OPINION prohíbe frases genéricas y pide inferir de lo disponible."""
    assert "frases genéricas de relleno" in en._PROMPT_OPINION_LOCAL
    assert "oferta fuera de rango" in en._PROMPT_OPINION_LOCAL
    assert "descripción es corta o ausente" in en._PROMPT_OPINION_LOCAL
    assert "max 300 chars" in en._PROMPT_OPINION_LOCAL


# ---------- 5. knobs ----------

def test_local_concurrency_knob():
    from jobhunt.config import load_config
    cfg = load_config()
    assert 1 <= cfg.ia.local_concurrency <= 6


def test_local_timeout_knob():
    from jobhunt.config import load_config
    cfg = load_config()
    assert cfg.ia.local_timeout >= 60   # 120 en .env (fallback cloud actúa rápido)


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
    # IA-1 (auditoría): el fallback cloud etiqueta el modelo REAL (cloud), no local_model
    assert row["ia_model"] == cfg.ia.model


# ---------- 10. spec-techs-dev-gate: _extract_techs + guard + regla salario ----------

def test_techs_planificacion_no_nifi():
    """'planificación' NO produce NiFi (word boundaries — bug raíz)."""
    assert en._extract_techs("ingeniero especialista", "planificación y control minero") == []


def test_techs_cargo_no_go():
    """'cargo/gobierno/gestionar' NO producen Go."""
    assert en._extract_techs("ingeniero", "cargo de jefe, gobierno corporativo, gestionar") == []


def test_techs_go_live_no_go():
    """'go live'/'go to market' NO producen Go (lookahead — P1-4)."""
    assert en._extract_techs("PM", "plan de go live y go to market") == []


def test_techs_javascript_js():
    """'JavaScript Developer'/'JS' → JS (P1-3)."""
    assert "JS" in en._extract_techs("JavaScript Developer", "")
    assert "JS" in en._extract_techs("", "experiencia con JS")


def test_techs_concatenadas():
    """Grafías concatenadas detectadas (P1-3): nodejs, reactjs, mongodb, dotnet, python3, ci-cd."""
    t = en._extract_techs("", "nodejs reactjs mongodb springboot dotnet python3 ci-cd")
    for ab in ("Node", "React", "Mongo", "Spring", ".NET", "Py", "CI/CD"):
        assert ab in t, f"falta {ab}"


def test_techs_titulo_java():
    """Título primero: 'Senior Java Developer' → Java."""
    assert "Java" in en._extract_techs("Senior Java Developer", "sin stack en desc")


def test_techs_feed_preservado(mem_db, monkeypatch):
    """Feed con techs + IA devuelve [] → columna LIMPIA (decisión usuario: techs
    SIEMPRE se regeneran con IA — [] limpia, no preserva)."""
    cfg = _cfg()
    _insert(mem_db, "g1", desc="x" * 50)
    mem_db.execute("UPDATE ofertas SET techs='Py;AWS' WHERE group_id='g1'")
    mem_db.commit()
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    monkeypatch.setattr(en, "_llm_local",
                        lambda *a, **k: ({**_resp_extract(), **_resp_opinion(), "techs": []}, ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT techs FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["techs"] == ""   # IA devolvió [] → limpia (no preserva)


def test_guard_rol_no_software_limpia_techs(mem_db, monkeypatch):
    """IA devuelve techs + rol no-software → techs='' (guard DESPUÉS del REFRESCA — P1-1)."""
    cfg = _cfg()
    _insert(mem_db, "g1", desc="x" * 50)
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    def fake_local(cfg, job, perfil, mercado):
        d = {**_resp_extract(), **_resp_opinion()}
        d["techs"] = ["NiFi", "Go"]          # alucinación
        d["rol_categoria"] = "Ingeniería no-software"
        return d, ""
    monkeypatch.setattr(en, "ia_extract_local", fake_local)
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT techs FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["techs"] == ""   # el guard pisó la lista alucinada


def test_salario_llega_desmarca_ia_model(mem_db, monkeypatch):
    """Regla §2.5: salary ''→valor + opinion 'sin salario' → se desmarca y el
    enrich la regenera con el dato nuevo (la opinion ya no dice 'sin salario')."""
    cfg = _cfg()
    _insert(mem_db, "g1", desc="x" * 50)
    mem_db.execute("UPDATE ofertas SET ia_model='deepseek-v4-flash:0731-cloud', "
                   "ai_opinion='Sin sueldo declarado; no hay con qué comparar' WHERE group_id='g1'")
    mem_db.commit()
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok",
                                     "salary": "CLP 2400000"})
    monkeypatch.setattr(en, "_llm_local",
                        lambda *a, **k: ({**_resp_extract(), **_resp_opinion()}, ""))
    en.enrich_pending(mem_db, cfg, max_n=1)
    row = mem_db.execute("SELECT ia_model, salary, ai_opinion FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["salary"] != ""          # el árbitro escribió el salario
    assert row["ia_model"] != ""        # re-enriquecida (la regla la desmarcó y el enrich la regeneró)
    assert "sin sueldo" not in (row["ai_opinion"] or "").lower()   # opinion nueva con el dato


def test_salario_sin_cambio_no_desmarca(mem_db):
    """Regla §2.5: salary ya presente → NO desmarca (sin loop — P1-7).
    Prueba directa de _aplicar_ficha: r con salary ya escrito + opinion 'sin
    salario' → la regla NO dispara (no hubo transición ''→valor)."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="CLP 2400000", desc="x" * 50)
    mem_db.execute("UPDATE ofertas SET ia_model='qwen2.5:7b', "
                   "ai_opinion='Sin sueldo declarado' WHERE group_id='g1'")
    mem_db.commit()
    r = {"group_id": "g1", "title": "Dev", "salary": "CLP 2400000",   # salary YA presente
         "ai_opinion": "Sin sueldo declarado", "ia_model": "qwen2.5:7b"}
    info = {"_access": "ok", "description": "x" * 500, "salary": "CLP 2400000",
            "techs_desc": [], "company": "", "modality_badge": "", "date_posted": "",
            "valid_through": "", "years_official": None, "remote_official": None,
            "employment_type": ""}
    pool = [2_000_000, 2_100_000, 2_200_000]
    en._aplicar_ficha(mem_db, r, info, pool, cfg)
    row = mem_db.execute("SELECT ia_model FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["ia_model"] == "qwen2.5:7b"   # no se desmarcó (no hubo transición ''→valor)


def test_c9_extendido_incluye_opinion_sin_salario(mem_db):
    """C9 extendido (P1-6): salary presente + opinion 'sin salario' → califica."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="CLP 2400000", desc="x" * 500)
    mem_db.execute("UPDATE ofertas SET modality='remoto', "
                   "ai_opinion='Sin salario declarado' WHERE group_id='g1'")
    mem_db.commit()
    assert en.ia_queue_count(mem_db) == 1   # C9 extendido la recoge


def test_c9_normal_excluye_completa(mem_db):
    """C9 normal: oferta completa (salary+modality+opinion con salario) NO califica."""
    cfg = _cfg()
    _insert(mem_db, "g1", salary="CLP 2400000", desc="x" * 500)
    mem_db.execute("UPDATE ofertas SET modality='remoto', "
                   "ai_opinion='Sueldo alineado con la mediana' WHERE group_id='g1'")
    mem_db.commit()
    assert en.ia_queue_count(mem_db) == 0

def test_enrich_all_incluye_completas(mem_db, monkeypatch):
    """all_pending=True → incluye ofertas COMPLETAS (modality+salary llenos)
    que la cola C9 normal NO procesaría, y NO limita a batch_size (40)."""
    cfg = _cfg()
    cfg.ia.local_enabled = False
    # oferta completa (no califica a C9: modality y salary llenos)
    _insert(mem_db, "g_completa", salary="CLP 2500000", desc="x" * 500)
    mem_db.execute("UPDATE ofertas SET modality='remoto' WHERE group_id='g_completa'")
    # oferta incompleta (sí califica a C9)
    _insert(mem_db, "g_incompleta", desc="x" * 500)
    mem_db.commit()
    monkeypatch.setattr(en, "extract_structured",
                        lambda url: {"description": "x" * 500, "_access": "ok"})
    vistos = []
    def fake_lote(cfg, rows, perfil, mercado):
        vistos.extend(r["group_id"] for r in rows)
        return [{"idx": i + 1, "opinion": "o", "resumen": "r", "fit_reason": "f",
                 "seniority_real": "s", "rol_categoria": "Backend", "ingles": "B2",
                 "idiomas": [], "modalidad": "R", "salario_clp_mensual": 0,
                 "red_flags": [], "green_flags": [], "benefits": []}
                for i in range(len(rows))], ""
    monkeypatch.setattr(en, "ia_extract_lote", fake_lote)
    # C9 normal: solo la incompleta
    en.run_ia_batch(mem_db, cfg, "perfil", max_n=10)
    assert vistos == ["g_incompleta"]
    vistos.clear()
    # reset: ambas vuelven a estar sin IA (la 1ª pasada las marcó)
    mem_db.execute("UPDATE ofertas SET ia_model=''")
    mem_db.commit()
    # all_pending: ambas (la completa también) — sin tope de batch_size
    en.run_ia_batch(mem_db, cfg, "perfil", all_pending=True)
    assert sorted(vistos) == ["g_completa", "g_incompleta"]

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


def test_clean_text_unescape_html():
    """_clean_text: unescape entidades HTML rotas del modelo 1-2bit."""
    assert en._clean_text("m&eacute;tricas") == "métricas"
    assert en._clean_text("automatizaci&oacute;n") == "automatización"
    assert en._clean_text("Ingenier&iacute;a Inform&aacute;tica") == "Ingeniería Informática"
    assert en._clean_text(None) == ""
    assert en._clean_text("  Java  ") == "Java"
    # truncado con … sobre el texto REAL (post-unescape)
    t = en._clean_text("a" * 25, 20)
    assert len(t) == 20 and t.endswith("…")
    en._clean_text("Sueldo sobre la mediana del mercado; empresa reconocida.", 300)


def test_clean_tech_filtra_conceptos():
    """_clean_tech: elimina conceptos no-tecnología y entidades partidas."""
    assert en._clean_tech("Java") == "Java"
    assert en._clean_tech("  Py  ") == "Py"
    assert en._clean_tech("CI") == ""
    assert en._clean_tech("AI") == ""
    assert en._clean_tech("Backend") == ""
    assert en._clean_tech("Full Stack") == ""
    assert en._clean_tech("Microservicios") == ""
    assert en._clean_tech("integraci&oacute") == ""  # entidad partida sin ;
    assert en._clean_tech("m&eacute;tricas") == "métricas"
    assert en._clean_tech("Inteligencia Artific") == ""  # truncado a medias
    assert en._clean_tech("REST") == ""
    assert en._clean_tech("devops") == ""  # case-insensitive
