"""Esquema analítico (aditivo e idempotente): columnas derivadas en `ofertas` + tablas nuevas.

Las tablas de historia (oferta_eventos, mercado_*) NO llevan FK: `/db old|all` borra filas de
`ofertas` y la historia debe sobrevivir.
"""
from __future__ import annotations

import sqlite3

COLUMNAS_OFERTAS = [
    ("salary_clp", "INTEGER"), ("salary_clp_min", "INTEGER"), ("salary_clp_max", "INTEGER"),
    ("salary_norm_src", "TEXT DEFAULT ''"),
    ("modality_norm", "TEXT DEFAULT ''"), ("modality_source", "TEXT DEFAULT ''"),
    ("seniority_norm", "TEXT DEFAULT ''"), ("rol_familia", "TEXT DEFAULT ''"),
    ("employment_norm", "TEXT DEFAULT ''"), ("applicants_n", "INTEGER"),
    ("region", "TEXT DEFAULT ''"), ("comuna", "TEXT DEFAULT ''"),
    ("company_canon", "TEXT DEFAULT ''"), ("n_fuentes", "INTEGER DEFAULT 1"),
    ("norm_version", "TEXT DEFAULT ''"),
    ("exp_anios", "INTEGER"),     # años de experiencia pedidos: years_official (JSON-LD) o texto
]

_TABLAS = (
    """CREATE TABLE IF NOT EXISTS oferta_techs (
        group_id TEXT NOT NULL, tech TEXT NOT NULL,
        PRIMARY KEY (group_id, tech)) WITHOUT ROWID""",
    "CREATE INDEX IF NOT EXISTS idx_ot_tech ON oferta_techs(tech)",
    """CREATE TABLE IF NOT EXISTS oferta_tags (
        group_id TEXT NOT NULL, tipo TEXT NOT NULL, valor TEXT NOT NULL,
        PRIMARY KEY (group_id, tipo, valor)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS oferta_eventos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL, scan_id INTEGER,
        group_id TEXT NOT NULL, title TEXT DEFAULT '', company TEXT DEFAULT '',
        rol_familia TEXT DEFAULT '', fuente TEXT DEFAULT '',
        tipo TEXT NOT NULL, antes TEXT DEFAULT '', despues TEXT DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS idx_ev_gid ON oferta_eventos(group_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_ev_ts ON oferta_eventos(ts)",
    """CREATE TABLE IF NOT EXISTS mercado_diario (
        fecha TEXT NOT NULL,
        rol_familia TEXT NOT NULL DEFAULT '*', seniority TEXT NOT NULL DEFAULT '*',
        fuente TEXT NOT NULL DEFAULT '*', modalidad TEXT NOT NULL DEFAULT '*',
        n_activas INTEGER, n_nuevas INTEGER, n_cerradas INTEGER,
        n_con_sueldo INTEGER, sueldo_p25 INTEGER, sueldo_p50 INTEGER, sueldo_p75 INTEGER,
        PRIMARY KEY (fecha, rol_familia, seniority, fuente, modalidad)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS mercado_tech_semanal (
        semana TEXT NOT NULL, tech TEXT NOT NULL, rol_familia TEXT NOT NULL DEFAULT '*',
        n_activas INTEGER, n_nuevas INTEGER, n_base INTEGER,
        PRIMARY KEY (semana, tech, rol_familia)) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS oferta_prev (
        group_id TEXT PRIMARY KEY, salary_clp INTEGER, score INTEGER,
        encaje TEXT DEFAULT '', modalidad TEXT DEFAULT '', cerrada INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS estado_oferta (
        group_id TEXT PRIMARY KEY, estado TEXT NOT NULL,
        nota TEXT DEFAULT '', actualizado TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS vistas_guardadas (
        id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
        tipo TEXT NOT NULL, spec TEXT NOT NULL, creada TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS analytics_meta (
        clave TEXT PRIMARY KEY, valor TEXT NOT NULL)""",
)


def asegurar_esquema(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ofertas)")}
    for nombre, tipo in COLUMNAS_OFERTAS:
        if nombre not in cols:
            conn.execute(f"ALTER TABLE ofertas ADD COLUMN {nombre} {tipo}")
    for ddl in _TABLAS:
        conn.execute(ddl)
