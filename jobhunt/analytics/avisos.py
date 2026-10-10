"""Avisos por Telegram sobre ofertas que SIGUES (estado_oferta). Opt-in: ANALYTICS_AVISOS_ESTADO=true.

Un aviso por (oferta, tipo): `aviso_estado` lo recuerda. Nunca lanza: un fallo de Telegram no tumba el barrido.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from ..config import Config
from ..logging_setup import get_logger

log = get_logger(__name__)
SEGUIDAS = ("guardada", "postulada", "entrevista")
MAX_POR_MENSAJE = 10


def avisar_cierres(conn, cfg: Config, tg_api) -> int:
    """→ número de ofertas avisadas."""
    try:
        if not cfg.analytics.avisos_estado or not cfg.telegram.bot_token or not cfg.telegram.chat_id:
            return 0
        filas = conn.execute(f"""SELECT e.oferta_id, e.title, e.company, s.estado
            FROM oferta_eventos e JOIN estado_oferta s ON s.oferta_id = e.oferta_id
            WHERE e.tipo='cerrada' AND s.estado IN ({','.join('?' * len(SEGUIDAS))})
              AND NOT EXISTS (SELECT 1 FROM aviso_estado a WHERE a.oferta_id = e.oferta_id AND a.tipo='cerrada')
              AND NOT EXISTS (SELECT 1 FROM oferta_eventos r WHERE r.oferta_id = e.oferta_id AND r.tipo='reaparecida' AND r.id > e.id)
            GROUP BY e.oferta_id ORDER BY e.id DESC LIMIT ?""", (*SEGUIDAS, MAX_POR_MENSAJE)).fetchall()
        if not filas:
            return 0
        lineas = [f"• <b>{html.escape(r['title'] or '')}</b>" + (f" — {html.escape(r['company'])}" if r['company'] else "")
                  + f" ({html.escape(r['estado'])})" for r in filas]
        texto = ("⚠️ <b>Ofertas que sigues y pudieron cerrar</b>\n" + "\n".join(lineas)
                 + "\n\n<i>Dejaron de aparecer en los últimos barridos; revisa si siguen abiertas.</i>")
        resp = tg_api("sendMessage", {"chat_id": cfg.telegram.chat_id, "parse_mode": "HTML", "text": texto})
        if not resp.get("ok"):
            return 0
        ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.executemany("INSERT OR IGNORE INTO aviso_estado VALUES (?,?,?)", [(r["oferta_id"], "cerrada", ahora) for r in filas])
        conn.commit()
        return len(filas)
    except Exception as e:
        log.warning("aviso de seguimiento falló (no tumba barrido): %s", e)
        return 0
