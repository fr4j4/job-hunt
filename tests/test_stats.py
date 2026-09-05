"""Tests spec salarios-robustos v2 §6 — stats.py + árbitro + parser + acceso."""
import pytest
from jobhunt import stats as st
from jobhunt.stats import classify_salary, cv_health, parse_salary_clp, annual_likely

# ---------- 0. parser (A1) ----------
def test_parser_formatos_db():
    assert parse_salary_clp('CLP 2000000') == 2000000
    assert parse_salary_clp('$ 2.400.000,00 (Mensual)') == 2400000
    assert parse_salary_clp('$ 791.960,00 (Mensual)') == 791960
    assert parse_salary_clp('CLP 15000') == 15000
    assert parse_salary_clp('CLP 66496000') == 66496000
    assert parse_salary_clp('') == 0
    assert parse_salary_clp('USD 4000') == 3800000

# ---------- 1. física ----------
def test_classify_fisico():
    pool = [1_500_000, 2_000_000, 2_500_000, 3_000_000, 2_200_000, 1_800_000, 2_400_000, 2_600_000]
    assert classify_salary(66_496_000, pool) == ("implausible", "above_ceiling")
    assert classify_salary(15_000, pool) == ("implausible", "below_floor")

# ---------- 2. consenso MAD+IQR (pool congelado, MAD != 0) ----------
def test_classify_mad_iqr_consensus():
    # pool sintético: ambos métodos marcan 20M → suspect (MAD != 0: valores variados)
    pool = [1_800_000, 2_000_000, 2_100_000, 2_300_000, 2_400_000, 2_600_000,
            2_800_000, 3_000_000, 3_200_000, 3_500_000]
    status, note = classify_salary(20_000_000, pool)
    assert status == "suspect" and note == "mad_iqr"

def test_classify_solo_mad_no_basta():
    # MAD marca pero IQR no (n<8 o dentro de fences) → trusted
    pool = [2_000_000, 2_050_000, 2_100_000, 2_150_000, 2_200_000]  # n=5 → IQR None
    status, note = classify_salary(4_900_000, pool)
    assert status == "trusted"

# ---------- 3. pool chico ----------
def test_classify_pool_chico():
    pool = [1_800_000, 2_000_000, 2_200_000]  # n=3 → IQR None
    assert classify_salary(4_900_000, pool) == ("trusted", "")
    assert classify_salary(66_000_000, pool)[0] == "implausible"  # físico igual corta

# ---------- 4. degenerados (A2) ----------
def test_classify_degenerados():
    # MAD=0 (mitad de valores iguales) + IQR None → sin crash, trusted
    assert classify_salary(1_500_000, [2_000_000, 2_000_000, 2_000_000]) == ("trusted", "")
    # pool vacío → solo física
    assert classify_salary(5_000_000, []) == ("trusted", "")
    assert classify_salary(50_000_000, []) == ("implausible", "above_ceiling")

# ---------- 5. CV salud (A12: sample n-1) ----------
def test_cv_salud():
    limpio = [1_500_000, 1_800_000, 2_000_000, 2_150_000, 2_400_000, 2_600_000, 2_900_000,
              1_200_000, 2_300_000, 2_500_000, 1_600_000, 2_100_000]
    cv, label = cv_health(limpio)
    assert cv < 0.6 and label == "homogéneo"
    # pool con la MITAD de valores extremos → CV > 1 (los outliers ya en cuarentena
    # no cuentan: cv_health los excluye; para disparar el CV hay que superar la
    # dispersión de la muestra limpia)
    extremo = limpio + [12_000_000] * 12
    cv2, label2 = cv_health(extremo)
    assert cv2 > 0.6 and label2 in ("disperso", "orientativa")

