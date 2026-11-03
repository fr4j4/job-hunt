"""Autenticación de la web: enlace de un solo uso (pedido con /web en el bot) → sesión.

- El token del enlace y el id de sesión son secretos aleatorios de 256 bits
  (secrets.token_urlsafe(32)). En la DB solo se guarda su SHA-256: quien lea la
  base no puede reconstruir un enlace ni una sesión válida.
- El token es de UN SOLO USO y vence en WEB_TOKEN_MINUTES (10 por defecto).
- La sesión vence en WEB_SESSION_DAYS; /web_salir revoca todas.
Funciones puras sobre una conexión sqlite (sin FastAPI) → testeables.
"""
from __future__ import annotations

import hashlib
import secrets
import time

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS web_tokens (
        hash TEXT PRIMARY KEY, expira REAL NOT NULL, usado INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS web_sesiones (
        hash TEXT PRIMARY KEY, expira REAL NOT NULL, creada REAL NOT NULL)""",
)


def _h(secreto: str) -> str:
    return hashlib.sha256(secreto.encode()).hexdigest()


def asegurar_tablas(conn) -> None:
    for ddl in _SCHEMA:
        conn.execute(ddl)
    conn.commit()


def _limpiar(conn, ahora: float) -> None:
    conn.execute("DELETE FROM web_tokens WHERE expira < ? OR usado = 1", (ahora - 3600,))
    conn.execute("DELETE FROM web_sesiones WHERE expira < ?", (ahora,))


def crear_token_login(conn, minutos: int = 10, ahora: float | None = None) -> str:
    """Nuevo enlace de un solo uso. Devuelve el token en claro (solo viaja por Telegram)."""
    ahora = ahora or time.time()
    asegurar_tablas(conn)
    _limpiar(conn, ahora)
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO web_tokens (hash, expira) VALUES (?, ?)", (_h(token), ahora + minutos * 60))
    conn.commit()
    return token


def canjear_token(conn, token: str, dias_sesion: int = 7, ahora: float | None = None) -> str | None:
    """Canjea el token por una sesión nueva. None si no existe, venció o ya se usó.
    El canje es atómico: el UPDATE ... WHERE usado=0 impide usarlo dos veces."""
    if not token or len(token) > 200:
        return None
    ahora = ahora or time.time()
    asegurar_tablas(conn)
    cur = conn.execute("UPDATE web_tokens SET usado = 1 WHERE hash = ? AND usado = 0 AND expira >= ?",
                       (_h(token), ahora))
    if cur.rowcount != 1:
        conn.commit()
        return None
    sesion = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO web_sesiones (hash, expira, creada) VALUES (?, ?, ?)",
                 (_h(sesion), ahora + dias_sesion * 86400, ahora))
    conn.commit()
    return sesion


def sesion_valida(conn, sesion: str | None, ahora: float | None = None) -> bool:
    if not sesion or len(sesion) > 200:
        return False
    ahora = ahora or time.time()
    asegurar_tablas(conn)
    fila = conn.execute("SELECT expira FROM web_sesiones WHERE hash = ?", (_h(sesion),)).fetchone()
    return bool(fila) and fila[0] >= ahora


def cerrar_sesion(conn, sesion: str | None) -> None:
    if sesion:
        asegurar_tablas(conn)
        conn.execute("DELETE FROM web_sesiones WHERE hash = ?", (_h(sesion),))
        conn.commit()


def revocar_todo(conn) -> int:
    """Cierra TODAS las sesiones e invalida los enlaces pendientes (/web_salir)."""
    asegurar_tablas(conn)
    n = conn.execute("DELETE FROM web_sesiones").rowcount
    conn.execute("DELETE FROM web_tokens")
    conn.commit()
    return n
