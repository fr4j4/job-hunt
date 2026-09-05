"""Tests de auditoria (fixes CH-2, CH-4, CH-10, F6, SEC-2) sobre jobhunt/channel.py y notify.py.

Corre: .venv/bin/python -m pytest tests/test_audit_channel.py -v
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from jobhunt import db as database
from jobhunt.channel import _bucket, channel_status, normalize_date, _send_digest
from jobhunt.config import load_config
from jobhunt.notify import table_block

NOW = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)


# ---------- F6: normalize_date semanas/meses ----------

def test_normalize_date_semanas():
    assert normalize_date("Hace 2 semanas", NOW) == (NOW - timedelta(days=14)).date().isoformat()


def test_normalize_date_meses():
    assert normalize_date("Hace 1 mes", NOW) == (NOW - timedelta(days=30)).date().isoformat()
    assert normalize_date("Hace 3 meses", NOW) == (NOW - timedelta(days=90)).date().isoformat()


# ---------- CH-10: _bucket ----------

def test_bucket_weekly_rol_es_semanal():
    iso = NOW.date().isocalendar()
    esperado = f"{iso[0]}-W{iso[1]:02d}"
    assert _bucket("weekly-rol", NOW) == esperado
    assert _bucket("weekly-techs", NOW) == esperado


# ---------- CH-4: _send_digest ----------

def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    return conn


def test_send_digest_trunca_4096():
    cfg = load_config()
    cfg.channel.chat_id = "-1004495706494"
    conn = _conn()
    capturado = {}

    def api(method, payload):
        capturado["text"] = payload["text"]
        return {"ok": True, "result": {"message_id": 1}}

    texto = "párrafo\n\n" * 1000  # >> 6000 chars
    assert len(texto) > 6000
    ok = _send_digest(cfg, api, "daily", texto, conn, NOW)
    assert ok is True
    assert len(capturado["text"]) <= 4096
    conn.close()


def test_send_digest_no_marca_bucket_si_falla():
    cfg = load_config()
    cfg.channel.chat_id = "-1004495706494"
    conn = _conn()

    def api_fail(method, payload):
        return {"ok": False, "error": "400 simulado"}

    ok = _send_digest(cfg, api_fail, "weekly-rol", "texto corto", conn, NOW)
    assert ok is False
    row = conn.execute("SELECT COUNT(*) FROM channel_posts WHERE kind='weekly-rol'").fetchone()
    assert row[0] == 0
    conn.close()


# ---------- SEC-2: table_block escapa company ----------

def test_table_block_escapa_company():
    offers = [{"score": 80, "company": "<b>Evil</b> Corp", "title": "Dev", "techs": "",
              "date_posted": "2026-09-02", "url": "", "salary": "", "modality": "",
              "seniority_real": "senior", "ai_idiomas": "", "ia_model": ""}]
    out = table_block(offers, links=False)
    assert "<b>Evil" not in out
    assert "&lt;b&gt;" in out


# ---------- CH-2: channel_status no lanza con filas Row ----------

def test_channel_status_no_lanza_con_rows(conn_mem_status):
    cfg = load_config()
    cfg.channel.chat_id = "-1004495706494"
    out = channel_status(conn_mem_status, cfg)
    assert isinstance(out, str)
    assert "Canal" in out


@pytest.fixture
def conn_mem_status():
    conn = _conn()
    conn.execute("""INSERT INTO ofertas (group_id, title, company, location, url, source,
        sources, found_by, salary, modality, description, techs, score, score_version,
        first_seen, last_seen, occurrences, active, rol_categoria, market_score,
        notified_channel_at, date_canonical)
        VALUES ('g1', 'Dev Python', 'X Corp', 'Chile', 'https://x.cl/1', 'test:x', 'test',
        'test', 'CLP 2500000', 'remoto', ?, 'Py;SQL', 50, 'env-test',
        '2026-09-02T10:00:00+00:00', '2026-09-02T10:00:00+00:00', 1, 1, 'Backend', 80,
        '', '2026-09-02')""", ("x" * 100,))
    conn.commit()
    yield conn
    conn.close()
