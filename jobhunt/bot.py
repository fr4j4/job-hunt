"""Modo daemon/bot: proceso residente con cron interno + paginación navegable.

Dos modos de operación del proyecto:
  jobhunt run    → ejecución simple: un barrido + 1 mensaje con las n ofertas nuevas
  jobhunt watch  → daemon: cron interno cada POLL_INTERVAL_MIN + digest paginado
                   navegable (callback_query → edit_message_text del mismo mensaje)

Requiere TELEGRAM_BOT_TOKEN propio (no compartido con hermes): Telegram permite
un solo consumidor de getUpdates por token.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime, timezone

from .config import Config
from . import db as database
from .cli import cmd_run
from .notify import esc, score_emoji, score_style, modality_tag, role_tag, techs_tag, age_tag

log = logging.getLogger("jobhunt.bot")


# ---------------------------------------------------------------- página render

def _score_row(j: dict) -> int:
    return int(j.get("score") or 0)


def render_page(offers: list[dict], page: int, page_size: int, cfg: Config) -> dict:
    """Renderiza página N del pool ordenado por score.

    Returns {"text": str, "keyboard": [[{text,url|callback_data,style}], ...]}.
    Botones por oferta = URL; navegación = callback_data jobs:page:N.
    """
    total = len(offers)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = offers[page * page_size:(page + 1) * page_size]

    stamp = datetime.now(timezone.utc).strftime("%d %b %H:%M")
    lines = [
        f"📬 <b>Ofertas</b> · <i>{total} activas</i> · {stamp}",
        f"Página <b>{page + 1}/{pages}</b> · {len(chunk)} en esta vista",
    ]
    if chunk:
        best = chunk[0]
        lines += [
            "",
            f"{score_emoji(best.get('score', 0))} <b>Mejor match:</b> {esc(best['title'][:70])}",
            f"   <code>{best.get('score', '?')}%</code> · {modality_tag(best.get('modality'))}"
            f" {role_tag(best['title'])} · {esc(best.get('company', '')[:25])}",
        ]
        if best.get("ai_fit_reason"):
            lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'][:140])}</i>")
    lines.append("")
    lines.append(f"<i>Toca el botón para abrir · umbral {cfg.alerts.min_score}%</i>")
    text = "\n".join(lines)[:4000]

    kb: list[list[dict]] = []
    for j in chunk:
        pct = j.get("score", 0)
        emp = (j.get("company") or "").strip()[:24]
        loc = (j.get("location") or "").split(",")[0].strip()[:18]
        label = (f"{score_emoji(pct)}{pct}% {modality_tag(j.get('modality'))}"
                 f"{role_tag(j['title'])}{age_tag(j.get('date_posted', ''))}")
        if techs_tag(j.get("title") or ""):
            label += f" {techs_tag(j['title'])}"
        if emp:
            label += f" {emp}"
        if loc:
            label += f" · {loc}"
        label = re.sub(r"\s+", " ", label).strip()[:140]
        kb.append([{"text": label, "url": j.get("url", ""), "style": score_style(pct)}])

    # navegación
    nav = []
    if page > 0:
        nav.append({"text": "«1", "callback_data": "jobs:page:0"})
        nav.append({"text": f"‹{page}", "callback_data": f"jobs:page:{page - 1}"})
    else:
        nav.append({"text": "·"})
    nav.append({"text": f"· {page + 1}/{pages} ·"})
    if page < pages - 1:
        nav.append({"text": f"{page + 2}›", "callback_data": f"jobs:page:{page + 1}"})
        nav.append({"text": f"{pages}»", "callback_data": f"jobs:page:{pages - 1}"})
    else:
        nav.append({"text": "·"})
    kb.append(nav)
    return {"text": text, "keyboard": kb}


# ---------------------------------------------------------------- telegram api

def _tg_api(cfg: Config, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def send_anchor(cfg: Config, offers: list[dict]) -> int | None:
    """Envía mensaje ancla paginado (página 0). Retorna message_id para editarlo después."""
    try:
        page = render_page(offers, 0, cfg.telegram.digest_page_size, cfg)
        kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in page["keyboard"]]
        body = {
            "chat_id": cfg.telegram.chat_id,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "text": page["text"],
            "reply_markup": json.dumps({"inline_keyboard": kb}),
        }
        resp = _tg_api(cfg, "sendMessage", body)
        return resp.get("result", {}).get("message_id")
    except Exception as exc:
        log.error("anchor send failed: %s", exc)
        return None


def handle_callback(cfg: Config, query: dict, offers: list[dict]) -> None:
    """callback jobs:page:N → edit_message_text del ancla con la página pedida."""
    qid = query.get("id")
    data = query.get("data") or ""
    msg = query.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    try:
        if data == "noop" or not data.startswith("jobs:page:"):
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
            return
        page = int(data.split(":")[2])
        rendered = render_page(offers, page, cfg.telegram.digest_page_size, cfg)
        kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rendered["keyboard"]]
        _tg_api(cfg, "editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": rendered["text"],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": json.dumps({"inline_keyboard": kb}),
        })
        _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
    except Exception as exc:
        log.error("callback failed (%s): %s", data, exc)
        try:
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid, "text": "⚠️ Error al cambiar de página"})
        except Exception:
            pass


# ---------------------------------------------------------------- daemon loop

def sweep(cfg: Config, state: dict):
    """Barrido completo + actualización del ancla."""
    cmd_run(cfg)
    conn = database.connect(cfg)
    try:
        state["offers"] = [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 AND score >= ? ORDER BY score DESC",
            (cfg.alerts.min_score,)).fetchall()]
    finally:
        conn.close()
    _refresh_anchor(cfg, state)


def _refresh_anchor(cfg: Config, state: dict):
    rendered = render_page(state["offers"], 0, cfg.telegram.digest_page_size, cfg)
    kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rendered["keyboard"]]
    body = {
        "chat_id": cfg.telegram.chat_id,
        "text": rendered["text"],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": json.dumps({"inline_keyboard": kb}),
    }
    try:
        if state["anchor_id"]:
            _tg_api(cfg, "editMessageText", {**body, "message_id": state["anchor_id"]})
            log.info("ancla actualizada (%d ofertas)", len(state["offers"]))
        else:
            resp = _tg_api(cfg, "sendMessage", body)
            state["anchor_id"] = resp.get("result", {}).get("message_id")
            log.info("ancla creada: message_id=%s (%d ofertas)", state["anchor_id"], len(state["offers"]))
    except Exception as exc:
        log.error("anchor refresh failed: %s", str(exc)[:200])


def run_daemon(cfg: Config) -> None:
    """Proceso residente: barrido periódico (cron interno) + polling de callbacks."""
    interval_min = cfg.daemon.interval_min
    log.info("daemon iniciado · barrido cada %d min · callbacks activos", interval_min)

    state: dict = {"offers": [], "anchor_id": None, "last_sweep": 0.0}

    # primer barrido inmediato en background
    threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()

    offset = 0
    while True:
        # 1. polling de callbacks (getUpdates — requiere token exclusivo del bot)
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{cfg.telegram.bot_token}/getUpdates"
                f"?timeout=50&offset={offset}&allowed_updates=%5B%22callback_query%22%5D")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                cq = upd.get("callback_query")
                if cq and (cq.get("data") or "").startswith(("jobs:page:", "noop")):
                    handle_callback(cfg, cq, state["offers"])
        except Exception as exc:
            log.warning("poll error: %s", str(exc)[:120])
            time.sleep(5)

        # 2. cron interno: ¿toca barrido?
        if time.time() - state["last_sweep"] >= interval_min * 60:
            state["last_sweep"] = time.time()
            threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()
