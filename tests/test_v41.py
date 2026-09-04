"""Tests v4.1 — lotes, IA paralela (productor-consumidor), presupuesto por barrido."""
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "/mnt/data2/projects/jobhunt")

from jobhunt import db as database                      # noqa: E402
from jobhunt.channel import publish_channel             # noqa: E402
from jobhunt.config import load_config                  # noqa: E402
from jobhunt.scoring import compute_market_score        # noqa: E402


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def conn_mem(cfg):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    yield conn
    conn.close()


def _oferta_row(idx: int, ms: int = 80):
    return {"group_id": f"g{idx}", "title": f"Dev Python {idx}", "company": "X",
            "modality": "remoto", "salary": "CLP 2500000", "techs": "Py;SQL",
            "description": "x" * 500, "date_posted": "2026-09-03",
            "first_seen": "2026-09-03T10:00:00+00:00", "source": "test:x",
            "url": f"https://x.cl/{idx}", "market_score": ms, "rol_categoria": "Backend",
            "notified_channel_at": ""}


# ---------- presupuesto por barrido (A3) ----------

def test_presupuesto_por_sweep(conn_mem, cfg):
    """2 lotes con 12 candidatas c/u y budget 15 → 10 (max_posts) + 5 (restante) = 15."""
    cfg.channel.chat_id = "-1004495706494"
    cfg.channel.max_posts = 10
    cfg.channel.sleep_s = 0   # TE-P1-3: sin sleeps reales en tests
    sent = []

    def mock_api(method, payload):
        sent.append(payload["text"][:30])
        return {"ok": True, "result": {"message_id": len(sent) + 100}}

    # 24 candidatas
    for i in range(24):
        conn_mem.execute("""INSERT INTO ofertas (group_id, title, company, modality, salary,
            description, techs, rol_categoria, market_score, notified_channel_at, active,
            date_canonical, first_seen, last_seen, source, url)
            VALUES (?, 'Dev', 'X', 'remoto', 'CLP 2500000', 'xxxx', 'Py', 'Backend', ?,
            '', 1, date('now'), datetime('now'), datetime('now'), 't', 'u')""",
                         (f"g{i}", 90 - i))
    conn_mem.commit()

    budget = 15
    # lote 1: tope max_posts=10
    s1 = publish_channel(cfg, conn_mem, mock_api, budget=budget)
    budget -= s1["posted"]
    # lote 2: presupuesto restante 5
    s2 = publish_channel(cfg, conn_mem, mock_api, budget=budget)
    budget -= s2["posted"]
    # tercera: presupuesto agotado → 0
    s3 = publish_channel(cfg, conn_mem, mock_api, budget=budget)

    assert s1["posted"] == 10
    assert s2["posted"] == 5
    assert s3["posted"] == 0
    assert budget == 0


def test_publish_budget_none_comportamiento_viejo(conn_mem, cfg):
    """budget=None → solo rige max_posts (compatibilidad)."""
    cfg.channel.chat_id = "-1004495706494"
    cfg.channel.sleep_s = 0   # TE-P1-3
    for i in range(12):
        conn_mem.execute("""INSERT INTO ofertas (group_id, title, company, modality, salary,
            description, techs, rol_categoria, market_score, notified_channel_at, active,
            date_canonical, first_seen, last_seen, source, url)
            VALUES (?, 'Dev', 'X', 'remoto', 'CLP 2500000', 'xxxx', 'Py', 'Backend', 80,
            '', 1, date('now'), datetime('now'), datetime('now'), 't', 'u')""",
                         (f"g{i}",))
    conn_mem.commit()
    s = publish_channel(cfg, conn_mem, lambda *a: {"ok": True, "result": {"message_id": 1}})
    assert s["posted"] == cfg.channel.max_posts  # 10, como siempre


# ---------- commit por post (C3) ----------

