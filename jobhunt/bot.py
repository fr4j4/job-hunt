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
        m = re.fullmatch(r"(jobs|latest|sc\d+|f[a-z0-9.\-]*):page:(\d+)", data)
        if not m:
            _tg_api(cfg, "answerCallbackQuery", {"callback_query_id": qid})
            return
        prefix, page = m.group(1), int(m.group(2))
        if prefix == "jobs":
            rendered = render_page(state["offers"], page, cfg.telegram.digest_page_size, cfg)
        elif prefix == "latest":
            rendered = render_page(_latest_offers(cfg), page, cfg.telegram.digest_page_size, cfg,
                                   label="🆕 <b>Últimas registradas</b>", cb_prefix=prefix)
        elif prefix.startswith("f"):
            f = _dec_filters(prefix[1:])
            rendered = render_page(_filter_offers(cfg, f), page, cfg.telegram.digest_page_size, cfg,
                                   label=f"🔎 <b>Ofertas — {_describe_filters(f)}</b>", cb_prefix=prefix)
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

USD_CLP_RATE_DEFAULT = 950      # tasa de referencia para sueldos USD (filtros)


def _norm_txt(s: str) -> str:
    from .db import _norm_text
    return _norm_text(s or "")


def _salary_clp(cfg: Config, j: dict) -> float | None:
    """Salario mensual numérico en CLP (USD convertido con tasa de referencia)."""
    raw = (j.get("salary") or "").strip()
    if not raw or raw == "-":
        return None
    is_usd = "usd" in raw.lower()
    if not is_usd and not raw.lstrip().startswith(("$", "clp", "CLP")):
        digits = re.sub(r"\D", "", raw.split(",")[0])
        if digits and 0 < int(digits) < 10000:
            is_usd = True
    m = re.search(r"(\d[\d.,]*)", raw)
    if not m:
        return None
    rawnum = m.group(1)
    if rawnum.count(".") >= 1 and "," in rawnum:
        num = rawnum.replace(".", "").replace(",", ".")
    else:
        num = rawnum.replace(",", "")
    try:
        val = float(num)
    except ValueError:
        return None
    if is_usd:
        rate = getattr(cfg, "usd_clp_rate", None) or USD_CLP_RATE_DEFAULT
        return val * rate
    return val


def _parse_filters(tokens: list[str]) -> dict:
    """'remoto sueldo2.5 stgo' → {'modality': {...}, 'min_salary': 2.5e6, 'has_salary': False, 'loc': ['santiago']}"""
    f: dict = {"modality": set(), "min_salary": None, "has_salary": False, "loc": []}
    _MOD = {"remote": "remoto", "remoto": "remoto", "remota": "remoto",
            "hybrid": "híbrido", "hibrido": "híbrido", "hibrida": "híbrido",
            "onsite": "presencial", "presencial": "presencial"}
    _LOC = {"stgo": "santiago", "scl": "santiago", "santiago": "santiago",
            "valpo": "valparaiso", "valparaiso": "valparaiso",
            "conce": "concepcion", "concepcion": "concepcion",
            "araucania": "araucania", "temuco": "temuco"}
    for t in tokens:
        tl = _norm_txt(t).replace(":", "")
        if tl in _MOD:
            f["modality"].add(_MOD[tl])
            continue
        m_num = re.fullmatch(r"(?:salary|sueldo|min|pay|pago|>|>=)?([\d.,]+)\s*([mk]?)", tl)
        if m_num and any(ch.isdigit() for ch in tl) and tl not in ("min",):
            numstr = m_num.group(1)
            try:
                val = float(numstr.replace(",", "."))       # '2.5' / '2,5' → decimal
            except ValueError:
                try:
                    val = float(re.sub(r"\.(\d{3})", r"\1", numstr))  # '2.500.000' → miles
                except ValueError:
                    val = None
            if val is not None:
                suf = m_num.group(2)
                if suf == "k":
                    val *= 1_000
                elif suf == "m":
                    val *= 1_000_000
                elif val < 100:          # '2.5' → millones (convención chilena)
                    val *= 1_000_000
                f["min_salary"] = val
                continue
        if tl in ("salary", "paid", "withsalary", "sueldo", "pago"):
            f["has_salary"] = True
            continue
        f["loc"].append(_LOC.get(tl, tl))
    return f


def _describe_filters(f: dict) -> str:
    parts = []
    if f["modality"]:
        parts.append("/".join(sorted(f["modality"])))
    if f["has_salary"]:
        parts.append("con sueldo")
    if f["min_salary"] is not None:
        v = f["min_salary"]
        parts.append(f"≥${v / 1_000_000:.1f}M" if v >= 1_000_000 else f"≥${v:,.0f}")
    if f["loc"]:
        parts.append(" · ".join(f["loc"]))
    return " + ".join(parts) or "sin filtro"


def _filter_offers(cfg: Config, f: dict) -> list[dict]:
    """Aplica los filtros sobre el pool activo (ordenado por score)."""
    conn = database.connect(cfg)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 ORDER BY score DESC").fetchall()]
    finally:
        conn.close()
    out = []
    for j in rows:
        mod = (j.get("modality") or "").lower()
        if f["modality"] and not (
                mod in f["modality"]
                or ("remoto" in f["modality"] and j.get("remote_official") == 1)):
            continue
        if f["has_salary"] and not (j.get("salary") or "").strip():
            continue
        if f["min_salary"] is not None:
            v = _salary_clp(cfg, j)
            if v is None or v < f["min_salary"]:
                continue
        if f["loc"]:
            hay = " ".join([_norm_txt(j.get("location") or ""), _norm_txt(j.get("title") or "")])
            if not any(t in hay for t in f["loc"]):
                continue
        out.append(j)
    return out