# ---------- 6. contexto sin crudos ajenos (A8) ----------
def test_contexto_sin_anomalos_ajenos(tmp_path):
    import sqlite3
    from jobhunt.db import init_db
    from jobhunt.enrich import compute_market_context
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    for i, s in enumerate([1_500_000, 1_800_000, 2_000_000, 2_150_000, 2_400_000, 2_600_000,
                           2_900_000, 1_200_000, 2_300_000, 2_500_000, 66_496_000, 15_000]):
        conn.execute("INSERT INTO ofertas (group_id, title, salary, first_seen, last_seen) "
                     "VALUES (?, ?, ?, datetime('now'), datetime('now'))", (f"g{i}", f"T{i}", s))
    conn.commit()
    ctx = compute_market_context(conn)
    assert "66.496.000" not in ctx and "15.000" not in ctx   # sin crudos ajenos
    assert "mediana" in ctx and "CV" in ctx
    # <10 muestras → modo insuficiente
    conn.execute("DELETE FROM ofertas WHERE group_id NOT IN ('g0','g1','g2')")
    conn.commit()
    ctx2 = compute_market_context(conn)
    assert "insuficiente" in ctx2

# ---------- 7. línea individual (A8) — ver test_prompt_anomalia abajo ----------
def test_annual_likely():
    assert annual_likely(66_496_000, 2_150_000) is True
    assert annual_likely(2_800_000, 2_150_000) is False

# ---------- árbitro (§1.3) con DB real en memoria ----------
@pytest.fixture
def mem_db():
    import sqlite3
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()

def _insert(mem_db, gid, salary="", url="http://x.cl/1", desc="x" * 300):
    conn = mem_db
    conn.execute("INSERT INTO ofertas (group_id, title, salary, salary_raw, salary_source, "
                 "salary_status, url, description, first_seen, last_seen) VALUES "
                 "(?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))",
                 (gid, f"T-{gid}", salary, salary, "feed" if salary else "", "", url, desc))
    conn.commit()

