"""Regresiones del review de la auditoría (5 hallazgos).

Corre: .venv/bin/python -m pytest tests/test_audit_review.py -v
"""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from jobhunt import db as database
from jobhunt.channel import _send_digest, normalize_date
from jobhunt.config import load_config
from jobhunt.domain.texto import _norm
from jobhunt.enrich import _aplicar_ficha
from jobhunt.scoring import _kw_hit
from jobhunt.telegram.api import TelegramClient

NOW = datetime(2026, 9, 5, 3, 0, tzinfo=timezone.utc)


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    return conn


# ---------- R1: _kw_hit con grafías pegadas (scoring.py) ----------

def test_kw_hit_grafias_pegadas():
    for kw, txt in [("react", "reactjs developer"), ("node", "nodejs backend"),
                    ("node", "node.js backend"), ("angular", "angularjs senior"),
                    ("python", "python3 developer"), (".net", "desarrollador asp.net senior"),
                    ("react", "react native dev")]:
        assert _kw_hit(kw, _norm(txt)) is True, (kw, txt)


def test_kw_hit_sigue_siendo_palabra_completa():
    assert _kw_hit("java", _norm("javascript dev")) is False
    assert _kw_hit("intern", _norm("proyecto internacional")) is False
    assert _kw_hit(".net", _norm("net salary")) is False


# ---------- R2: rechazo de allowlist visible (telegram/api.py) ----------

def test_chat_rechazado_loguea_warning(caplog):
    cli = TelegramClient("tok", {111})
    with caplog.at_level(logging.WARNING, logger="jobhunt.telegram.api"):
        assert cli.chat_allowed(222) is False
    assert "222" in caplog.text


# ---------- R3: 'Hace N horas' con resolución horaria (domain/fechas.py) ----------

def test_normalize_date_horas_cruza_medianoche():
    assert normalize_date("Hace 5 horas", NOW) == "2026-09-04"
    assert normalize_date("Hace 20 horas", NOW) == "2026-09-04"
    assert normalize_date("Hace 2 horas", NOW) == "2026-09-05"
    assert normalize_date("Hace 30 minutos", NOW) == "2026-09-05"
    assert normalize_date("Hace 2 días", NOW) == (NOW - timedelta(days=2)).date().isoformat()


# ---------- R4: truncado 4096 también en la ruta manual/DM (channel.py) ----------

def test_send_digest_trunca_en_ruta_force():
    cfg = load_config()
    cfg.channel.chat_id = "-1004495706494"
    conn = _conn()
    visto = {}

    def api(method, payload):
        visto["len"] = len(payload["text"])
        return {"ok": True, "result": {"message_id": 1}}

    texto = "párrafo\n\n" * 1000
    assert _send_digest(cfg, api, "daily", texto, conn, NOW, force=True) is True
    assert visto["len"] <= 4096
    assert _send_digest(cfg, api, "daily", texto, conn, NOW, chat_id=111) is True
    assert visto["len"] <= 4096


# ---------- R5: params alineados en el UPDATE de la ficha (enrich.py) ----------

def test_aplicar_ficha_no_cruza_columnas():
    conn = _conn()
    # remote_official NULL = lo que deja db.upsert cuando el feed no trae el dato
    # (el COALESCE del UPDATE solo puede escribir sobre NULL).
    conn.execute("INSERT INTO ofertas (group_id, title, url, active, remote_official, "
                 "first_seen, last_seen) "
                 "VALUES ('g1','Dev','http://x.cl/1',1,NULL,datetime('now'),datetime('now'))")
    conn.commit()
    info = {"description": "x" * 200, "employment_type": "FULL_TIME",
            "years_official": 5, "remote_official": 1, "techs_desc": []}
    assert _aplicar_ficha(conn, {"group_id": "g1", "title": "Dev"}, info, []) == "ok"
    row = conn.execute("SELECT employment_type, years_official, remote_official "
                       "FROM ofertas WHERE group_id='g1'").fetchone()
    assert row["employment_type"] == "FULL_TIME"
    assert row["years_official"] == 5
    assert row["remote_official"] == 1
