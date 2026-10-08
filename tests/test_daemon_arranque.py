"""Arranque del daemon con DB inexistente/vacía — regresión crash-loop 2026-10-08.

run_daemon leía _load_pool ANTES de init_db: con el archivo .sqlite borrado,
SELECT * FROM ofertas lanzaba "no such table" → exit 1 → systemd loop.
"""
import sqlite3

from jobhunt import bot


def _cfg_tmp(tmp_path):
    from jobhunt.config import load_config
    cfg = load_config()
    cfg.data_dir = tmp_path          # db_path es property sobre data_dir
    return cfg


def test_bootstrap_con_db_inexistente(tmp_path):
    """Sin archivo sqlite: bootstrap crea tablas y retorna pool vacío (sin excepción)."""
    cfg = _cfg_tmp(tmp_path)
    assert not cfg.db_path.exists()
    state = bot._daemon_bootstrap(cfg)
    assert state["offers"] == []
    assert state["last_sweep_key"] == ""
    # init_db corrió ANTES de las lecturas: las tablas existen
    conn = sqlite3.connect(str(cfg.db_path))
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"ofertas", "channel_posts", "score_versions", "scan_log"} <= tables


def test_load_pool_sin_esquema_no_tumba(tmp_path):
    """DB sin tablas (init_db fallido u omitted) → _load_pool degrada a []."""
    cfg = _cfg_tmp(tmp_path)
    sqlite3.connect(str(cfg.db_path)).close()   # archivo vacío, cero tablas
    assert bot._load_pool(cfg) == []


def test_bootstrap_es_idempotente(tmp_path):
    """Segundo bootstrap sobre la misma DB no falla (migraciones IF NOT EXISTS)."""
    cfg = _cfg_tmp(tmp_path)
    bot._daemon_bootstrap(cfg)
    state = bot._daemon_bootstrap(cfg)
    assert state["offers"] == []