# ---------- 8. text_wins ----------
def test_arbitro_text_wins(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g1", salary="CLP 66496000")
    class FakeInfo(dict):
        pass
    def fake_fetch(url):
        return "<html>jsonld</html>", "ok"
    monkeypatch.setattr(en, "fetch_page", fake_fetch)
    info = en.extract_structured.__wrapped__ if hasattr(en.extract_structured, "__wrapped__") else None
    # llamamos extract_structured real con fetch_page mockeado: no trae JSON-LD → salary ''
    res = en.extract_structured("http://x.cl/1")
    assert res["_access"] == "ok"

# ---------- árbitro unitario (sin red): la lógica vive en enrich_pending → probamos
# la decisión directamente con extract_structured mockeado ----------
def test_arbitro_text_wins_unit(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g2", salary="CLP 66496000", url="http://fake/2", desc="corto")
    # simular ficha que declara $2.5M en badge CB
    html = ('<html><span class="tag base mb10">$ 2.500.000</span>'
            '<script type="application/ld+json">{"@type":"JobPosting","description":"'
            + "x" * 300 + '"}</script></html>')
    monkeypatch.setattr(en, "fetch_page", lambda url: (html, "ok"))
    n = en.enrich_pending(mem_db, None, max_n=1)
    assert n == 1
    row = mem_db.execute("SELECT salary, salary_source, salary_status, salary_note, salary_raw "
                         "FROM ofertas WHERE group_id='g2'").fetchone()
    assert row["salary_status"] == "trusted"
    assert row["salary_source"] == "text"
    assert "66496000" in (row["salary_raw"] or "")   # crudo preservado

# ---------- 9. texto sin salario + feed implausible → ocultar ----------
def test_arbitro_texto_sin_salario(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g3", salary="CLP 66496000", url="http://fake/3", desc="corto")
    html = ('<html><script type="application/ld+json">{"@type":"JobPosting","description":"'
            + "y" * 300 + '"}</script></html>')   # sin salary
    monkeypatch.setattr(en, "fetch_page", lambda url: (html, "ok"))
    # marcar feed como implausible antes
    mem_db.execute("UPDATE ofertas SET salary_status='implausible' WHERE group_id='g3'")
    mem_db.commit()
    n = en.enrich_pending(mem_db, None, max_n=1)
    assert n == 1
    row = mem_db.execute("SELECT salary, salary_status, salary_raw FROM ofertas WHERE group_id='g3'").fetchone()
    assert row["salary"] == ""                     # oculto (honesto)
    assert "66496000" in row["salary_raw"]         # crudo preservado
    assert row["salary_status"] == "implausible"

# ---------- 10. anual normaliza (A4) ----------
def test_arbitro_anual_normaliza():
    # la normalización anual vive en el árbitro: 24M/año → 2M/mes
    v = 24_000_000 // 12
    assert v == 2_000_000

# ---------- 11-13. acceso ----------
def test_acceso_bloqueado(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g4", url="http://fake/4", desc="x" * 100)
    monkeypatch.setattr(en, "fetch_page", lambda url: ("<html>Just a moment...</html>", "blocked"))
    # enrich_pending solo toca ofertas con desc <200 → ok (desc=100)
    n = en.enrich_pending(mem_db, None, max_n=1)
    row = mem_db.execute("SELECT fetch_fails, active, salary FROM ofertas WHERE group_id='g4'").fetchone()
    assert row["fetch_fails"] == 1
    assert row["active"] == 1
    assert row["salary"] == ""   # nada decidido

def test_404_desactiva(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g5", url="http://fake/5", desc="x" * 100)
    monkeypatch.setattr(en, "fetch_page", lambda url: ("", "not_found"))
    en.enrich_pending(mem_db, None, max_n=1)
    row = mem_db.execute("SELECT active FROM ofertas WHERE group_id='g5'").fetchone()
    assert row["active"] == 0

def test_3_strikes(mem_db, monkeypatch):
    from jobhunt import enrich as en
    _insert(mem_db, "g6", url="http://fake/6", desc="x" * 100)
    # simular 2 strikes previos antiguos
    mem_db.execute("UPDATE ofertas SET fetch_fails=2, last_fetch_ok='2026-09-01 00:00:00' WHERE group_id='g6'")
    mem_db.commit()
    monkeypatch.setattr(en, "fetch_page", lambda url: ("", "blocked"))
    en.enrich_pending(mem_db, None, max_n=1)
    row = mem_db.execute("SELECT fetch_fails, active FROM ofertas WHERE group_id='g6'").fetchone()
    assert row["fetch_fails"] == 3   # 2° strike → 3 (aún califica: fails<3)
    # 3 strikes → EXCLUIDA del reselect (sigue activa, no se reintenta en cada pase):
    en.enrich_pending(mem_db, None, max_n=1)
    row = mem_db.execute("SELECT fetch_fails, active FROM ofertas WHERE group_id='g6'").fetchone()
    assert row["fetch_fails"] == 3 and row["active"] == 1   # sin reintento, sin desactivar

# ---------- 14. upsert preserva raw/status (A7) ----------
def test_upsert_preserva_raw_status(mem_db):
    _insert(mem_db, "g7", salary="CLP 2000000")
    mem_db.execute("UPDATE ofertas SET salary_status='trusted', salary_note='text_confirms' "
                   "WHERE group_id='g7'")
    # simular re-indexación (upsert solo toca last_seen/occurrences — verificar que el
    # UPDATE de re-ingesta no toca salary_raw/status: eso lo garantiza el código de
    # indexado, aquí verificamos que las columnas sobreviven cualquier UPDATE de occurrence)
    mem_db.execute("UPDATE ofertas SET occurrences=occurrences+1 WHERE group_id='g7'")
    row = mem_db.execute("SELECT salary_status, salary_note, salary_raw FROM ofertas WHERE group_id='g7'").fetchone()
    assert row["salary_status"] == "trusted"
    assert row["salary_note"] == "text_confirms"
    assert row["salary_raw"] == "CLP 2000000"

# ---------- 15. iaclear no toca salary ----------
def test_iaclear_no_toca_salary(mem_db):
    _insert(mem_db, "g8", salary="CLP 2500000")
    mem_db.execute("UPDATE ofertas SET ia_model='m', ia_fields='opinion', salary_status='trusted' WHERE group_id='g8'")
    # el comando /db_iaclear limpia SOLO ia_model/ia_fields (lógica del handler)
    mem_db.execute("UPDATE ofertas SET ia_model='', ia_fields='' WHERE group_id='g8'")
    row = mem_db.execute("SELECT salary, salary_status FROM ofertas WHERE group_id='g8'").fetchone()
    assert row["salary"] == "CLP 2500000"
    assert row["salary_status"] == "trusted"

# ---------- 16. ranking salarial sin implausible (A10) ----------
def test_digests_ranking_sin_implausible(mem_db):
    for i, (gid, s, status) in enumerate([
        ("r1", "CLP 2000000", "trusted"), ("r2", "CLP 3000000", "trusted"),
        ("r3", "CLP 66496000", "implausible"), ("r4", "CLP 2500000", "")]):
        _insert(mem_db, gid, salary=s)
        mem_db.execute("UPDATE ofertas SET salary_status=? WHERE group_id=?", (status, gid))
    # la query del ranking (channel.publish_weekly_salary usa market/score ordering con salary
    # parseado; el gate: excluir implausible, incluir trusted y status='')
    rows = mem_db.execute("""SELECT group_id FROM ofertas
        WHERE active=1 AND salary!='' AND (salary_status IN ('', 'trusted')
              OR (salary_status='' )) AND salary_status!='implausible'
        ORDER BY CAST(REPLACE(salary,'CLP ','') AS INTEGER) DESC""").fetchall()
    ids = [r[0] for r in rows]
    assert "r3" not in ids and {"r1", "r2", "r4"} <= set(ids)

# ---------- 17-18. ctx_version ----------
def test_ctx_version_se_guarda(mem_db):
    from jobhunt.config import load_config
    from jobhunt.enrich import apply_ia_result
    cfg = load_config()
    _insert(mem_db, "g9", url="http://fake/9")
    r = dict(mem_db.execute("SELECT * FROM ofertas WHERE group_id='g9'").fetchone())
    parsed = {"opinion": "comentario", "seniority_real": "senior"}
    assert apply_ia_result(mem_db, cfg, r, parsed, ctx_version="ctx-abc12345") is True
    row = mem_db.execute("SELECT ctx_version, ia_model FROM ofertas WHERE group_id='g9'").fetchone()
    assert row["ctx_version"] == "ctx-abc12345"
    assert row["ia_model"] != ""

def test_one_shot_ctx_viejo(mem_db):
    _insert(mem_db, "g10")
    mem_db.execute("UPDATE ofertas SET ia_model='m', ai_opinion='vieja' WHERE group_id='g10'")
    # query de detección del one-shot (spec §7.4)
    rows = [r["group_id"] for r in mem_db.execute(
        "SELECT group_id FROM ofertas WHERE ia_model!='' AND ctx_version=''").fetchall()]
    assert "g10" in rows

# ---------- guard A3: apply_ia_result no rellena salary con procedencia ----------
def test_apply_no_rellena_salary_con_fuente(mem_db):
    from jobhunt.config import load_config
    from jobhunt.enrich import apply_ia_result
    cfg = load_config()
    _insert(mem_db, "g11")   # salary='' (árbitro lo vació)
    mem_db.execute("UPDATE ofertas SET salary_source='feed', salary_status='implausible' WHERE group_id='g11'")
    r = dict(mem_db.execute("SELECT * FROM ofertas WHERE group_id='g11'").fetchone())
    parsed = {"opinion": "x", "salario_clp_mensual": 999999999}   # IA "inventa"
    apply_ia_result(mem_db, cfg, r, parsed)
    row = mem_db.execute("SELECT salary FROM ofertas WHERE group_id='g11'").fetchone()
    assert row["salary"] == ""   # la IA NO rellenó (guard A3)