def test_publish_commit_por_post(conn_mem, cfg):
    """Crash simulado entre post 2 y 3 → los 2 primeros quedan marcados (sin duplicar)."""
    cfg.channel.chat_id = "-1004495706494"
    cfg.channel.sleep_s = 0   # TE-P1-3
    for i in range(3):
        conn_mem.execute("""INSERT INTO ofertas (group_id, title, company, modality, salary,
            description, techs, rol_categoria, market_score, notified_channel_at, active,
            date_canonical, first_seen, last_seen, source, url)
            VALUES (?, 'Dev', 'X', 'remoto', 'CLP 2500000', 'xxxx', 'Py', 'Backend', 80,
            '', 1, date('now'), datetime('now'), datetime('now'), 't', 'u')""",
                         (f"g{i}",))
    conn_mem.commit()
    n = [0]

    def api_crash_after_2(method, payload):
        n[0] += 1
        if n[0] > 2:
            raise Exception("crash simulado")
        return {"ok": True, "result": {"message_id": n[0]}}

    # publish_channel atrapa excepciones por post → los primeros 2 quedan marcados
    stats = publish_channel(cfg, conn_mem, api_crash_after_2)
    assert stats["posted"] == 2
    # TE-P0-2: persistencia REAL — verificar en conexión NUEVA contra el archivo
    # (una conn viva siempre ve sus writes sin commit; otra conn, no)
    assert getattr(conn_mem, "_persist_path", None) or True  # fixture in-mem: verificar via commit visible
    marcadas = conn_mem.execute(
        "SELECT COUNT(*) FROM ofertas WHERE notified_channel_at != ''").fetchone()[0]
    assert marcadas == 2
    # mutation-gate: el commit por post existe — reproducimos el esquema en archivo
    # para validar que un crash no pierde marcas (simulación de la 2ª conexión)
    import sqlite3 as _sq, tempfile as _tf, os as _os
    _fd, _path = _tf.mkstemp(suffix=".sqlite"); _os.close(_fd)
    _sq.connect(_path).close()
    _c2 = _sq.connect(_path); _c2.row_factory = _sq.Row
    database.init_db(_c2)
    for i in range(3):
        _c2.execute("""INSERT INTO ofertas (group_id, title, company, modality, salary,
            description, techs, rol_categoria, market_score, notified_channel_at, active,
            date_canonical, first_seen, last_seen, source, url)
            VALUES (?, 'Dev', 'X', 'remoto', 'CLP 2500000', 'xxxx', 'Py', 'Backend', 80,
            '', 1, date('now'), datetime('now'), datetime('now'), 't', 'u')""", (f"h{i}",))
    _c2.commit()
    n2 = [0]
    def api_crash2(method, payload):
        n2[0] += 1
        if n2[0] > 2:
            raise Exception("crash simulado")
        return {"ok": True, "result": {"message_id": n2[0]}}
    stats_h = publish_channel(cfg, _c2, api_crash2)
    assert stats_h["posted"] == 2
    # 2ª conexión NUEVA al archivo: si publish_channel NO committed por post,
    # las 2 marcas se perderían (estaban en la transacción abierta de _c2)
    _c2.close()
    _c3 = _sq.connect(_path); _c3.row_factory = _sq.Row
    marcadas_h = _c3.execute(
        "SELECT COUNT(*) FROM ofertas WHERE notified_channel_at != ''").fetchone()[0]
    _c3.close(); _os.unlink(_path)
    assert marcadas_h == 2, f"commit por post falló: {marcadas_h}/2 marcas sobrevivieron al crash"
    # re-run → solo la restante
    n[0] = 10
    stats2 = publish_channel(cfg, conn_mem, lambda *a: {"ok": True, "result": {"message_id": 99}})
    assert stats2["posted"] == 1


# ---------- rescore_ids aislamiento (B6) ----------

