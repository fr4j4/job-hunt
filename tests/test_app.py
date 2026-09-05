"""Tests del paquete jobhunt.app (paso 6: estado global → clases, BatchRunner)."""
import ast
import pathlib
import sqlite3
import threading

import pytest

from jobhunt import db as database
from jobhunt.app.batch import BatchRunner
from jobhunt.app.state import IAState, SearchState, StopEvent
from jobhunt.config import load_config


# ---------------- state ----------------

def test_ia_state_reset_limpia_todo():
    st = IAState()
    st.update(running=True, done=7, total=9, current="Dev Python", t0=123.4)
    st.reset()
    assert dict(st.items()) == {"running": False, "done": 0, "total": 0,
                                "current": "", "t0": 0.0}
    assert st.running is False and st.t0 == 0.0   # t0 incluido (deuda cerrada)


def test_search_state_compat_dict():
    st = SearchState()
    assert st["running"] is False and st.get("t0") == 0.0
    st.update(running=True, t0=42.0)
    assert st["running"] is True and st.running is True
    st["running"] = False
    assert st.running is False
    assert set(st.keys()) == {"running", "t0"}
    assert st == {"running": False, "t0": 42.0}
    st.reset()
    assert st["t0"] == 0.0


def test_stop_event_es_event():
    ev = StopEvent()
    assert not ev.is_set()
    ev.set()
    assert ev.is_set()


# ---------------- BatchRunner ----------------

@pytest.fixture
def conn_mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    yield conn
    conn.close()


def test_batch_runner_procesa_dos_lotes(conn_mem):
    """2 lotes (batch_size=1) → worker_fn y consume_fn ven ambos; stop_event
    externo NO seteado no corta el loop (CONC-1: el corte es por lote_stop)."""
    cfg = load_config()
    cfg.ia.enabled, cfg.ia.api_key, cfg.ia.batch_size = True, "fake", 1
    cfg.ia.local_enabled = False
    cfg.channel.enabled = False

    vistos_worker, vistos_consume = [], []

    def fake_worker(cfg_, work_q, out_q, lote_id, stop_event, mercado, p_desc, extract_fn=None):
        while not work_q.empty():
            job = work_q.get()
            vistos_worker.append(job["group_id"])
            out_q.put((job, {"opinion": "ok"}, "", lote_id))
            work_q.task_done()

    def fake_consume(conn, cfg_, out_q, lote, lote_id, deadline, stop_event=None, **kw):
        vistos_consume.append([j["group_id"] for j in lote])
        return {"recibidos": len(lote), "ia_failures": 0, "breaker_trips": 0,
                "descartados": 0}

    runner = BatchRunner(cfg, "v-test", worker_fn=fake_worker, consume_fn=fake_consume)
    jobs = [{"group_id": "a", "score": 10}, {"group_id": "b", "score": 5}]
    st = runner.run(conn_mem, jobs, stop_event=threading.Event())   # externo, sin set

    assert sorted(vistos_worker) == ["a", "b"]
    assert vistos_consume == [["a"], ["b"]]   # 2 lotes, mejor fit primero
    assert st["lots_done"] == 2 and st["hechas"] == 2


def test_batch_runner_stop_event_corta_entre_lotes(conn_mem):
    cfg = load_config()
    cfg.ia.enabled, cfg.ia.api_key, cfg.ia.batch_size = True, "fake", 1
    cfg.ia.local_enabled = False
    cfg.channel.enabled = False
    stop = threading.Event()
    stop.set()
    llamadas = []
    runner = BatchRunner(cfg, "v-test",
                         worker_fn=lambda *a, **k: llamadas.append(1),
                         consume_fn=lambda *a, **k: llamadas.append(1))
    st = runner.run(conn_mem, [{"group_id": "a", "score": 1}], stop_event=stop)
    assert llamadas == [] and st["lots_done"] == 0


# ---------------- arquitectura ----------------

def test_app_no_importa_bot():
    """jobhunt/app/ es capa inferior: nunca importa jobhunt.bot (ni módulo ni
    diferido dentro de funciones)."""
    raiz = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "app"
    for f in raiz.glob("*.py"):
        arbol = ast.parse(f.read_text())
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ImportFrom):
                mod = ("." * nodo.level) + (nodo.module or "")
                assert "bot" not in mod.split("."), f"{f.name}: importa {mod}"
            elif isinstance(nodo, ast.Import):
                for a in nodo.names:
                    assert "bot" not in a.name.split("."), f"{f.name}: importa {a.name}"
