"""Salud de fuentes y del canal a partir de scan_log (sin red, solo DB).

scan_log.sources_summary guarda {fuente: {"n": ofertas, "err": fallos}} por barrido.
Una fuente habilitada que rinde 0 varios barridos seguidos casi siempre está
bloqueada o cambió su API: se avisa al admin una vez (y se recuerda cada 12 barridos).
"""
from __future__ import annotations

import json

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)

_RECORDATORIO_CADA = 12   # barridos entre recordatorios de una fuente que sigue en 0


def _historial(conn, limite: int) -> list[dict]:
    """Últimos barridos (más reciente primero): [{ts, canal, fuentes:{...}}]."""
    out = []
    for ts, summ, posts in conn.execute(
            "SELECT ts, sources_summary, channel_posts FROM scan_log ORDER BY id DESC LIMIT ?",
            (limite,)).fetchall():
        try:
            fuentes = json.loads(summ) if summ else {}
        except (ValueError, TypeError):
            fuentes = {}
        out.append({"ts": ts, "canal": posts or 0,
                    "fuentes": fuentes if isinstance(fuentes, dict) else {}})
    return out


def racha_ceros(hist: list[dict], fuente: str) -> int:
    """Barridos consecutivos (desde el último) en que `fuente` rindió 0 ofertas."""
    racha = 0
    for h in hist:
        f = h["fuentes"].get(fuente)
        if f is None or (f.get("n") or 0) > 0:
            break
        racha += 1
    return racha


def fuentes_a_alertar(conn, cfg: Config) -> dict[str, int]:
    """{fuente: racha} que toca avisar ahora: racha == N exacto o recordatorio periódico."""
    n = max(1, cfg.alerts.source_sweeps)
    hist = _historial(conn, n + _RECORDATORIO_CADA * 4)
    if not hist:
        return {}
    out = {}
    for fuente in hist[0]["fuentes"]:
        r = racha_ceros(hist, fuente)
        if r >= n and (r == n or (r - n) % _RECORDATORIO_CADA == 0):
            out[fuente] = r
    return out


def canal_en_silencio(conn, cfg: Config) -> int:
    """Barridos consecutivos sin publicar al canal si alcanzan silence_sweeps; si no, 0.
    Avisa exactamente al llegar al umbral (no en cada barrido posterior)."""
    n = cfg.channel.silence_sweeps
    if not (cfg.channel.enabled and cfg.channel.chat_id) or n <= 0:
        return 0
    hist = _historial(conn, n + 1)
    if len(hist) < n or any(h["canal"] for h in hist[:n]):
        return 0
    if len(hist) > n and not hist[n]["canal"]:
        return 0          # ya se avisó antes: la racha pasó el umbral
    return n


_NOMBRES = {"linkedin": "LinkedIn", "computrabajo": "Computrabajo", "indeed": "Indeed",
            "glassdoor": "Glassdoor", "laborum": "Laborum", "jooble": "Jooble",
            "accenture": "Accenture", "aira": "Feeds de empleadores (AIRA)"}


def _nombre(fuente: str) -> str:
    return _NOMBRES.get(fuente, fuente.capitalize())


def _hace(ts: str) -> str:
    """'2026-10-08T03:00:00' (UTC) → 'hace 3 h' / 'hace 12 min' / 'hace 2 días'."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        mins = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)
    except ValueError:
        return str(ts)[:16]
    if mins < 1:
        return "hace instantes"
    if mins < 60:
        return f"hace {mins} min"
    if mins < 60 * 24:
        return f"hace {mins // 60} h"
    return f"hace {mins // (60 * 24)} días"


def texto_alertas(conn, cfg: Config) -> str:
    """Mensaje para el admin ('' si no hay nada que avisar). Dice qué pasó y qué hacer."""
    lineas = []
    for fuente, r in fuentes_a_alertar(conn, cfg).items():
        lineas.append(f"🔴 <b>{_nombre(fuente)}</b> no devuelve ofertas hace {r} barridos. "
                      f"Suele ser un bloqueo temporal o un cambio en el sitio.")
    silencio = canal_en_silencio(conn, cfg)
    if silencio:
        lineas.append(f"📢 El canal lleva {silencio} barridos sin publicar nada. Puede que el "
                      f"filtro de encaje sea muy estricto o que no haya ofertas nuevas.")
    if not lineas:
        return ""
    return ("⚠️ <b>Hay algo que revisar</b>\n\n" + "\n\n".join(lineas)
            + "\n\n👉 Detalle por fuente: /fuentes")


def texto_fuentes(conn, cfg: Config, barridos: int = 5) -> str:
    """Estado por fuente en lenguaje simple, para /fuentes."""
    hist = _historial(conn, barridos)
    if not hist or not any(h["fuentes"] for h in hist):
        return ("ℹ️ Todavía no hay datos por fuente. Se llenan después del próximo barrido "
                "(puedes lanzarlo con /search).")
    ultimo = hist[0]["fuentes"]
    umbral = cfg.alerts.source_sweeps
    filas, ok = [], 0
    for f in sorted(ultimo):
        n = (ultimo[f] or {}).get("n", 0) or 0
        err = (ultimo[f] or {}).get("err", 0) or 0
        racha = racha_ceros(hist, f)
        antes = [str((h["fuentes"].get(f) or {}).get("n", 0)) for h in hist[1:3] if f in h["fuentes"]]
        if racha >= umbral:
            icono, estado = "🔴", f"sin resultados hace {racha} barridos · posible bloqueo"
        elif racha:
            icono, estado = "🟡", "sin resultados en el último barrido" + (
                f" (antes: {', '.join(antes)})" if antes else "")
        else:
            icono, estado = "🟢", f"funcionando · {n} ofertas"
            ok += 1
            if err:
                icono = "🟡"
                estado += f" · con {err} error{'es' if err != 1 else ''} de conexión"
        if racha and err:
            estado += f" · {err} error{'es' if err != 1 else ''} de conexión"
        filas.append(f"{icono} <b>{_nombre(f)}</b> — {estado}")
    return "\n".join([
        "📡 <b>Estado de las fuentes</b>",
        f"Último barrido {_hace(hist[0]['ts'])} · {ok} de {len(ultimo)} funcionando bien",
        "",
        *filas,
        "",
        "<i>🟢 ok · 🟡 a vigilar · 🔴 caída (el sitio pudo bloquear el acceso o cambiar).</i>",
    ])


def alertar_admin(conn, cfg: Config, tg_api) -> bool:
    """Envía el aviso al chat admin si corresponde. Nunca lanza."""
    try:
        texto = texto_alertas(conn, cfg)
        if not texto or not cfg.telegram.bot_token or not cfg.telegram.chat_id:
            return False
        resp = tg_api("sendMessage", {"chat_id": cfg.telegram.chat_id, "parse_mode": "HTML",
                                      "text": texto})
        return bool(resp.get("ok"))
    except Exception as e:
        log.warning("alerta de salud falló (no tumba barrido): %s", e)
        return False