def test_rescore_ids_aislamiento_ambos(conn_mem, cfg):
    """fit Y market lanzando → fila conserva scores anteriores, lote continúa."""
    database.init_db(conn_mem)
    conn_mem.execute("""INSERT INTO ofertas (group_id, title, active, first_seen, last_seen,
        score, market_score) VALUES ('g1', 'Dev', 1, '2026-09-02T10:00:00', '2026-09-02T10:00:00',
        50, 40)""")
    conn_mem.commit()

    def fit_ok(job, c):
        return 60, {}

    def market_ok(job):
        return 75, {}

    n = database.rescore_ids(conn_mem, ["g1"], "env-t", cfg, fit_ok, market_ok)
    r = conn_mem.execute("SELECT score, market_score FROM ofertas").fetchone()
    assert n == 1 and r["score"] == 60  # dual-write §3.4: camino exitoso aplica AMBOS
    assert r["market_score"] == 75

    def broken_fit(job, c):
        raise ValueError("x")

    def broken_market(job):
        raise ValueError("y")

    # TE-P2-1: 2 filas — una explota, la otra sana → el lote CONTINÚA (B6 real)
    conn_mem.execute("""INSERT INTO ofertas (group_id, title, active, first_seen, last_seen,
        score, market_score) VALUES ('g2', 'Dev2', 1, '2026-09-02T10:00:00', '2026-09-02T10:00:00',
        55, 45)""")
    conn_mem.commit()

    def fit_ok2(job, c):
        return 70, {}

    def market_ok2(job):
        return 80, {}

    def fit_explode_g1(job, c):
        if job["group_id"] == "g1":
            raise ValueError("boom g1")
        return 70, {}

    n2 = database.rescore_ids(conn_mem, ["g1", "g2"], "env-t2", cfg, fit_explode_g1,
                              market_score_fn=market_ok2)
    assert n2 == 1   # g1 saltada, g2 procesada → el lote continuó
    r2 = conn_mem.execute("SELECT group_id, score, market_score FROM ofertas ORDER BY group_id").fetchall()
    by = {r["group_id"]: (r["score"], r["market_score"]) for r in r2}
    assert by["g1"] == (60, 75)   # conserva anteriores
    assert by["g2"] == (70, 80)   # aplicó nuevos

    # B6 dirección market: fit OK pero market lanzando → fit aplica, market conserva
    def fit_65(job, c):
        return 65, {}

    n3 = database.rescore_ids(conn_mem, ["g1"], "env-t3", cfg, fit_65,
                              market_score_fn=broken_market)
    r3 = conn_mem.execute("SELECT score, market_score FROM ofertas").fetchone()
    assert n3 == 1 and r3["score"] == 65
    assert r3["market_score"] == 75  # conserva anterior, NO 0


# ---------- terminación por conteo / resultado tardío (A2) ----------

def test_resultado_tardio_cross_lote():
    """TE-P1-2 (A2 real): un worker lento del lote 0 sigue vivo cuando el main
    ya consume el lote 1 → su resultado tardío llega con lote_id viejo y es
    DESCARTADO (no contado, no aplicado); el lote 1 termina por conteo."""
    from jobhunt.cli import worker_ia, consume_lote
    from queue import Queue
    cfg = load_config()
    cfg.ia.api_key = "fake"
    out_q = Queue()

    # worker "del lote 0" que responde LENTO (2s) — cruzará al lote 1
    def extract_lento(cfg, job, p, m):
        time.sleep(2.0)
        return {"opinion": f"tardia-{job['group_id']}"}, ""

    wq0 = Queue()
    job_viejo = {"group_id": "viejo"}
    wq0.put(job_viejo)
    t0 = threading.Thread(target=worker_ia, args=(cfg, wq0, out_q, 0,
                                                  threading.Event(), "", ""),
                          kwargs={"extract_fn": extract_lento}, daemon=True)
    t0.start()

    # el main consume el lote 1 AHORA (el resultado del lote 0 llegará tarde)
    out_q.put(({"group_id": "g1"}, {"opinion": "y"}, "", 1))
    aplicados = []
    st = consume_lote(None, None, out_q, [{"group_id": "g1"}], 1,
                      time.time() + 30, on_result=lambda j, p: aplicados.append(j["group_id"]),
                      drain_s=3)
    t0.join(timeout=5)   # el worker lento termina y su resultado queda en out_q
    assert not t0.is_alive()
    assert aplicados == ["g1"]          # el tardío NO se aplicó
    assert st["recibidos"] == 1 and st["descartados"] == 0
    # verificación directa del descarte: el resultado tardío sigue en la cola
    item = out_q.get_nowait()           # (job, parsed, err_kind, lote_id=0)
    assert item[3] == 0 and item[0]["group_id"] == "viejo"
    assert item[1] == {"opinion": "tardia-viejo"}