def _score_offers(cfg: Config, threshold: int) -> list[dict]:
    """Ofertas activas ≥ threshold, máx 50, ordenadas por score."""
    conn = database.connect(cfg)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 AND score >= ? "
            "ORDER BY score DESC LIMIT 50", (threshold,)).fetchall()]
    finally:
        conn.close()


def _enc_filters(f: dict) -> str:
    """Serializa filtros para callback_data: {'remoto', ≥2.5M, santiago} → 'r-s2.5-lstgo'."""
    parts = []
    for m in sorted(f["modality"]):
        parts.append({"remoto": "r", "híbrido": "h", "presencial": "p"}.get(m, ""))
    if f["min_salary"] is not None:
        parts.append(f"s{f['min_salary'] / 1_000_000:.1f}")
    for loc in f["loc"]:
        parts.append("l" + loc.replace(" ", "")[:12])
    if f["has_salary"]:
        parts.append("q")
    return "-".join(parts)


def _dec_filters(enc: str) -> dict:
    """Inverso de _enc_filters (fallback si el daemon se reinició entre páginas)."""
    f: dict = {"modality": set(), "min_salary": None, "has_salary": False, "loc": []}
    for p in (enc or "").split("-"):
        if not p:
            continue
        if p in ("r", "h", "p"):
            f["modality"].add({"r": "remoto", "h": "híbrido", "p": "presencial"}[p])
        elif p == "q":
            f["has_salary"] = True
        elif p.startswith("s"):
            try:
                f["min_salary"] = float(p[1:]) * 1_000_000
            except ValueError:
                pass
        elif p.startswith("l"):
            f["loc"].append(p[1:])
    return f


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
        "/enrich — corre el batch IA ahora (modalidad, sueldo, inglés, techs…)",
        "/latest — últimas ofertas registradas",
        "/score N — ofertas con score ≥ N (ej: /score 60)",
        "/jobs [filtros] — filtra el pool (combinables):",
        "    remote · hybrid · onsite · salary (con sueldo publicado) ·",
        "    salary2.5 (≥$2.5M) · 2.5 / 500k / 2.500.000 ·",
        "    ubicación: stgo, temuco, valpo, conce, araucania o texto libre",
        "    ej: <code>/jobs remote salary2.5</code> · <code>/jobs temuco</code> · <code>/jobs hybrid stgo</code>",
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
        elif cmd == "/enrich":
            threading.Thread(target=_ia_batch_async, args=(cfg, chat_id), daemon=True).start()
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
        elif cmd == "/jobs":
            f = _parse_filters(parts[1:])
            offers = _filter_offers(cfg, f)
            if not offers:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"Nada con <b>{_describe_filters(f)}</b> en el pool activo."})
                return
            prefix = "f" + _enc_filters(f)
            rendered = render_page(offers, 0, cfg.telegram.digest_page_size, cfg,
                                   label=f"🔎 <b>Ofertas — {_describe_filters(f)}</b>",
                                   cb_prefix=prefix)
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


def _ia_batch_async(cfg: Config, chat_id: int | None):
    """Batch IA nocturno con reporte opcional al chat. Nunca tumba el daemon."""
    try:
        conn = database.connect(cfg)
        try:
            from .enrich import run_ia_batch, profile_description
            done = run_ia_batch(conn, cfg, profile_description(cfg))
        finally:
            conn.close()
        if chat_id:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": f"🧠 <b>Batch IA terminado</b> — {done} ofertas enriquecidas"})
        log.info("batch IA OK: %d ofertas", done)
    except Exception as exc:
        log.error("batch IA falló: %s", exc)
        if chat_id:
            try:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"⚠️ Batch IA falló: <code>{esc(str(exc)[:200])}</code>"})
            except Exception:
                pass


def _ia_hours_due(cfg: Config, last_ia: dict) -> bool:
    """True si la hora UTC actual está en IA_RUN_HOURS_UTC y no corrió en esa hora."""
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    key = now.strftime("%Y-%m-%d")
    return now.hour in cfg.ia.run_hours_utc and last_ia.get("day") != key


def _ia_sweep_maybe(cfg: Config, state: dict) -> None:
    """Dispara el batch IA si toca la hora agendada (3:00 UTC por defecto)."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return
    if not _ia_hours_due(cfg, state.setdefault("ia_log", {})):
        return
    state["ia_log"]["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("batch IA agendado (%s) — disparando", cfg.ia.run_hours_utc)
    threading.Thread(target=_ia_batch_async, args=(cfg, None), daemon=True).start()


def _register_commands(cfg: Config) -> None:
    """Registra los comandos en Telegram (menú "/" del cliente)."""
    commands = [
        {"command": "search", "description": "Gatilla una búsqueda ahora"},
        {"command": "enrich", "description": "Corre el batch IA ahora (rellena datos faltantes)"},
        {"command": "latest", "description": "Últimas ofertas registradas"},
        {"command": "score",  "description": "Ofertas con score ≥ N (ej: /score 60)"},
        {"command": "jobs",   "description": "Filtra: remote, salary2.5, temuco… combinables"},
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

    state: dict = {"offers": [], "anchor_id": None, "last_sweep": time.time()}
    _register_commands(cfg)

    # primer barrido inmediato en background (el cron interno no lo duplica:
    # last_sweep parte en now, no en 0.0)
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
                if cq and (cq.get("data") or "").startswith(("jobs:", "sc", "latest", "f", "noop")):
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

        # 3. batch IA nocturno (hora agendada en IA_RUN_HOURS_UTC, default 03)
        _ia_sweep_maybe(cfg, state)
