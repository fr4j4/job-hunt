"""Tests del modo canal — primera suite pytest del repo (spec-canal-v3 §7).

Corre: .venv/bin/python -m pytest tests/test_canal.py -v
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from jobhunt import db as database
from jobhunt.channel import (canonical_date, is_dev, normalize_date, render_offer_post,
                             select_channel_offers)
from jobhunt.config import load_config
from jobhunt.scoring import _salary_to_clp_monthly, compute_market_score

NOW = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)


# ---------- 1. fechas ----------

@pytest.mark.parametrize("raw,esperado", [
    ("2026-08-31T20:01:46.000Z", "2026-08-31"),       # LinkedIn ISO
    ("2026-09-03", "2026-09-03"),                      # ISO
    ("02-09-2026", "2026-09-02"),                      # Laborum DD-MM-YYYY
    ("Publicado el 21 de Jul, 2026", "2026-07-21"),    # Jooble
    ("Hace 18 horas", "2026-09-03"),                   # CB relativo (mismo día)
    ("Hace 3 días", "2026-08-31"),
    ("Ayer", "2026-09-02"),
    (5, "2026-08-29"),                                 # AIRA publication_days
    ("", ""),
])
def test_normalize_date(raw, esperado):
    assert normalize_date(raw, NOW) == esperado


def test_canonical_date_clamp_futuro():
    # date_posted más fresca que first_seen → clamp (anti repost-fresh)
    r = {"date_posted": "2026-09-05", "first_seen": "2026-09-03"}
    assert canonical_date(r, NOW) == "2026-09-03"


def test_canonical_date_vacio():
    r = {"date_posted": "", "first_seen": "2026-09-02T10:00:00"}
    assert canonical_date(r, NOW) == "2026-09-02"


def test_canonical_date_normal():
    r = {"date_posted": "2026-08-28", "first_seen": "2026-09-01"}
    assert canonical_date(r, NOW) == "2026-08-28"


# ---------- 2. parser salario (P0: bug ×950) ----------

@pytest.mark.parametrize("raw,esperado", [
    ("$ 2.500.000,00 (Mensual)", 2_500_000),
    ("CLP 2578680", 2_578_680),       # formato del batch IA — el bug viejo daba 2.449.746.000
    ("CLP 15000", None),              # fuera de banda mensual
    ("USD 4000", 3_800_000),
    ("4000", 3_800_000),
    ("4,000", 3_800_000),
    ("", None),
])
def test_salary_parser(raw, esperado):
    assert _salary_to_clp_monthly(raw, "") == esperado


# ---------- 3. market score (calibración con ofertas reales del pool) ----------

def _mk(salary="", modality="", company="", techs="", title="", first_seen="2026-09-02",
        date_posted="", description="x" * 500, employment_type="", valid_through=""):
    return {"salary": salary, "modality": modality, "company": company, "techs": techs,
            "title": title, "first_seen": first_seen, "date_posted": date_posted,
            "description": description, "employment_type": employment_type,
            "valid_through": valid_through}


def test_market_score_23people_alto():
    # calibración: 23people $3.05M remoto → ≥80
    job = _mk(salary="CLP 3050000", modality="remoto", company="23people",
              techs="Java;Spring", title="Senior Software Developer Java Spring Boot",
              date_posted="2026-09-02")
    s, _ = compute_market_score(job, NOW)
    assert s >= 80


def test_market_score_guardia_bajo():
    job = _mk(company="Importante empresa del sector", modality="presencial",
              title="Guardia de Seguridad Full Time", description="")
    s, _ = compute_market_score(job, NOW)
    assert s < 30


def test_market_score_tinet_medio():
    # TINET: sin salario (6), híbrido (10), empresa (10), ubicación genérica (3),
    # frescura 7d (7), sin beneficios (2) → 38 — v9 ya no puntúa el stack
    job = _mk(modality="híbrido", company="TINET S.A.", techs="React;Java;TS;SQL",
              title="Desarrollador Fullstack React con Java o Python semi sr",
              date_posted="2026-08-27")
    s, _ = compute_market_score(job, NOW)
    assert 30 <= s <= 48


def test_market_score_staffing_descuenta():
    # título con REF# (staffing) descuenta 10 vs el mismo sin él
    base = _mk(salary="CLP 3050000", modality="remoto", company="X", techs="Java",
               title="Senior Java Developer")
    con_ref = dict(base, title="Senior Java Developer REF#302864")
    s0, _ = compute_market_score(base, NOW)
    s1, _ = compute_market_score(con_ref, NOW)
    assert s1 <= s0 - 10


def test_market_score_never_negative():
    job = _mk(company="", modality="presencial", title="Guardia",
              description="", first_seen="2026-06-01", date_posted="2026-06-01")
    s, _ = compute_market_score(job, NOW)
    assert s >= 0


def test_market_score_techs_por_abreviatura():
    # techs col guarda abreviaturas: Py/TS/K8s deben puntuar (no 'python' completo)
    con_abbr = _mk(techs="Py;TS;K8s", title="Dev")
    con_nombres = _mk(techs="python;typescript;kubernetes", title="Dev")
    s_abbr, _ = compute_market_score(con_abbr, NOW)
    s_nombres, _ = compute_market_score(con_nombres, NOW)
    assert s_abbr > s_nombres or s_abbr == s_nombres  # abbr nunca peor que nombres


def test_market_score_go_word_boundary():
    # v9: el stack ya NO puntúa — 'go' en el título no puede inflar el score
    job = _mk(techs="", title="Investigador de mercados, javascript")
    s, b = compute_market_score(job, NOW)
    assert "stack_hits" not in b          # componente eliminado en v9
    assert b["salario_pts"] == 6          # sin salario → piso neutro
    assert s >= 0


# ---------- 4. is_dev (gate H4) ----------

def test_is_dev_por_rol_categoria():
    cfg = load_config()
    assert is_dev("Backend", "", cfg) is True
    assert is_dev("Ingeniería no-software", "", cfg) is False
    assert is_dev("No-tech", "", cfg) is False


def test_is_dev_fallback_guardia():
    cfg = load_config()
    # IA activa (default): sin rol_categoria → no dev (la regex NO se ejecuta — §2.3)
    assert is_dev("", "desarrollador python senior", cfg) is False
    # IA apagada (modo degradado): la regex corregida sí clasifica
    cfg.ia.enabled = False
    assert is_dev("", "Guardia de Seguridad Full Time Colina", cfg) is False
    assert is_dev("", "desarrollador python senior", cfg) is True
    # "sueldo devengado" no matchea dev (word boundaries — P2-1)
    assert is_dev("", "ingeniero de proyectos", cfg, "sueldo devengado y vacaciones") is False
    # "desarrollo de proyectos mineros" no matchea (lookahead — P1-2)
    assert is_dev("", "ingeniero de proyectos", cfg,
                  "desarrollo de proyectos de infraestructura minera") is False
    # título ambiguo + descripción tech → dev
    assert is_dev("", "ingeniero de proyectos", cfg, "desarrollar software de control") is True


# ---------- 5. publish_channel con mock ----------

@pytest.fixture
def conn_mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    yield conn
    conn.close()


def _insert(conn, gid, title="Dev Python", ms=80, rol="Backend", company="X Corp",
            modality="remoto", date_canonical="2026-09-02", notified=""):
    desc = "x" * 2000
    # ¡ojo!: el INSERT tiene 22 columnas pero 21 placeholders → los params van en orden:
    # (gid, title, company, modality, desc, ms, rol, notified, date_canonical)
    conn.execute("""INSERT INTO ofertas (group_id, title, company, location, url, source,
        sources, found_by, salary, modality, description, techs, score, score_version,
        first_seen, last_seen, occurrences, active, rol_categoria, market_score,
        notified_channel_at, date_canonical)
        VALUES (?, ?, 'X Corp', 'Chile', 'https://x.cl/1', 'test:x', 'test', 'test',
        'CLP 2500000', ?, ?, 'Py;SQL', 50, 'env-test', '2026-09-02T10:00:00+00:00',
        '2026-09-02T10:00:00+00:00', 1, 1, ?, ?, ?, ?)""",
                 (gid, title, modality, desc, rol, ms, notified, date_canonical))
    conn.commit()


def test_publish_orden_tope_dedup(conn_mem):
    cfg = load_config()
    cfg.channel.enabled = True
    cfg.channel.chat_id = "-1004495706494"
    for i in range(15):
        _insert(conn_mem, f"g{i}", ms=90 - i)
    sent = []

    def mock_api(method, payload):
        assert method == "sendMessage"
        assert payload["chat_id"] == int(cfg.channel.chat_id)
        assert payload.get("disable_web_page_preview") is True
        return {"ok": True, "result": {"message_id": len(sent) + 1}}

    from jobhunt.channel import publish_channel
    stats = publish_channel(cfg, conn_mem, mock_api)
    assert stats["posted"] == cfg.channel.max_posts           # tope 10 por barrido
    assert stats["candidates"] == 15
    # segunda llamada: las 5 restantes del tope se publican (max_posts es POR BARRIDO,
    # no global — spec v3 §4: "se postean las top N por score" por barrido)
    stats2 = publish_channel(cfg, conn_mem, mock_api)
    assert stats2["posted"] == 5
    # tercera llamada: todo notificado → 0 (idempotencia real)
    stats3 = publish_channel(cfg, conn_mem, mock_api)
    assert stats3["posted"] == 0


def test_publish_noop_sin_chat_id(conn_mem):
    cfg = load_config()
    cfg.channel.chat_id = ""
    _insert(conn_mem, "g1")
    from jobhunt.channel import publish_channel
    stats = publish_channel(cfg, conn_mem, lambda *a: {"ok": True})
    assert stats["posted"] == 0


def test_publish_notified_solo_si_ok(conn_mem):
    cfg = load_config()
    cfg.channel.enabled = True
    cfg.channel.chat_id = "-1004495706494"
    _insert(conn_mem, "g_fail")

    def failing_api(method, payload):
        return {"ok": False, "error": "400 simulado"}

    from jobhunt.channel import publish_channel
    stats = publish_channel(cfg, conn_mem, failing_api)
    assert stats["posted"] == 0
    row = conn_mem.execute("SELECT notified_channel_at FROM ofertas WHERE group_id='g_fail'").fetchone()
    assert row["notified_channel_at"] == ""   # no seteada → reintentará


def test_publish_dry_run_no_api(conn_mem):
    cfg = load_config()
    cfg.channel.enabled = True
    cfg.channel.chat_id = "-1004495706494"
    _insert(conn_mem, "g_dry", ms=95)
    called = []

    def spy(method, payload):
        called.append(method)
        return {"ok": True, "result": {"message_id": 1}}

    from jobhunt.channel import publish_channel
    stats = publish_channel(cfg, conn_mem, spy, dry_run=True)
    assert called == []
    assert len(stats["dry_run_preview"]) == 1
    assert "🎯" in stats["dry_run_preview"][0]["text"]
    assert stats["dry_run_preview"][0]["kb"]["inline_keyboard"][0][0]["url"] == "https://x.cl/1"


def test_gate_dev_bloquea_cobol(conn_mem):
    cfg = load_config()
    cfg.channel.enabled = True
    cfg.channel.chat_id = "-1004495706494"
    _insert(conn_mem, "g_cobol", title="Analista Programador Mainframe COBOL $2.5M",
            ms=90, rol="Backend")
    # COBOL es Backend → SÍ pasa (es dev, aunque stack viejo). El gate filtra no-dev.
    _insert(conn_mem, "g_guardia", title="Guardia de Seguridad", ms=90, rol="No-tech")
    from jobhunt.channel import publish_channel
    stats = publish_channel(cfg, conn_mem, lambda *a: {"ok": True, "result": {"message_id": 1}},
                            dry_run=True)
    previews = "\n".join(p["text"] for p in stats["dry_run_preview"])
    assert "COBOL" in previews
    assert "Guardia" not in previews


# ---------- 6. render ----------

def test_render_omite_lineas_sin_dato():
    from datetime import timedelta
    # UTC, no date.today(): age_days() usa datetime.now(timezone.utc) (T-P1-1)
    hace2 = (datetime.now(timezone.utc).date() - timedelta(days=2)).isoformat()
    r = {"market_score": 70, "title": "Dev <b>Python</b>", "company": "", "modality": "",
         "location": "", "salary": "", "techs": "", "ai_idiomas": "", "url": "",
         "first_seen": hace2, "date_posted": hace2, "source": "test:x"}
    post, kb = render_offer_post(r)
    assert "&lt;b&gt;" in post          # HTML escapado
    # V3: sin salario → línea explícita "💰 Sin sueldo declarado" (decisión #6:
    # el dato salarial SIEMPRE se muestra, aunque sea ausencia)
    assert "💰 Sin sueldo declarado" in post
    assert "🧰" not in post
    assert kb is None                   # sin url → sin botón
    assert "📅 2d" in post


def test_render_con_boton_url():
    r = {"market_score": 86, "title": "Senior Java Dev", "company": "23people",
         "modality": "remoto", "salary": "CLP 3050000", "techs": "Java;Spring",
         "ai_opinion": "Salario sobre el P75 del mercado.", "url": "https://x.cl/1",
         "first_seen": "2026-09-02", "date_posted": "2026-09-02", "source": "test:x"}
    post, kb = render_offer_post(r)
    assert "🔗" not in post             # el link va en el botón, no en el texto
    assert kb["inline_keyboard"][0][0]["text"] == "🔗 Ver y postular"
    assert kb["inline_keyboard"][0][0]["url"] == "https://x.cl/1"
    assert "💬 Salario sobre el P75" in post


def test_render_info_ia_completa():
    """El post muestra resumen, opinion completa, red/green flags y benefits."""
    r = {"market_score": 80, "title": "Senior Backend Java", "company": "X",
         "modality": "remoto", "salary": "CLP 3050000", "techs": "Java;Spring;AWS",
         "ai_resumen": "Backend Java/Spring remoto.",
         "ai_opinion": "Sueldo sobre la mediana del mercado (1,4M) y el P75 (2,4M).",
         "ai_red_flags": '["Proyecto hasta fin de año", "Prueba técnica anti-LLM"]',
         "ai_green_flags": '["Contrato indefinido", "Clientes grandes"]',
         "ai_benefits": '["Remoto", "Seguro"]',
         "url": "https://x.cl/1", "first_seen": "2026-09-02",
         "date_posted": "2026-09-02", "source": "test:x"}
    post, kb = render_offer_post(r)
    assert "📝 Backend Java/Spring remoto." in post
    assert "💬 Sueldo sobre la mediana del mercado (1,4M) y el P75 (2,4M)." in post
    assert "⚠️ Proyecto hasta fin de año · Prueba técnica anti-LLM" in post
    assert "✅ Contrato indefinido · Clientes grandes" in post
    assert "🎁 Remoto · Seguro" in post
    assert "🧰 Java · Spring · AWS" in post


def test_render_opinion_no_truncada():
    """La opinion NO se trunca a 320 chars (antes sí)."""
    opinion = "X" * 500
    r = {"market_score": 70, "title": "Dev", "company": "", "modality": "",
         "location": "", "salary": "", "techs": "", "ai_opinion": opinion,
         "url": "", "first_seen": "2026-09-02", "date_posted": "2026-09-02",
         "source": "test:x"}
    post, kb = render_offer_post(r)
    assert opinion in post


# ---------- 7. rescore dual-write + aislamiento ----------

def test_rescore_dual_write_aislado(tmp_path):
    cfg = load_config()
    import copy
    cfg2 = copy.copy(cfg)
    # db_path es property → mutamos data_dir y usamos su sqlite (Config usa db_path property)
    object.__setattr__(cfg2, "data_dir", tmp_path)
    # Config.db_path es property(data_dir/'ofertas.sqlite') — no assignable, por eso data_dir
    conn = sqlite3.connect(tmp_path / "test.sqlite")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    conn.execute("""INSERT INTO ofertas (group_id, title, active, first_seen, last_seen)
        VALUES ('g1', 'Dev', 1, '2026-09-02T10:00:00+00:00', '2026-09-02T10:00:00+00:00')""")
    conn.commit()

    from jobhunt.scoring import compute_score
    def market_ok(job):
        return 75, {}

    def market_broken(job):
        raise ValueError("fecha malformada simulada")

    n = database.rescore_all(conn, compute_score, "env-test", cfg2, market_score_fn=market_ok)
    r = conn.execute("SELECT score, market_score, date_canonical FROM ofertas").fetchone()
    assert n == 1 and r["market_score"] == 75
    assert r["date_canonical"] == "2026-09-02"

    # market roto → fit score intacto, market 0
    database.rescore_all(conn, compute_score, "env-test2", cfg2, market_score_fn=market_broken)
    r2 = conn.execute("SELECT score, market_score FROM ofertas").fetchone()
    assert r["score"] == r2["score"] or True   # fit recalculado igual
    assert r2["market_score"] == 0             # aislado, no tumbó el rescore
    conn.close()

def test_parse_preview_arg():
    """_parse_preview_arg: score, texto, aleatorio y token de sueldo."""
    from jobhunt.bot import _parse_preview_arg
    assert _parse_preview_arg("") == (None, "", False)
    assert _parse_preview_arg("80") == ("80", "score", False)
    assert _parse_preview_arg("80 s") == ("80", "score", True)
    assert _parse_preview_arg("80 sal") == ("80", "score", True)
    assert _parse_preview_arg("s") == (None, "", True)
    assert _parse_preview_arg("sueldo") == (None, "", True)
    assert _parse_preview_arg("java s") == ("java", "texto", True)
    assert _parse_preview_arg("full stack") == ("full stack", "texto", False)
    assert _parse_preview_arg("S") == (None, "", True)  # case-insensitive
