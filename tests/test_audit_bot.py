"""Tests de auditoria (fixes SEC-1/SEC-8, DB-1, CH-3) sobre jobhunt/bot.py y db.py.

Corre: .venv/bin/python -m pytest tests/test_audit_bot.py -v
"""
import os
import shutil
import sqlite3
import tempfile

from jobhunt import db as database
from jobhunt.bot import _chat_allowed
from jobhunt.config import load_config


# ---------- SEC-1/SEC-8: _chat_allowed ----------

def test_chat_allowed_vacio_es_true():
    cfg = load_config()
    cfg.telegram.allowed_chats = ()
    cfg.telegram.chat_id = ""
    cfg.channel.chat_id = ""
    assert _chat_allowed(cfg, 12345) is True


def test_chat_allowed_canal_configurado_sin_estar_en_allowlist():
    cfg = load_config()
    cfg.telegram.allowed_chats = (111,)
    cfg.telegram.chat_id = "111"
    cfg.channel.chat_id = "-1009999"
    # el canal no está en TELEGRAM_ALLOWED_CHATS pero debe poder recibir posts (SEC-8)
    assert _chat_allowed(cfg, -1009999) is True
    # el chat del bot admin (en allowed_chats) sigue permitido
    assert _chat_allowed(cfg, 111) is True


def test_chat_allowed_chat_ajeno_false():
    cfg = load_config()
    cfg.telegram.allowed_chats = (111,)
    cfg.telegram.chat_id = "111"
    cfg.channel.chat_id = "-1009999"
    assert _chat_allowed(cfg, 999999) is False


# ---------- DB-1: backup_db (online backup API vs shutil.copy2 del WAL) ----------

def test_backup_db_incluye_datos_no_checkpointeados():
    d = tempfile.mkdtemp()
    try:
        src_path = os.path.join(d, "src.sqlite")
        conn = sqlite3.connect(src_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()  # committed, pero sigue en el -wal, NO checkpointeado

        # backup_db (el fix): debe ver la fila
        dst_path = os.path.join(d, "dst.sqlite")
        database.backup_db(conn, dst_path)
        dst = sqlite3.connect(dst_path)
        assert dst.execute("SELECT * FROM t").fetchall() == [(1,)]
        dst.close()

        # shutil.copy2 crudo (el bug DB-1): sin el -wal, la tabla ni existe
        bad_path = os.path.join(d, "bad.sqlite")
        shutil.copy2(src_path, bad_path)
        bad = sqlite3.connect(bad_path)
        try:
            bad.execute("SELECT * FROM t").fetchall()
            assert False, "shutil.copy2 no debería ver los datos del WAL"
        except sqlite3.OperationalError:
            pass
        bad.close()
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------- CH-3: /channel reset debe limpiar channel_posts kind='offer' ----------

def test_channel_reset_limpia_channel_posts():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    conn.execute("""INSERT INTO ofertas (group_id, title, active, notified_channel_at,
        first_seen, last_seen)
        VALUES ('g1', 't1', 1, '2026-09-01T00:00:00', '2026-09-01T00:00:00', '2026-09-01T00:00:00')""")
    conn.execute("""INSERT INTO channel_posts (message_id, group_id, kind, bucket, body_hash, posted_at)
        VALUES (1, 'g1', 'offer', 'g1', '', '2026-09-01T00:00:00')""")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM channel_posts WHERE kind='offer'").fetchone()[0] == 1

    # misma lógica que el handler /channel-reset-confirm (bot.py)
    n = conn.execute("""UPDATE ofertas SET notified_channel_at=''
        WHERE active=1 AND notified_channel_at != ''""").rowcount
    conn.execute("DELETE FROM channel_posts WHERE kind='offer'")
    conn.commit()

    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM channel_posts WHERE kind='offer'").fetchone()[0] == 0
    assert conn.execute("SELECT notified_channel_at FROM ofertas WHERE group_id='g1'").fetchone()[0] == ""
    conn.close()
