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


def texto_alertas(conn, cfg: Config) -> str:
    """Mensaje para el admin ('' si no hay nada que avisar)."""
    from .notify import esc
    lineas = []
    for fuente, r in fuentes_a_alertar(conn, cfg).items():
        lineas.append(f"• <b>{esc(fuente)}</b>: 0 ofertas hace {r} barridos seguidos "
                      f"(¿bloqueo o cambio de API?)")
    silencio = canal_en_silencio(conn, cfg)
    if silencio:
        lineas.append(f"• <b>canal</b>: {silencio} barridos sin publicar nada")
    return ("⚠️ <b>Salud del scraping</b>\n" + "\n".join(lineas)) if lineas else ""


def texto_fuentes(conn, cfg: Config, barridos: int = 5) -> str:
    """Resumen para /fuentes: ofertas por fuente en los últimos barridos."""
    from .notify import esc
    hist = _historial(conn, barridos)
    if not hist or not any(h["fuentes"] for h in hist):
        return "ℹ️ Aún no hay barridos con detalle por fuente."
    nombres = sorted({f for h in hist for f in h["fuentes"]})
    lineas = [f"📡 <b>Fuentes</b> — último barrido {esc(str(hist[0]['ts'])[:16])} UTC",
              f"<i>ofertas por barrido, más reciente primero ({len(hist)})</i>", ""]
    for f in nombres:
        serie = [h["fuentes"].get(f) for h in hist]
        nums = " · ".join("–" if x is None else str(x.get("n", 0)) for x in serie)
        racha = racha_ceros(hist, f)
        err = (hist[0]["fuentes"].get(f) or {}).get("err", 0)
        icono = "🔴" if racha >= cfg.alerts.source_sweeps else ("🟡" if racha or err else "🟢")
        lineas.append(f"{icono} <b>{esc(f)}</b>: {nums}" + (f" · ⚠️ {err} err" if err else ""))
    return "\n".join(lineas)


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
