"""Modo daemon/bot: proceso residente con cron interno + paginación navegable + comandos.

Dos modos de operación del proyecto:
  jobhunt run    → ejecución simple: un barrido + 1 mensaje con las n ofertas nuevas
  jobhunt watch  → daemon: cron interno cada POLL_INTERVAL_MIN + digest paginado
                   navegable (callback_query → edit_message_text del mismo mensaje)
                   + comandos Telegram: /search /latest /score N /help

Requiere TELEGRAM_BOT_TOKEN propio (no compartido con hermes): Telegram permite
un solo consumidor de getUpdates por token.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import Config
from . import db as database
from .cli import cmd_run
from .notify import (esc, score_emoji, score_style, modality_tag, role_tag, techs_tag,
                     age_tag, salary_tag, compact_label, abbr_loc)

log = logging.getLogger("jobhunt.bot")

_sweep_lock = threading.Lock()


# ---------------------------------------------------------------- página render

def _score_row(j: dict) -> int:
    return int(j.get("score") or 0)


def render_page(offers: list[dict], page: int, page_size: int, cfg: Config,
                label: str | None = None, cb_prefix: str = "jobs") -> dict:
    """Renderiza página N: texto con títulos numerados + botones compactos (≤64 chars).

    La fila N del texto = botón N. El pago va después del porcentaje.
    label = encabezado alternativo (ej: "🎯 Ofertas ≥60"); cb_prefix = namespace
    de callback_data para la navegación (jobs = ancla, sc<umbral> = /score).
    """
    total = len(offers)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = offers[page * page_size:(page + 1) * page_size]

    stamp = datetime.now(timezone.utc).strftime("%d %b %H:%M")
    head = label or f"📬 <b>Ofertas ≥{cfg.alerts.min_score}</b>"
    lines = [
        f"{head} · <i>{total} activas</i> · {stamp}",
        f"Página <b>{page + 1}/{pages}</b>",
    ]
    if chunk:
        best = chunk[0]
        lines += [
            "",
            f"{score_emoji(best.get('score', 0))} <b>Mejor match:</b> {esc(best['title'][:70])}",
            f"   <code>{best.get('score', '?')}%</code>{salary_tag(best)}"
            f" · {modality_tag(best.get('modality'))} {role_tag(best['title'])}"
            f" · {age_tag(best.get('date_posted', ''))} · {esc((best.get('company') or '')[:24])}"
            f" · {esc(abbr_loc(best.get('location') or ''))}",
        ]
        if best.get("ai_fit_reason"):
            lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'][:140])}</i>")
        lines += ["", "<b>Esta página:</b>"]
        for i, j in enumerate(chunk, 1):
            money = salary_tag(j)
            title = esc((j.get("title") or "?")[:52])
            lines.append(f"{i}. {title} ({j.get('score', '?')}%){money}")
    lines.append("")
    lines.append("<i>Toca el botón para abrir — fila N = botón N</i>")
    text = "\n".join(lines)[:4000]

    kb = [[{"text": compact_label(j), "url": j.get("url", ""), "style": score_style(j.get("score", 0))}]
          for j in chunk]

    # navegación (todo botón debe tener acción: los inertes usan callback_data="noop")
    nav = []
    if page > 0:
        nav.append({"text": "«1", "callback_data": f"{cb_prefix}:page:0"})
        nav.append({"text": f"‹{page}", "callback_data": f"{cb_prefix}:page:{page - 1}"})
    nav.append({"text": f"· {page + 1}/{pages} ·", "callback_data": "noop"})
    if page < pages - 1:
        nav.append({"text": f"{page + 2}›", "callback_data": f"{cb_prefix}:page:{page + 1}"})
        nav.append({"text": f"{pages}»", "callback_data": f"{cb_prefix}:page:{pages - 1}"})
    kb.append(nav)
    return {"text": text, "keyboard": kb}


# ---------------------------------------------------------------- telegram api

def _chat_allowed(cfg: Config, chat_id) -> bool:
    """True si el chat está en TELEGRAM_ALLOWED_CHATS (vacío = sin restricción)."""
    if not cfg.telegram.allowed_chats:
        return True
    try:
        return int(chat_id) in cfg.telegram.allowed_chats
    except (TypeError, ValueError):
        return False


def _tg_api(cfg: Config, method: str, payload: dict, retries: int = 2) -> dict:
    cid = payload.get("chat_id")
    if cid is not None and not _chat_allowed(cfg, cid):
        raise PermissionError(f"chat {cid} no está en TELEGRAM_ALLOWED_CHATS")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # 403/429 transitorios (membership lag / rate limit) → backoff corto y retry
            if exc.code in (403, 429) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("unreachable")


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


def handle_callback(cfg: Config, query: dict, state: dict) -> None:
    """callback jobs:page:N (ancla) o sc<umbral>:page:N (vista /score) → edit_message_text."""
    qid = query.get("id")
    data = query.get("data") or ""
    msg = query.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    if not _chat_allowed(cfg, chat_id):
        log.warning("callback ignorado: chat %s fuera del allowlist", chat_id)
        try:
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
        except Exception:
            pass
        return
    try:
        if data == "noop":
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
            return
        m = re.fullmatch(r"(jobs|latest|sc\d+):page:(\d+)", data)
        if not m:
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
            return
        prefix, page = m.group(1), int(m.group(2))
        if prefix == "jobs":
            rendered = render_page(state["offers"], page, cfg.telegram.digest_page_size, cfg)
        elif prefix == "latest":
            rendered = render_page(_latest_offers(cfg), page, cfg.telegram.digest_page_size, cfg,
                                   label="🆕 <b>Últimas registradas</b>", cb_prefix=prefix)
        else:
            th = int(prefix[2:])
            offers = _score_offers(cfg, th)
            rendered = render_page(offers, page, cfg.telegram.digest_page_size, cfg,
                                   label=f"🎯 <b>Ofertas ≥{th}</b>", cb_prefix=prefix)
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


# ---------------------------------------------------------------- comandos del bot

def _score_offers(cfg: Config, threshold: int) -> list[dict]:
    """Ofertas activas ≥ threshold, máx 50, ordenadas por score."""
    conn = database.connect(cfg)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 AND score >= ? "
            "ORDER BY score DESC LIMIT 50", (threshold,)).fetchall()]
    finally:
        conn.close()


def _latest_offers(cfg: Config, n: int = 5) -> list[dict]:
    """Últimas n ofertas registradas (por primera vez vistas), activas."""
    conn = database.connect(cfg)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 ORDER BY first_seen DESC, score DESC LIMIT ?",
            (n,)).fetchall()]
    finally:
        conn.close()


def _run_search_async(cfg: Config, chat_id: int):
    """Barrido en background con reporte de inicio/término/error al chat."""
    try:
        _tg_api(cfg, "sendMessage", {"chat_id": chat_id,
                                     "text": "🔍 <b>Búsqueda iniciada</b> — barriendo fuentes…",
                                     "parse_mode": "HTML"})
        offers, stats = _do_sweep(cfg)
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        msg = (f"✅ <b>Búsqueda terminada</b> ({stamp})\n"
               f"   Vistas: <code>{stats.get('total_seen', 0)}</code> · "
               f"Nuevas: <code>{stats.get('new_count', 0)}</code>\n"
               f"   Activas ≥{cfg.alerts.min_score}: <code>{len(offers)}</code>")
        if offers:
            best = max(offers, key=lambda o: o.get("score", 0))
            msg += f"\n   Mejor: {esc((best.get('title') or '?')[:60])} ({best.get('score', 0)}%)"
        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                                     "disable_web_page_preview": True})
        log.info("/search: barrido OK (%d vistas, %d nuevas)", stats.get("total_seen", 0),
                 stats.get("new_count", 0))
    except Exception as exc:
        log.error("/search falló: %s", exc)
        try:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": f"⚠️ <b>Error en la búsqueda</b>\n<code>{esc(str(exc)[:300])}</code>"})
        except Exception:
            pass


def _help_text() -> str:
    return "\n".join([
        "🤖 <b>Comandos del bot</b>",
        "",
        "/search — gatilla una búsqueda ahora (reporta inicio, término y error)",
        "/latest — últimas ofertas registradas",
        "/score N — ofertas con score ≥ N (ej: /score 60)",
        "/help — esta ayuda",
    ])


def _handle_command(cfg: Config, message: dict, state: dict) -> None:
    """Despacha comandos de texto. Solo chats del allowlist."""
    chat_id = (message.get("chat") or {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return
    if not _chat_allowed(cfg, chat_id):
        log.warning("comando ignorado: chat %s fuera del allowlist", chat_id)
        return
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()      # /score@MiBot → /score
    arg = parts[1] if len(parts) > 1 else ""
    log.info("comando %s (chat %s)", cmd, chat_id)
    try:
        if cmd == "/search":
            threading.Thread(target=_run_search_async, args=(cfg, chat_id), daemon=True).start()
        elif cmd == "/latest":
            offers = _latest_offers(cfg)
            rendered = render_page(offers, 0, cfg.telegram.digest_page_size, cfg,
                                   label="🆕 <b>Últimas registradas</b>", cb_prefix="latest")
            kb = [[{k: v for k, v in b.items() if k != "style"} for b in row]
                  for row in rendered["keyboard"]]
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "text": rendered["text"], "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": json.dumps({"inline_keyboard": kb})})
        elif cmd == "/score":
            try:
                th = int(arg)
            except ValueError:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": "Uso: <code>/score N</code> — ej: <code>/score 60</code>"})
                return
            th = max(0, min(100, th))
            offers = _score_offers(cfg, th)
            if not offers:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"Nada con score ≥{th} en el pool activo."})
                return
            prefix = f"sc{th}"
            rendered = render_page(offers, 0, cfg.telegram.digest_page_size, cfg,
                                   label=f"🎯 <b>Ofertas ≥{th}</b>", cb_prefix=prefix)
            kb = [[{k: v for k, v in b.items() if k != "style"} for b in row]
                  for row in rendered["keyboard"]]
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "text": rendered["text"], "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": json.dumps({"inline_keyboard": kb})})
        elif cmd in ("/help", "/start"):
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": _help_text()})
        else:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": "Comando no reconocido. <code>/help</code> para ver los disponibles."})
    except Exception as exc:
        log.error("comando %s falló: %s", cmd, exc)
        try:
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id,
                                         "text": f"⚠️ Error ejecutando {cmd}: {esc(str(exc)[:200])}"})
        except Exception:
            pass


def _register_commands(cfg: Config) -> None:
    """Registra los comandos en Telegram (menú "/" del cliente)."""
    commands = [
        {"command": "search", "description": "Gatilla una búsqueda ahora"},
        {"command": "latest", "description": "Últimas ofertas registradas"},
        {"command": "score",  "description": "Ofertas con score ≥ N (ej: /score 60)"},
        {"command": "help",   "description": "Ayuda"},
    ]
    try:
        _tg_api(cfg, "setMyCommands", {"commands": commands})
        log.info("comandos registrados en Telegram")
    except Exception as exc:
        log.warning("setMyCommands falló: %s", exc)


# ---------------------------------------------------------------- daemon loop

def _do_sweep(cfg: Config) -> tuple[list[dict], dict]:
    """Barrido completo + pool refrescado. Retorna (ofertas ≥min_score, stats del scan_log)."""
    with _sweep_lock:
        cmd_run(cfg, notify=False)          # barrido SIN mensaje push
        conn = database.connect(cfg)
        try:
            offers = [dict(r) for r in conn.execute(
                "SELECT * FROM ofertas WHERE active=1 AND score >= ? ORDER BY score DESC",
                (cfg.alerts.min_score,)).fetchall()]
            row = conn.execute(
                "SELECT ts, total_seen, new_count FROM scan_log ORDER BY id DESC LIMIT 1").fetchone()
            stats = dict(row) if row else {"ts": "", "total_seen": 0, "new_count": 0}
        finally:
            conn.close()
    return offers, stats


def sweep(cfg: Config, state: dict):
    """Barrido + refresh del pool + ancla (cron interno del daemon). Nunca mata el thread."""
    try:
        offers, _stats = _do_sweep(cfg)
        state["offers"] = offers
        _refresh_anchor(cfg, state)
    except Exception as exc:
        log.error("sweep del cron falló: %s", exc)


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
    """Proceso residente: barrido periódico (cron interno) + polling de callbacks y comandos."""
    interval_min = cfg.daemon.interval_min
    log.info("daemon iniciado · barrido cada %d min · callbacks + comandos activos", interval_min)

    state: dict = {"offers": [], "anchor_id": None, "last_sweep": 0.0}
    _register_commands(cfg)

    # primer barrido inmediato en background
    threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()

    offset = 0
    while True:
        # 1. polling: comandos de texto + callbacks de paginación
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{cfg.telegram.bot_token}/getUpdates"
                f"?timeout=50&offset={offset}"
                f"&allowed_updates=%5B%22callback_query%22%2C%22message%22%5D")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                cq = upd.get("callback_query")
                if cq and (cq.get("data") or "").startswith(("jobs:", "sc", "latest", "noop")):
                    handle_callback(cfg, cq, state)
                    continue
                msg = upd.get("message")
                if msg:
                    _handle_command(cfg, msg, state)
        except Exception as exc:
            log.warning("poll error: %s", str(exc)[:120])
            time.sleep(5)

        # 2. cron interno: ¿toca barrido?
        if time.time() - state["last_sweep"] >= interval_min * 60:
            state["last_sweep"] = time.time()
            threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()