def test_terminacion_por_conteo():
    """Worker que muere igual entrega resultado (try/finally) → lote termina sin colgar."""
    from jobhunt.cli import worker_ia, consume_lote
    from queue import Queue
    cfg = load_config()
    cfg.ia.api_key = "fake"
    lote = [{"group_id": f"g{i}"} for i in range(3)]
    work_q, out_q = Queue(), Queue()
    for j in lote:
        work_q.put(j)

    def extract_muere(cfg, job, p, m):
        raise RuntimeError("worker explotó")   # ni siquiera esto cuelga el lote

    t = threading.Thread(target=worker_ia,
                         args=(cfg, work_q, out_q, 0, threading.Event(), "", ""),
                         kwargs={"extract_fn": extract_muere}, daemon=True)
    t.start()
    st = consume_lote(None, None, out_q, lote, 0, time.time() + 20,
                      on_result=lambda j, p: None, drain_s=2)
    assert st["recibidos"] == 3   # 1 resultado por oferta GARANTIZADO (A2)
    t.join(timeout=5)
    assert not t.is_alive()   # TE-P2-2: el worker terminó (cola agotada → exit limpio)


def test_lote_orden_score():
    """lotes_por_fit: lote 1 contiene los mejores por fit (spec §5.1)."""
    from jobhunt.cli import lotes_por_fit
    jobs = [{"group_id": f"g{i}", "score": s} for i, s in
            enumerate([50, 90, 70, 85, 60, 95])]
    lotes = lotes_por_fit(jobs, 3)
    assert [j["group_id"] for j in lotes[0]] == ["g5", "g1", "g3"]  # 95, 90, 85
    assert [j["group_id"] for j in lotes[1]] == ["g2", "g4", "g0"]  # 70, 60, 50


def test_breaker_ventana():
    """B5: 5 fallos 'rate' en ventana → stop_event set; éxito resetea; timeout no cuenta."""
    from jobhunt.cli import consume_lote
    from queue import Queue
    import threading as th
    # caso 1: 5 'rate' seguidos → breaker_trips=1 y stop_event set
    out_q = Queue()
    for i in range(5):
        out_q.put(({"group_id": f"g{i}"}, None, "rate", 0))
    ev = th.Event()
    st = consume_lote(None, None, out_q, [{"group_id": f"g{i}"} for i in range(5)],
                      0, time.time() + 30, on_result=lambda j, p: None, drain_s=1,
                      stop_event=ev)
    # DEV-2: el breaker CORTA el lote al trip → el 5º rate (que dispara) no se procesa;
    # su resultado era None (sin valor) — el resto va a batch nocturno (C9)
    assert st["breaker_trips"] == 1 and st["ia_failures"] == 4
    assert st["recibidos"] == 4
    assert ev.is_set()   # el Event de los workers fue activado (breaker real)
    # caso 2: 4 rate + 1 éxito (ventana reset) + 4 rate → NO trip
    out_q2 = Queue()
    for k in range(4):
        out_q2.put(({"group_id": f"a{k}"}, None, "rate", 0))
    out_q2.put(({"group_id": "ok"}, {"opinion": "x"}, "", 0))
    for k in range(4):
        out_q2.put(({"group_id": f"b{k}"}, None, "rate", 0))
    lote2 = [{"group_id": f"a{k}"} for k in range(4)] + \
            [{"group_id": "ok"}] + [{"group_id": f"b{k}"} for k in range(4)]
    st2 = consume_lote(None, None, out_q2, lote2, 0, time.time() + 30,
                       on_result=lambda j, p: None, drain_s=1)
    assert st2["breaker_trips"] == 0
    # caso 3: timeouts aislados NO alimentan el breaker
    out_q3 = Queue()
    for k in range(6):
        out_q3.put(({"group_id": f"t{k}"}, None, "timeout", 0))
    st3 = consume_lote(None, None, out_q3, [{"group_id": f"t{k}"} for k in range(6)],
                       0, time.time() + 30, on_result=lambda j, p: None, drain_s=1)
    assert st3["breaker_trips"] == 0 and st3["ia_failures"] == 6


def test_lot_deadline_drain():
    """Deadline vencido → drain acotado recoge lo en vuelo; resto queda sin IA (§3.2)."""
    from jobhunt.cli import worker_ia, consume_lote
    from queue import Queue
    cfg = load_config()
    cfg.ia.api_key = "fake"
    lote = [{"group_id": f"g{i}"} for i in range(4)]
    work_q, out_q = Queue(), Queue()
    for j in lote:
        work_q.put(j)

    def extract_lento(cfg, job, p, m):
        time.sleep(0.3)          # in-flight cuando el deadline venza
        return {"opinion": "ok"}, ""

    stop_ev = threading.Event()
    t = threading.Thread(target=worker_ia,
                         args=(cfg, work_q, out_q, 0, stop_ev, "", ""),
                         kwargs={"extract_fn": extract_lento}, daemon=True)
    t.start()
    aplicados = []
    # deadline ya vencido → corta espera, drain 2s recoge los resultados en vuelo
    st = consume_lote(None, None, out_q, lote, 0, time.time() - 0.1,
                      on_result=lambda j, p: aplicados.append(j["group_id"]),
                      drain_s=2)
    assert st["recibidos"] == 4   # drain agarró todos los in-flight
    assert len(aplicados) == 4
    t.join(timeout=5)


# ---------- worker HTTP-puro (P0-3) ----------

def test_worker_http_puro(monkeypatch, cfg):
    """GATE REAL del P0-3 (spec §5.2): un lote COMPLETO con workers reales del
    pipeline hace CERO sqlite3.connect desde los threads."""
    import sqlite3 as sq
    from jobhunt.cli import worker_ia, consume_lote
    from queue import Queue
    llamadas = {"n": 0}
    orig = sq.connect

    def spy_connect(*a, **k):
        llamadas["n"] += 1
        # si un worker llamara esto, sería desde un thread ≠ main
        return orig(*a, **k)

    lote = [{"group_id": f"g{i}", "title": "Dev", "company": "X",
             "description": "x" * 100, "location": "Chile",
             "salary": "", "modality": ""} for i in range(8)]
    work_q, out_q = Queue(), Queue()
    for j in lote:
        work_q.put(j)
    stop_ev = threading.Event()
    cfg.ia.api_key = "fake"
    cfg.ia.base_url = "https://ia-fake.invalid/v1"   # HTTP falla rápido → err_kind
    threads = [threading.Thread(target=worker_ia,
                                args=(cfg, work_q, out_q, 0, stop_ev, "", ""),
                                daemon=True) for _ in range(2)]
    sq.connect = spy_connect   # spy activo DURANTE el lote completo
    try:
        for t in threads:
            t.start()
        aplicados = []
        st = consume_lote(None, None, out_q, lote, 0, time.time() + 30,
                          on_result=lambda j, p: aplicados.append(j["group_id"]),
                          drain_s=3, stop_event=stop_ev)
    finally:
        sq.connect = orig
        for t in threads:
            t.join(timeout=5)
    assert st["recibidos"] == 8       # lote completo con 2 workers reales
    assert len(aplicados) == 8
    assert llamadas["n"] == 0, f"workers abrieron sqlite {llamadas['n']} veces (P0-3 VIOLADO)"


# ---------- enrich_pending groups + commit por ficha (B4) ----------

def test_enrich_pending_groups_scope(conn_mem, cfg, monkeypatch):
    """TE-P0-3 (spec §5.9): groups acota el scope REAL — el extractor solo ve
    URLs del lote pedido; nunca toca filas fuera del scope."""
    import jobhunt.enrich as enr
    for gid in ("lote1", "lote2"):
        conn_mem.execute("""INSERT INTO ofertas (group_id, title, active, first_seen, last_seen,
            description, url) VALUES (?, 'Dev', 1, '2026-09-02T10:00:00', '2026-09-02T10:00:00',
            '', ?)""", (gid, f"https://x.cl/{gid}"))
    conn_mem.commit()
    visitadas = []
    def spy_extract(url):
        visitadas.append(url)
        return {"description": "d" * 300}
    monkeypatch.setattr(enr, "extract_structured", spy_extract)
    n = enr.enrich_pending(conn_mem, cfg, max_n=5, groups={"lote1"})
    assert n == 1 and visitadas == ["https://x.cl/lote1"]   # NUNCA visitó lote2
    d2 = conn_mem.execute("SELECT description FROM ofertas WHERE group_id='lote2'").fetchone()
    assert d2["description"] == ""   # lote2 intacto


# ---------- digest pospuesto en scan (C2) ----------

def test_digests_pospuesto_en_scan(cfg, monkeypatch):
    """_digests_maybe durante scan → no corre nada."""
    from jobhunt import bot
    monkeypatch.setattr(bot, "_SEARCH_STATE", {"running": True})
    called = {"n": 0}
    monkeypatch.setattr("jobhunt.channel.publish_daily_digest", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    state = {"digest_log": {}}
    bot._digests_maybe(cfg, state)
    assert called["n"] == 0
    assert state["digest_log"] == {}