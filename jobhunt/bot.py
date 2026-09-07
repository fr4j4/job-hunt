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
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from . import db as database
from .cli import cmd_run
from .notify import (esc, score_emoji, score_style, modality_tag, role_tag, techs_tag,
                     age_tag, salary_tag, compact_label, abbr_loc, table_block, _age_short)
# compat: re-export — eliminar en v6 cuando los imports apunten al paquete nuevo
from .telegram.api import TelegramClient
from .app.state import IAState, SearchState, StopEvent

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
            f" · {_age_short(best.get('date_posted', ''))} · {esc((best.get('company') or '')[:24])}"
            f" · {esc(abbr_loc(best.get('location') or ''))}",
        ]
        if best.get("ai_fit_reason"):
            lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'][:140])}</i>")
        lines += ["", "<b>Esta página</b> (toca el 🔗 para abrir):", table_block(chunk)]
    lines.append("")
    lines.append("<i>⭐ ≥85 · 🟢 ≥70 · 🟡 ≥55 · ⚪ resto · * = IA · toca el 🔗 para abrir</i>")
    text = "\n".join(lines)[:4000]

    kb = []   # solo navegación — el link está en el título de cada fila

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

def _kb_json(rows: list[list[dict]]) -> str:
    """Keyboard para la API de Telegram (style no va en el JSON crudo)."""
    kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rows]
    return json.dumps({"inline_keyboard": kb})


def _tg_client(cfg: Config) -> TelegramClient:
    """Instancia módulo-level derivada de cfg: allowlist efectiva (SEC-1/SEC-8) =
    TELEGRAM_ALLOWED_CHATS UNION {telegram.chat_id, channel.chat_id} (los no vacíos).
    Así el canal siempre puede recibir posts aunque no esté en TELEGRAM_ALLOWED_CHATS.
    """
    allowed = set(cfg.telegram.allowed_chats)
    for extra in (cfg.telegram.chat_id, cfg.channel.chat_id):
        if extra:
            try:
                allowed.add(int(extra))
            except (TypeError, ValueError):
                pass
    return TelegramClient(cfg.telegram.bot_token, allowed)


def _chat_allowed(cfg: Config, chat_id) -> bool:
    """True si el chat está en la allowlist efectiva (SEC-1/SEC-8).

    La allowlist efectiva casi nunca está vacía (_tg_client une chat_id y
    channel.chat_id), así que el modo dev "vacía -> True" es residual: si
    TELEGRAM_ALLOWED_CHATS no lista un chat secundario, queda rechazado y
    TelegramClient.chat_allowed lo loguea a WARNING con su chat_id.
    """
    return _tg_client(cfg).chat_allowed(chat_id)


def _tg_edit_or_send(cfg: Config, chat_id: int, message_id: int | None,
                     body: dict) -> int | None:
    """Edita el mensaje con body; si no existe (borrado por el usuario) manda uno nuevo.

    Retorna el message_id vigente tras la operación (el nuevo si recreó).
    """
    if message_id:
        try:
            _tg_api(cfg, "editMessageText", {**body, "chat_id": chat_id,
                                             "message_id": message_id})
            return message_id
        except Exception as exc:
            # "message is not modified" = el mensaje existe, solo no cambió → NO recrear
            if "not modified" in str(exc).lower():
                return message_id
            if "not found" in str(exc).lower() or "message to edit" in str(exc).lower():
                log.info("mensaje vivo %s ya no existe (borrado) — recreando", message_id)
            else:
                log.warning("editMessageText falló (%.80s) — intentando recrear", str(exc))
    resp = _tg_api(cfg, "sendMessage", {**body, "chat_id": chat_id})
    return (resp or {}).get("result", {}).get("message_id")


def _tg_api(cfg: Config, method: str, payload: dict, retries: int = 2) -> dict:
    return _tg_client(cfg).call(method, retries=retries, **payload)


def _tg_send_document(cfg: Config, chat_id: int, path: str, caption: str = "") -> bool:
    """sendDocument multipart/form-data (archivos binarios: PDFs, imágenes)."""
    import mimetypes
    import uuid
    from pathlib import Path as _P

    if not _chat_allowed(cfg, chat_id):
        raise PermissionError(f"chat {chat_id} no está en TELEGRAM_ALLOWED_CHATS")
    file = _P(path)
    if not file.exists():
        return False
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    def field(name: str, value: str):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption[:1024])
        field("parse_mode", "HTML")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; "
                 f"filename=\"{file.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    parts.append(file.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/sendDocument",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception as e:
        log.error("sendDocument falló: %s", e)
        return False


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
            "reply_markup": _kb_json(kb),
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
        m = re.fullmatch(r"(jobs|latest|sc\d+s?|f[a-z0-9.\-]*):page:(\d+)", data)
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
            sc = prefix[2:]  # ej: "60s" → th=60, solo_sueldo=True
            solo_s = sc.endswith("s")
            th = int(sc[:-1] if solo_s else sc)
            offers = _score_offers(cfg, th, solo_s)
            label = f"🎯 <b>Ofertas ≥{th}</b>"
            if solo_s:
                label += " 💰 con sueldo"
            rendered = render_page(offers, page, cfg.telegram.digest_page_size, cfg,
                                   label=label, cb_prefix=prefix)
        kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rendered["keyboard"]]
        _tg_api(cfg, "editMessageText", {
            "chat_id": chat_id,
            "message_id": msg.get("message_id"),
            "text": rendered["text"],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": _kb_json(kb),
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
    f: dict = {"modality": set(), "min_salary": None, "has_salary": False, "loc": [],
               "no_excluyente": False, "lang": ""}
    _MOD = {"remote": "remoto", "remoto": "remoto", "remota": "remoto",
            "hybrid": "híbrido", "hibrido": "híbrido", "hibrida": "híbrido",
            "onsite": "presencial", "presencial": "presencial"}
    _LOC = {"stgo": "santiago", "scl": "santiago", "santiago": "santiago",
            "valpo": "valparaiso", "valparaiso": "valparaiso",
            "conce": "concepcion", "concepcion": "concepcion",
            "araucania": "araucania", "temuco": "temuco"}
    _LANG = {"en": "inglés", "ingles": "inglés", "english": "inglés",
             "aleman": "alemán", "frances": "francés", "portugues": "portugués"}
    for t in tokens:
        tl = _norm_txt(t).replace(":", "")
        if tl in _MOD:
            f["modality"].add(_MOD[tl])
            continue
        if tl in ("sinen", "noen", "sinexcluyente"):
            f["no_excluyente"] = True
            continue
        if tl in _LANG:
            f["lang"] = _LANG[tl]
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
    if f["lang"]:
        parts.append(f"pide {f['lang']}")
    if f["no_excluyente"]:
        parts.append("sin idioma excluyente")
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
        # idiomas: parsear ai_idiomas una vez
        idiomas = []
        try:
            idiomas = json.loads(j.get("ai_idiomas") or "[]")
            if not isinstance(idiomas, list):
                idiomas = []
        except Exception:
            idiomas = []
        if f["lang"]:
            if not any((i.get("idioma") or "").lower() == f["lang"] for i in idiomas if isinstance(i, dict)):
                continue
        if f["no_excluyente"]:
            # excluir ofertas donde CUALQUIER idioma venga marcado excluyente
            # (si la oferta no pasó por el re-pase de idiomas, no la bloqueamos)
            if idiomas and any(i.get("excluyente") for i in idiomas if isinstance(i, dict)):
                continue
        out.append(j)
    return out


def _score_offers(cfg: Config, threshold: int, solo_sueldo: bool = False) -> list[dict]:
    """Ofertas activas ≥ threshold, máx 50, ordenadas por score.
    solo_sueldo=True → solo ofertas CON sueldo declarado (salary != '')."""
    conn = database.connect(cfg)
    try:
        sql = "SELECT * FROM ofertas WHERE active=1 AND score >= ? "
        if solo_sueldo:
            sql += "AND salary != '' "
        return [dict(r) for r in conn.execute(
            sql + "ORDER BY score DESC LIMIT 50", (threshold,)).fetchall()]
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
    if f["no_excluyente"]:
        parts.append("nx")
    if f["lang"]:
        parts.append("g" + f["lang"][:8].replace(" ", ""))
    return "-".join(parts)


def _dec_filters(enc: str) -> dict:
    """Inverso de _enc_filters (fallback si el daemon se reinició entre páginas)."""
    f: dict = {"modality": set(), "min_salary": None, "has_salary": False, "loc": [],
               "no_excluyente": False, "lang": ""}
    for p in (enc or "").split("-"):
        if not p:
            continue
        if p in ("r", "h", "p"):
            f["modality"].add({"r": "remoto", "h": "híbrido", "p": "presencial"}[p])
        elif p == "q":
            f["has_salary"] = True
        elif p == "nx":
            f["no_excluyente"] = True
        elif p.startswith("g"):
            f["lang"] = p[1:]
        elif p.startswith("s"):
            try:
                f["min_salary"] = float(p[1:]) * 1_000_000
            except ValueError:
                pass
        elif p.startswith("l"):
            f["loc"].append(p[1:])
    return f


def _latest_offers(cfg: Config, n: int = 10) -> list[dict]:
    """Últimas n ofertas registradas (por primera vez vistas), activas."""
    conn = database.connect(cfg)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 ORDER BY first_seen DESC, score DESC LIMIT ?",
            (n,)).fetchall()]
    finally:
        conn.close()


_SEARCH_STATE = SearchState()
_STOP_EVENT = StopEvent()   # /stop: termina search/IA/report en su próximo punto seguro
_REPORT_STATE: dict = {"running": False, "phase": 0, "phase_msg": "", "t0": 0.0,
                       "pdf": ""}


def _op_busy() -> str | None:
    """'search' | 'ia' | 'report' si hay una operación pesada corriendo, None si libre."""
    if _SEARCH_STATE["running"]:
        return "search"
    if _IA_STATE["running"]:
        return "ia"
    if _REPORT_STATE["running"]:
        return "report"
    return None


def _op_minutes(op: str) -> int:
    t0 = (_SEARCH_STATE["t0"] if op == "search"
          else _REPORT_STATE["t0"] if op == "report"
          else _IA_STATE["t0"])
    return int(time.time() - t0) // 60 if t0 else 0


def _run_search_async(cfg: Config, chat_id: int):
    """Barrido en background con mensaje vivo que muta por fuente/query/página."""
    msg_id = None
    try:
        _SEARCH_STATE.update(running=True, t0=time.time())
        sent = _tg_api(cfg, "sendMessage", {"chat_id": chat_id,
                                            "text": "🔍 <b>Búsqueda iniciada</b> — barriendo fuentes…",
                                            "parse_mode": "HTML"})
        msg_id = (sent or {}).get("result", {}).get("message_id")
        t0 = time.time()
        fase = {"fuente": "preparando", "query": "", "page": 0, "detail": ""}

        def on_phase(fuente: str, query: str = "", page: int = 0, detail: str = ""):
            """Actualiza estado y edita el MISMO mensaje (throttle 15s)."""
            fase.update(fuente=fuente, query=query, page=page, detail=detail)
            nonlocal msg_id
            if not msg_id or time.time() - t0 < 15:
                return
            mins = int(time.time() - t0) // 60
            linea = f"   ▸ <b>{esc(fuente)}</b>"
            if query:
                linea += f' — "{esc(query[:40])}"'
            if page:
                linea += f" · pág {page}"
            if fase.get("detail"):
                linea += f"\n   {esc(fase['detail'][:60])}"
            try:
                msg_id = _tg_edit_or_send(cfg, chat_id, msg_id, {
                    "text": (f"🔍 <b>Búsqueda en curso</b> ({mins}m)\n{linea}"),
                    "parse_mode": "HTML"})
            except Exception:
                pass  # flood o mensaje igual → no tumba el barrido

        offers, stats = _do_sweep(cfg, on_phase=on_phase)
        # resumen final: borrar el mensaje vivo y mandar el resultado limpio
        if msg_id:
            try:
                _tg_api(cfg, "deleteMessage", {"chat_id": chat_id, "message_id": msg_id})
            except Exception:
                pass
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        dur = int(time.time() - t0)
        msg = (f"✅ <b>Búsqueda terminada</b> ({stamp} · {dur // 60}m{dur % 60:02d}s)\n"
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
    finally:
        _SEARCH_STATE.update(running=False, t0=0.0)


def _help_text() -> str:
    return "\n".join([
        "🤖 <b>Comandos del bot</b>",
        "",
        "🔎 <b>Búsqueda y pool</b>",
        "/search — gatilla una búsqueda ahora (reporta inicio, término y error)",
        "/latest — últimas ofertas registradas (default 10, /latest 20 para más)",
        "/score N — ofertas con score ≥ N (ej: /score 60 · /score 60 s solo con sueldo)",
        "/jobs [filtros] — filtra el pool (combinables):",
        "    remote · hybrid · onsite · salary (con sueldo publicado) ·",
        "    salary2.5 (≥$2.5M) · 2.5 / 500k / 2.500.000 ·",
        "    sinen (sin idioma excluyente) · en (pide inglés)",
        "    ubicación: stgo, temuco, valpo, conce, araucania o texto libre",
        "    ej: <code>/jobs remote salary2.5</code> · <code>/jobs sinen</code> · <code>/jobs en remote</code>",
        "",
        "🧠 <b>Enriquecimiento IA</b>",
        "/enrich — corre el batch IA ahora (modalidad, sueldo, inglés, techs…)",
        "/enrich_all — TODAS las activas sin IA con descripción (incluye completas)",
        "/enrich status — avance del batch IA (o tamaño de la cola)",
        "/stop — detiene la operación en curso (search/enrich/report) con corte limpio",
        "",
        "📊 <b>Análisis y estado</b>",
        "/report — análisis completo del mercado con gráficos → PDF",
        "/report status · /report list — avance del reporte · historial de PDFs",
        "/stats — cobertura del pool (procesadas IA, datos faltantes)",
        "/config — configuración actual (tokens enmascarados)",
        "/preview — oferta aleatoria como se vería en el canal (sin marcar publicada)",
        "/preview 80 — aleatoria con market_score >= 80 · /preview java — filtra por texto",
        "/preview ... s — SOLO ofertas con sueldo declarado (ej: /preview 80 s)",
        "",
        "📢 <b>Canal (broadcast)</b>",
        "/channel — estado del canal (umbral, cola, distribución market score)",
        "/channel_publish — publica las candidatas en cola ahora",
        "/channel_publish_ia — igual, pero SOLO ofertas ya revisadas por IA",
        "/channel_daily — digest diario 'Top del día' ahora",
        "/channel_weekly — digests semanales ahora (remote + rol + techs + salary)",
        "/channel_weekly_remote — solo digest remoto por seniority",
        "/channel_weekly_rol — solo mejor de la semana por rol",
        "/channel_weekly_techs — solo tecnologías del mercado (top + emergentes + salarios)",
        "/channel_weekly_salary — solo ranking salarial",
        "/channel_trends — post mensual de tendencias",
        "/channel_all — publish + todos los digests",
        "/channel_dry — preview de lo que publicaría (sin publicar)",
        "/channel_reset_confirm — limpia marcas: todas vuelven a ser candidatas",
        "/channel_wipe_confirm — borra TODOS los mensajes del canal + resetea marcas",
        "",
        "🧪 <b>Reportes de prueba (DM)</b> — mismos digests, enviados a este chat",
        "/report_daily · /report_weekly_remote · /report_weekly_rol · /report_weekly_techs · /report_weekly_salary · /report_trends · /report_all",
        "",
        "🗄 <b>Mantenimiento DB</b>",
        "/db — conteos (activas, inactivas, purgables, no-dev)",
        "/db_old_confirm — elimina físicamente inactivas >30 días",
        "/db_nondev_confirm — elimina físicamente las no-dev activas",
        "/db_all_confirm — borra TODO el pool (backup previo automático)",
        "/db_iaclear_confirm — limpia marca IA: todo vuelve a la cola (regenera comentarios)",
        "",
        "/help — esta ayuda",
    ])


def _config_text(cfg: Config) -> str:
    """Config actual (comando /config) — enmascara tokens/keys (nunca mostrar secretos)."""
    from .notify import esc
    def mask(v: str) -> str:
        v = v or ""
        if len(v) <= 8:
            return "***"
        return v[:4] + "…" + v[-4:]

    ia = cfg.ia
    ch = cfg.channel
    tg = cfg.telegram
    lines = [
        "⚙️ <b>Configuración</b>",
        f"🤖 <b>IA</b>",
        f"  enabled: {ia.enabled} · modelo: <code>{esc(ia.model)}</code>",
        f"  base_url: <code>{esc(ia.base_url)}</code> · api_key: <code>{mask(ia.api_key)}</code>",
        f"  timeout: {ia.timeout}s · retries: {ia.retries} · concurrency: {ia.concurrency}",
        f"  batch_prompt: {ia.batch_prompt} · batch_size: {ia.batch_size}",
        f"  reasoning_effort: {ia.reasoning_effort or 'off'}",
        f"  <b>local</b>: {'ON' if ia.local_enabled else 'OFF'} · modelo: <code>{esc(ia.local_model)}</code>",
        f"  local_url: <code>{esc(ia.local_base_url)}</code> · timeout: {ia.local_timeout}s",
        f"  fallback_cloud: {ia.local_fallback_cloud} · concurrency: {ia.local_concurrency}",
        f"📢 <b>Canal</b>",
        f"  enabled: {ch.enabled} · umbral: {ch.min_score} · ventana: {ch.max_age_days}d",
        f"  dev-gate: {'ON' if ch.require_dev else 'OFF'} · max_posts: {ch.max_posts}",
        f"  digest_min: {ch.digest_min_score} · max_posts_sweep: {ch.max_posts_per_sweep}",
        f"📡 <b>Telegram</b>",
        f"  chat: <code>{esc(str(tg.chat_id))}</code> · allowed: {len(tg.allowed_chats)} chats",
        f"  token: <code>{mask(tg.bot_token)}</code>",
        f"🔎 <b>Búsqueda</b>",
        f"  horas premium: {cfg.premium_hours} · jooble_key: <code>{mask(cfg.jooble_api_key)}</code>",
        f"  fuentes: {', '.join(k for k, v in cfg.sources.items() if v) or 'ninguna'}",
        f"📊 <b>Alertas</b>: min {cfg.alerts.min_score} · worth_it {cfg.alerts.worth_it_score}",
    ]
    return "\n".join(lines)


def _parse_preview_arg(arg: str) -> tuple[str | None, str, bool]:
    """Parsea el arg de /preview → (filtro, modo, solo_sueldo).

    modos: 'score' (N), 'texto' (match título/empresa), '' (aleatorio).
    El token 's'/'sal' al final activa solo ofertas CON sueldo declarado.
    /preview 80 s → score>=80 con sueldo · /preview s → aleatoria con sueldo
    /preview java s → matchea 'java' con sueldo
    """
    solo_sueldo = False
    tokens = arg.split()
    resto = []
    for t in tokens:
        tl = t.lower()
        if tl in ("s", "sal", "sueldo"):
            solo_sueldo = True
        else:
            resto.append(t)
    if resto and resto[0].isdigit():
        return resto[0], "score", solo_sueldo
    if resto:
        return " ".join(resto), "texto", solo_sueldo
    return None, "", solo_sueldo


def _preview_offer(cfg: Config, chat_id, arg: str = ""):
    """Comando /preview: oferta aleatoria renderizada como en el canal (DM).

    NO marca notified_channel_at — es solo previsualización.
    /preview → aleatoria con IA · /preview N → market_score >= N
    /preview <texto> → aleatoria que matchee título/empresa
    /preview ... s → SOLO ofertas con sueldo declarado (ej: /preview 80 s)
    """
    from .channel import render_offer_post
    conn = database.connect(cfg)
    try:
        where = "active=1 AND ia_model != ''"
        params: list = []
        filtro, modo, solo_sueldo = _parse_preview_arg(arg)
        if modo == "score" and filtro is not None:
            where += " AND market_score >= ?"
            params.append(int(filtro))
        elif modo == "texto":
            where += " AND (title LIKE ? OR company LIKE ?)"
            like = f"%{filtro}%"
            params.extend([like, like])
        if solo_sueldo:
            where += " AND salary != ''"
        rows = conn.execute(
            f"SELECT * FROM ofertas WHERE {where} ORDER BY RANDOM() LIMIT 1",
            params).fetchall()
        if not rows:
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": "🔍 No encontré ofertas con ese filtro"})
            return
        r = dict(rows[0])
        text, kb = render_offer_post(r)
        payload: dict = {"chat_id": chat_id, "parse_mode": "HTML",
                         "text": text, "disable_web_page_preview": True}
        if kb:
            payload["reply_markup"] = kb
        _tg_api(cfg, "sendMessage", payload)
        log.info("preview enviado: %s (chat %s)", r.get("group_id", "?"), chat_id)
    finally:
        conn.close()


def _stats_text(cfg: Config) -> str:
    """Cobertura del pool: procesadas IA, pendientes, qué datos faltan.
    'En cola IA' usa el criterio C9 real (procesables por el enrich) + separa
    las que requieren scan (sin ficha) — feedback honesto (fix feedback engañoso)."""
    conn = database.connect(cfg)
    try:
        q = lambda s: conn.execute(s).fetchone()[0]
        total = q("SELECT COUNT(*) FROM ofertas WHERE active=1")
        ia = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model != ''")
        con_mod = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND modality != ''")
        con_sal = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND salary != ''")
        # C9 ampliado (mismo criterio que el ACK del enrich): procesables AHORA
        en_cola = q("""SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model=''
                       AND (length(description)>200 OR description_source!='')
                       AND (modality='' OR salary='' OR description IS NULL OR
                            salary_status IN ('implausible','suspect'))""")
        # sin IA + sin descripción suficiente → requieren scan (no enrich)
        sin_ficha = q("""SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model=''
                         AND NOT (length(description)>200 OR description_source!='')""")
        # modelo ACTIVO (config) — no el de la última oferta por last_seen
        # (last_seen refleja el search, no cuándo se procesó con IA — engañoso)
        modelo_activo = cfg.ia.local_model if cfg.ia.local_enabled else cfg.ia.model
        lines = [
            "📊 <b>Estado del pool</b>",
            "",
            f"Activas: <code>{total}</code>",
            f"🧠 Procesadas por IA: <code>{ia}</code> ({ia * 100 // max(total, 1)}%)",
            f"   Modelo: <code>{modelo_activo}</code>",
            f"   En cola IA: <code>{en_cola}</code> (procesables con /enrich)",
            f"   Sin ficha: <code>{sin_ficha}</code> (requieren /search para bajar descripción)",
            "",
            f"Con modalidad: <code>{con_mod}</code> · Con sueldo: <code>{con_sal}</code>",
        ]
        return "\n".join(lines)
    finally:
        conn.close()


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
    arg = " ".join(parts[1:]).strip()          # TODOS los tokens: '/score 60 s' → '60 s'
    log.info("comando %s (chat %s)", cmd, chat_id)
    try:
        if cmd == "/search":
            busy = _op_busy()
            if busy:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"⏳ Hay una operación en curso ({busy}, {_op_minutes(busy)}m) — "
                            f"espera que termine antes de lanzar otra"})
                return
            threading.Thread(target=_run_search_async, args=(cfg, chat_id), daemon=True).start()
        elif cmd == "/enrich":
            if arg.lower() == "status":
                _enrich_status(cfg, chat_id)
            else:
                busy = _op_busy()
                if busy:
                    _tg_api(cfg, "sendMessage", {
                        "chat_id": chat_id, "parse_mode": "HTML",
                        "text": f"⏳ Hay una operación en curso ({busy}, {_op_minutes(busy)}m) — "
                                f"espera que termine antes de lanzar el batch IA"})
                else:
                    # ack inmediato — el batch corre en background y reporta por sí solo
                    threading.Thread(target=_ia_batch_async, args=(cfg, chat_id), daemon=True).start()
        elif cmd == "/enrich_all":
            busy = _op_busy()
            if busy:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"⏳ Hay una operación en curso ({busy}, {_op_minutes(busy)}m) — "
                            f"espera que termine antes de lanzar el batch IA"})
            else:
                # /enrich_all: TODAS las activas sin IA con descripción (incluye completas)
                threading.Thread(target=_ia_batch_async,
                                 args=(cfg, chat_id), kwargs={"all_pending": True},
                                 daemon=True).start()
        elif cmd == "/report":
            if arg.lower() == "status":
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": _report_status_text()})
            elif arg.lower() == "list":
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": _report_list_text(cfg)})
            elif not cfg.report.enabled:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "📊 /report deshabilitado (REPORT_ENABLED=false)"})
            else:
                busy = _op_busy()
                if busy:
                    _tg_api(cfg, "sendMessage", {
                        "chat_id": chat_id, "parse_mode": "HTML",
                        "text": f"⏳ Hay una operación en curso ({busy}, {_op_minutes(busy)}m) — "
                                f"espera que termine antes de lanzar el reporte"})
                    return
                else:
                    threading.Thread(target=_report_async, args=(cfg, chat_id), daemon=True).start()
        elif cmd == "/config":
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": _config_text(cfg)})
        elif cmd == "/preview":
            _preview_offer(cfg, chat_id, arg)
        elif cmd == "/stats":
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": _stats_text(cfg)})
        elif cmd == "/stop":
            busy = _op_busy()
            if not busy:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "ℹ️ No hay operación en curso — nada que detener"})
            elif _STOP_EVENT.is_set():
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"⏹ Stop ya solicitado — {busy} terminando en su próximo punto seguro…"})
            else:
                _STOP_EVENT.set()
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"⏹ <b>Deteniendo {busy}</b> — corte limpio en el próximo "
                                                     f"punto seguro (lo procesado queda guardado). "
                                                     f"Te confirmo al terminar."})
                def _ack_done():
                    # auto-limpieza: cuando la operación suelta su estado, resetea el flag y avisa
                    import time as _ts
                    while _op_busy():
                        _ts.sleep(2)
                    _STOP_EVENT.clear()
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": "✅ Operación detenida — bot libre"})
                threading.Thread(target=_ack_done, daemon=True).start()
        elif cmd.startswith("/channel"):
            sub = (cmd[len("/channel"):].replace("_", "-").strip("-")) or "status"
            confirm = sub.endswith("-confirm")
            if confirm:
                sub = sub[:-len("-confirm")]
            action = sub or "status"
            from .channel import (publish_channel, publish_daily_digest,
                                  publish_weekly_remote, publish_weekly_rol,
                                  publish_weekly_salary, publish_weekly_techs,
                                  publish_trends, channel_status)
            api = _tg_api_for_channel(cfg)
            if action == "status":
                conn = database.connect(cfg)
                try:
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": channel_status(conn, cfg)})
                finally:
                    conn.close()
            elif action in ("publish", "publish-ia", "daily", "weekly-remote", "weekly-rol",
                            "weekly-techs", "weekly-salary", "weekly", "trends", "all", "dry"):
                def _run():
                    # conn se abre EN el thread (SQLite no permite compartir entre threads)
                    import time as _t
                    conn = database.connect(cfg)
                    try:
                        if action in ("publish", "publish-ia", "dry", "all"):
                            stats = publish_channel(cfg, conn, api, dry_run=(action == "dry"),
                                                    require_ia=(action == "publish-ia"),
                                                    drain=True)   # manual: drena toda la cola
                            if action == "dry":
                                for p in stats.get("dry_run_preview", []):
                                    payload = {
                                        "chat_id": chat_id, "text": "👁 preview:\n" + p["text"],
                                        "parse_mode": "HTML", "disable_web_page_preview": True}
                                    if p.get("kb"):
                                        payload["reply_markup"] = p["kb"]
                                    _tg_api(cfg, "sendMessage", payload)
                                    _t.sleep(0.5)
                            else:
                                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                             "text": f"📢 canal publish: {stats}"})
                        if action in ("daily", "all"):
                            ok = publish_daily_digest(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "📊 daily digest enviado" if ok else
                                                                 "📊 daily digest: sin candidatas o ya enviado hoy"})
                            _t.sleep(1)
                        if action in ("weekly-remote", "weekly", "all"):
                            ok = publish_weekly_remote(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🌍 weekly-remote enviado" if ok else
                                                                 "🌍 weekly-remote: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-rol", "weekly", "all"):
                            ok = publish_weekly_rol(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🏆 weekly-rol enviado" if ok else
                                                                 "🏆 weekly-rol: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-techs", "weekly", "all"):
                            ok = publish_weekly_techs(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🧰 weekly-techs enviado" if ok else
                                                                 "🧰 weekly-techs: sin datos o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-salary", "weekly", "all"):
                            ok = publish_weekly_salary(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "💰 weekly-salary enviado" if ok else
                                                                 "💰 weekly-salary: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("trends", "all"):
                            ok = publish_trends(cfg, conn, api, force=True)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "📈 trends enviado" if ok else
                                                                 "📈 trends: sin datos o ya enviado este mes"})
                    except Exception as exc:
                        log.error("channel-%s falló: %s", action, str(exc)[:150])
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"⚠️ channel-{action} falló: "
                                                             f"<code>{esc(str(exc)[:120])}</code>"})
                    finally:
                        conn.close()
                # ack inmediato — la acción corre en background y reporta al terminar
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"📢 <code>channel-{action}</code> "
                                                     f"ejecutando en background — te reporto al terminar"})
                threading.Thread(target=_run, daemon=True).start()
            elif action == "wipe":
                # ack inmediato + thread (borrar N mensajes toma N×0.3s + latencia)
                if not confirm:
                    conn2 = database.connect(cfg)
                    try:
                        total = conn2.execute(
                            "SELECT COUNT(*) FROM channel_posts WHERE message_id IS NOT NULL").fetchone()[0]
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"🧨 Wipe borraría {total} mensajes "
                                                             f"del canal y resetearía las marcas. "
                                                             f"Confirma con: <code>/channel-wipe-confirm</code>"})
                    finally:
                        conn2.close()
                else:
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": "🧨 <code>channel-wipe</code> ejecutando en "
                                                         "background — borrando todos los mensajes del "
                                                         "canal, te reporto al terminar"})
                    def _wipe():
                        conn2 = database.connect(cfg)
                        try:
                            from .channel import channel_wipe
                            stats = channel_wipe(cfg, conn2, api)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": f"🧨 canal limpiado: {stats['deleted']}/"
                                                                 f"{stats['total']} mensajes borrados, "
                                                                 f"{stats['skipped']} ya no existían. "
                                                                 f"Marcas reseteadas — /channel_publish "
                                                                 f"para repoblar"})
                            log.info("canal wipe: %s", stats)
                        except Exception as exc:
                            log.error("channel-wipe falló: %s", str(exc)[:150])
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": f"⚠️ channel-wipe falló: "
                                                                 f"<code>{esc(str(exc)[:120])}</code>"})
                        finally:
                            conn2.close()
                    threading.Thread(target=_wipe, daemon=True).start()
            elif action == "reset":
                conn = database.connect(cfg)
                try:
                    if confirm:
                        n = conn.execute("""UPDATE ofertas SET notified_channel_at=''
                            WHERE active=1 AND notified_channel_at != ''""").rowcount
                        conn.execute("DELETE FROM channel_posts WHERE kind='offer'")
                        conn.commit()
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"🧹 {n} marcas limpiadas — "
                                                             f"{n} ofertas vuelven a ser candidatas"})
                        log.info("canal reset: %d marcas limpiadas", n)
                    else:
                        cand = conn.execute(_count_gate_sql(cfg)).fetchone()[0]
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"⚠️ Esto limpiará las marcas de publicación "
                                                             f"de todas las activas ({cand} volverían a "
                                                             f"ser candidatas).\nConfirma con: "
                                                             f"<code>/channel-reset-confirm</code>"})
                finally:
                    conn.close()
            else:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "❓ /channel-&lt;action&gt;\n"
                                                     "<code>/channel · /channel-publish · "
                                                     "/channel-daily · /channel-weekly-remote · "
                                                     "/channel-weekly-salary · /channel-weekly · "
                                                     "/channel-trends · /channel-all · "
                                                     "/channel-dry · /channel-reset (-confirm) · /channel-wipe (-confirm)</code>"})
        elif cmd.startswith("/report_"):
            # Versión DM de los reportes del canal (para testear sin ensuciar el canal):
            # mismos digests, destino = este chat. Bucket separado '-dm' (no bloquea
            # el envío real al canal del mismo día).
            sub = cmd[len("/report_"):].replace("_", "-").strip("-")
            action = sub or "help"
            from .channel import (publish_daily_digest, publish_weekly_remote,
                                  publish_weekly_rol, publish_weekly_salary,
                                  publish_weekly_techs, publish_trends)
            api = _tg_api_for_channel(cfg)
            if action == "help":
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "❓ /report-&lt;accion&gt; (DM — prueba de reportes)\n"
                                                     "<code>/report-daily · /report-weekly-remote · "
                                                     "/report-weekly-rol · /report-weekly-salary · "
                                                     "/report-weekly-techs · /report-trends · /report-all</code>"})
            else:
                def _run():
                    import time as _t
                    conn = database.connect(cfg)
                    try:
                        if action in ("daily", "all"):
                            # mensaje honesto: distinguir 0 filas de gate vs
                            # N filas filtradas por no-dev (el "ya enviado hoy"
                            # NUNCA aplica en DM — con chat_id envía siempre)
                            from .channel import _GATE_SQL
                            from .domain.roles import is_dev
                            try:
                                rows = [dict(r) for r in conn.execute(
                                    _GATE_SQL, {"min_score": cfg.channel.digest_min_score,
                                                "max_age": cfg.channel.max_age_days}).fetchall()]
                                n_total = len(rows)
                                n_dev = sum(1 for r in rows if is_dev(
                                    r.get("rol_categoria"), r.get("title") or "", cfg,
                                    r.get("description") or ""))
                            except Exception:
                                n_total = n_dev = -1
                            ok = publish_daily_digest(cfg, conn, api, chat_id=chat_id)
                            if ok:
                                msg = "📊 report-daily enviado"
                            elif n_total == 0:
                                msg = "📊 report-daily: 0 ofertas en el gate (score ≥ digest_min_score y ≤14d)"
                            elif n_dev == 0:
                                msg = (f"📊 report-daily: {n_total} ofertas en gate, "
                                       "ninguna dev (todas filtradas por rol no-dev)")
                            else:
                                msg = f"📊 report-daily: {n_dev} dev de {n_total} gate, envío falló"
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": msg})
                            _t.sleep(1)
                        if action in ("weekly-remote", "all"):
                            ok = publish_weekly_remote(cfg, conn, api, chat_id=chat_id)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🌍 report-weekly-remote enviado" if ok else
                                                                 "🌍 report-weekly-remote: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-rol", "all"):
                            ok = publish_weekly_rol(cfg, conn, api, chat_id=chat_id)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🏆 report-weekly-rol enviado" if ok else
                                                                 "🏆 report-weekly-rol: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-techs", "all"):
                            ok = publish_weekly_techs(cfg, conn, api, chat_id=chat_id)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🧰 report-weekly-techs enviado" if ok else
                                                                 "🧰 report-weekly-techs: sin datos o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-salary", "all"):
                            ok = publish_weekly_salary(cfg, conn, api, chat_id=chat_id)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "💰 report-weekly-salary enviado" if ok else
                                                                 "💰 report-weekly-salary: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("trends", "all"):
                            ok = publish_trends(cfg, conn, api, chat_id=chat_id)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "📈 report-trends enviado" if ok else
                                                                 "📈 report-trends: sin datos o ya enviado este mes"})
                    except Exception as exc:
                        log.error("report-%s falló: %s", action, str(exc)[:150])
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"⚠️ report-{action} falló: "
                                                             f"<code>{esc(str(exc)[:120])}</code>"})
                    finally:
                        conn.close()
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"📢 <code>report-{action}</code> "
                                                     f"ejecutando en background — te reporto al terminar"})
                threading.Thread(target=_run, daemon=True).start()
        elif cmd.startswith("/db"):
            action = (cmd[len("/db"):].replace("_", "-").strip("-")) or "stats"
            confirm = action.endswith("-confirm")
            if confirm:
                action = action[:-len("-confirm")]
            if action == "" or action == "stats":
                conn = database.connect(cfg)
                try:
                    act = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1").fetchone()[0]
                    inact = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=0").fetchone()[0]
                    old = conn.execute("""SELECT COUNT(*) FROM ofertas WHERE active=0
                        AND last_seen < datetime('now', '-30 days')""").fetchone()[0]
                    nondev = conn.execute("""SELECT COUNT(*) FROM ofertas WHERE active=1
                        AND rol_categoria IN ('Ingeniería no-software','No-tech','Profesor/Formación',
                        'Analista/Empresa')""").fetchone()[0]
                    conia = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model!=''").fetchone()[0]
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                        "text": f"🗄 <b>DB</b>\n✅ activas: {act}\n🪦 inactivas: {inact} "
                                f"(>30d purgable: {old})\n🚫 no-dev activas: {nondev}\n"
                                f"🧠 con IA: {conia}\n"
                                f"purge disponible: <code>old</code> ({old}) · <code>nondev</code> ({nondev}) · "
                                f"<code>iaclear</code> (re-encola {conia}) · <code>all</code> (TODO)"})
                finally:
                    conn.close()
            elif action in ("old", "nondev", "all", "iaclear"):
                if confirm:
                    def _purge():
                        conn = database.connect(cfg)
                        try:
                            import pathlib
                            bak = pathlib.Path(cfg.data_dir) / "backups"
                            bak.mkdir(exist_ok=True)
                            database.backup_db(conn,
                                bak / f"ofertas-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.sqlite")
                            if action == "old":
                                n = conn.execute("""DELETE FROM ofertas WHERE active=0
                                    AND last_seen < datetime('now', '-30 days')""").rowcount
                            elif action == "nondev":
                                n = conn.execute("""DELETE FROM ofertas WHERE active=1
                                    AND rol_categoria IN ('Ingeniería no-software','No-tech','Profesor/Formación',
                                    'Analista/Empresa')""").rowcount
                            elif action == "iaclear":
                                # FIX B: limpia SOLO la marca de IA → las ofertas vuelven a la cola
                                # y /enrich regenera los comentarios con contexto completo.
                                # NO toca modality/salary (valores buenos ya extraídos).
                                n = conn.execute("""UPDATE ofertas SET ia_model='', ia_fields=''
                                    WHERE active=1""").rowcount
                            else:  # all
                                n = conn.execute("DELETE FROM ofertas").rowcount
                                conn.execute("DELETE FROM channel_posts")
                                conn.execute("""DELETE FROM score_versions""")
                            conn.commit()
                            extra = " (también channel_posts y score_versions)" if action == "all" else ""
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": f"🗑 {n} ofertas eliminadas ({action}){extra} — "
                                                                 f"backup previo en data/backups/"})
                            log.info("db purge %s: %d filas", action, n)
                        except Exception as exc:
                            log.error("db purge %s falló: %s", action, str(exc)[:150])
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": f"⚠️ purge {action} falló: "
                                                                 f"<code>{esc(str(exc)[:120])}</code>"})
                        finally:
                            conn.close()
                    if action == "all":
                        # all borra TODO — ack + thread (la DB es grande y el backup toma tiempo)
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": "🗑 <code>db-all</code> ejecutando en "
                                                             "background — borrando TODAS las ofertas, "
                                                             "te reporto al terminar"})
                        threading.Thread(target=_purge, daemon=True).start()
                    elif action == "iaclear":
                        # iaclear es UPDATE masivo sobre las activas — thread con ACK (no bloquea el loop)
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": "🧠 <code>db-iaclear</code> ejecutando en "
                                                             "background — re-encolando todas las activas "
                                                             "para IA (los comentarios se regeneran con "
                                                             "contexto completo al correr /enrich)"})
                        threading.Thread(target=_purge, daemon=True).start()
                    else:
                        _purge()
                else:
                    warning = ("DELETE físico" if action != "iaclear"
                               else "re-encola TODAS las activas para IA (los comentarios actuales se regeneran)")
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": f"⚠️ /db-{action}: {warning}. "
                                                         f"Confirma con: <code>/db-{action}-confirm</code>"})
            else:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "❓ /db-&lt;action&gt; · <code>stats · old · nondev · iaclear</code>"})
        elif cmd == "/latest":
            try:
                n = max(1, min(25, int(arg))) if arg else 10
            except ValueError:
                n = 10
            offers = _latest_offers(cfg, n)
            rendered = render_page(offers, 0, cfg.telegram.digest_page_size, cfg,
                                   label=f"🆕 <b>Últimas registradas</b>", cb_prefix="latest")
            kb = [[{k: v for k, v in b.items() if k != "style"} for b in row]
                  for row in rendered["keyboard"]]
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "text": rendered["text"], "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": _kb_json(kb)})
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
                "reply_markup": _kb_json(kb)})
        elif cmd == "/score":
            filtro, modo, solo_sueldo = _parse_preview_arg(arg)
            if modo != "score" or filtro is None:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": "Uso: <code>/score N</code> — ej: <code>/score 60</code> · "
                            "<code>/score 60 s</code> solo con sueldo"})
                return
            th = max(0, min(100, int(filtro)))
            offers = _score_offers(cfg, th, solo_sueldo)
            if not offers:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"Nada con score ≥{th} en el pool activo."
                            + (" con sueldo declarado." if solo_sueldo else "")})
                return
            prefix = f"sc{th}" + ("s" if solo_sueldo else "")
            label = f"🎯 <b>Ofertas ≥{th}</b>"
            if solo_sueldo:
                label += " 💰 con sueldo"
            rendered = render_page(offers, 0, cfg.telegram.digest_page_size, cfg,
                                   label=label, cb_prefix=prefix)
            kb = [[{k: v for k, v in b.items() if k != "style"} for b in row]
                  for row in rendered["keyboard"]]
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "text": rendered["text"], "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": _kb_json(kb)})
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


_IA_STATE = IAState()


def _ia_batch_async(cfg: Config, chat_id: int | None, scheduled: bool = False,
                    state: dict | None = None, all_pending: bool = False):
    """Batch IA con ACK inicial, progreso por oferta y resumen final.
    Nunca tumba el daemon. scheduled=True → consume el marcador diario SOLO si parte de verdad.
    all_pending=True → /enrich_all: TODAS las activas sin IA con descripción
    (incluye las completas, no solo la cola C9)."""
    if _IA_STATE["running"]:
        if chat_id:
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": "🧠 Ya hay un batch IA corriendo — "
                                                 "<code>/enrich status</code> para ver el avance"})
        return
    # FIX carrera: entre el handler y este thread puede haber partido un barrido
    # (mismo tick del loop). Chocar con el lock del fetch = muerte a los 60s.
    if _SEARCH_STATE["running"]:
        if chat_id:
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": "🧠 Batch IA pospuesto: un barrido acaba de partir — "
                                                 "reintenta con <code>/enrich</code> cuando termine"})
        return
    # FIX runs agendados: si un enrich manual está en curso, el agendado NO parte
    # (y el marcador diario no se consume — se reintenta el próximo tick libre)
    if scheduled:
        state = state or {}
        state.setdefault("ia_log", {})["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _IA_STATE.update(running=True, done=0, total=0, current="", t0=time.time())   # lock temprano: cierra la ventana de carrera
    try:
        conn = database.connect(cfg)
        try:
            from .enrich import run_ia_batch, profile_description, ia_queue_count, enrich_pending
            cola = ia_queue_count(conn)
            sin_ficha = conn.execute(
                "SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model='' AND "
                "NOT (length(description)>200 OR description_source!='')").fetchone()[0]
        finally:
            conn.close()
        if chat_id:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": f"🧠 <b>Batch IA iniciado</b> — {cola} en cola + {sin_ficha} sin ficha "
                        f"(bajaré fichas primero) · avance con <code>/enrich status</code>"})
        _IA_STATE.update(running=True, done=0, total=cola + sin_ficha, current="", t0=time.time())
        t0 = time.time()
        conn = database.connect(cfg)
        try:
            # FIX: bajar fichas de las sin descripción ANTES de la IA (rescate global).
            # enrich_pending global: fetch paralelo + árbitro + IA por lote sobre las
            # recargadas — un solo /enrich rescata las huérfanas (mismo patrón cmd_enrich).
            fichas = 0
            try:
                fichas = enrich_pending(conn, cfg, stop_event=_STOP_EVENT)
                if fichas:
                    log.info("enrich_pending global: %d fichas bajadas/procesadas", fichas)
            except Exception as e:
                log.warning("enrich_pending global falló (continúa con la cola): %s", e)
            done = run_ia_batch(conn, cfg, profile_description(cfg),
                                progress=(_mk_progress_cb(cfg, chat_id) if chat_id else None),
                                stop_event=_STOP_EVENT, all_pending=all_pending)
        finally:
            conn.close()
        dur = int(time.time() - t0)
        _IA_STATE.reset()   # reset completo (incluye t0) — evita "0/0 lote 8" fantasma
        # recalcular cola real tras el batch (la variable `cola` era previa al inicio)
        try:
            conn2 = database.connect(cfg)
            try:
                cola_fin = ia_queue_count(conn2)
            finally:
                conn2.close()
        except Exception:
            cola_fin = None
        cola_txt = f" · quedan {cola_fin} en cola" if cola_fin is not None else ""
        # re-score con los datos nuevos de IA (salary/modality/seniority cambian el score)
        rescored = 0
        try:
            conn3 = database.connect(cfg)
            try:
                from .scoring import compute_score, compute_market_score, compute_market_score
                version_id = database.current_version(conn3) or (
                    "env-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
                database.register_criteria_version(conn3, version_id, cfg)
                rescored = database.rescore_all(conn3, compute_score, version_id, cfg,
                                            market_score_fn=compute_market_score)
            finally:
                conn3.close()
        except Exception as e:
            log.warning("rescore post-IA falló (batch OK): %s", e)
        if chat_id:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": f"🧠 <b>Batch IA terminado</b> — {done} ofertas enriquecidas{cola_txt} · rescore: {rescored} · {dur // 60}m{dur % 60:02d}s"})
        log.info("batch IA OK: %d ofertas, rescore %d (%ds)", done, rescored, dur)
    except Exception as exc:
        _IA_STATE.reset()   # reset completo también en fallo
        log.error("batch IA falló: %s", exc)
        if chat_id:
            try:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": f"⚠️ Batch IA falló: <code>{esc(str(exc)[:200])}</code>"})
            except Exception:
                pass


def _enrich_status(cfg: Config, chat_id: int) -> None:
    """Reporta el avance del batch IA en curso (o la cola si está libre)."""
    if _IA_STATE["running"]:
        mins = int(time.time() - _IA_STATE["t0"]) // 60
        txt = (f"🧠 Batch IA en curso — <code>{_IA_STATE['done']}/{_IA_STATE['total']}</code> "
               f"({mins}m)\n   {esc(_IA_STATE['current'][:50])}")
    else:
        conn = database.connect(cfg)
        try:
            from .enrich import ia_queue_count
            n = ia_queue_count(conn)
        finally:
            conn.close()
        txt = (f"🧠 Sin batch en curso. En cola: <code>{n}</code> ofertas\n"
               f"(batch nocturno 03:00 UTC · o lánzalo con <code>/enrich</code>)")
    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML", "text": txt})


# ---------------------------------------------------------------- report /market

def _report_status_text() -> str:
    if _REPORT_STATE["running"]:
        mins = int(time.time() - _REPORT_STATE["t0"]) // 60
        return (f"📊 Análisis de mercado en curso — fase {_REPORT_STATE['phase']}/4 "
                f"({mins}m)\n   {esc(_REPORT_STATE['phase_msg'][:60])}")
    pdf = _REPORT_STATE.get("pdf") or ""
    if pdf:
        p = Path(pdf)
        kb = p.stat().st_size // 1024 if p.exists() else 0
        return (f"📊 Último reporte: <code>{esc(p.name)}</code> ({kb} KB)\n"
                f"en <code>{esc(str(p.parent))}</code>")
    return "📊 Sin reportes generados todavía — lanza uno con <code>/report</code>"


def _report_list_text(cfg: Config) -> str:
    d = cfg.report.out_dir
    if not d.exists():
        return "📊 Sin reportes generados todavía — lanza uno con <code>/report</code>"
    pdfs = sorted(d.glob("mercado_*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    if not pdfs:
        return "📊 Sin reportes generados todavía — lanza uno con <code>/report</code>"
    lines = ["📊 <b>Reportes de mercado</b>", ""]
    for p in pdfs:
        kb = p.stat().st_size // 1024
        lines.append(f"• <code>{esc(p.name)}</code> ({kb} KB)")
    return "\n".join(lines)


def _report_async(cfg: Config, chat_id: int):
    """Pipeline de análisis de mercado en background con progreso por fase."""
    from .market import highlights, run_market_pipeline
    t0 = time.time()
    _REPORT_STATE.update(running=True, phase=0, phase_msg="iniciando", t0=t0, pdf="")
    last_msg = {"t": 0.0}

    def on_phase(n: int, msg: str):
        _REPORT_STATE.update(phase=n, phase_msg=msg)
        # throttle: máx 1 mensaje de fase cada 30s (el primero siempre pasa)
        now = time.time()
        if now - last_msg["t"] >= 30:
            last_msg["t"] = now
            try:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"📊 <b>Reporte</b> — fase {n}/4 · {esc(msg[:60])}"})
            except Exception:
                pass

    try:
        if chat_id:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": "📊 <b>Análisis de mercado iniciado</b> — ~1-2 min\n"
                        "fases: agregación → gráficos → narrativa IA → PDF"})
        pdf_path, narr, ia_ok = run_market_pipeline(cfg, on_phase=on_phase)
        _REPORT_STATE["pdf"] = str(pdf_path)
        dur = int(time.time() - t0)
        if chat_id:
            sent = _tg_send_document(cfg, chat_id, str(pdf_path))
            resumen = " · ".join(narr.get("tldr", [])[:2])
            txt = (f"📄 <b>Reporte de mercado listo</b> · {dur // 60}m{dur % 60:02d}s\n"
                   f"{'✅ entregado al chat' if sent else '⚠️ no se pudo enviar — archivo local abajo'}\n"
                   f"<i>{esc(resumen[:180])}</i>\n"
                   f"<code>{esc(str(pdf_path))}</code>")
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML", "text": txt})
        log.info("report OK: %s (ia_ok=%s, %ds)", pdf_path, ia_ok, dur)
    except Exception as exc:
        log.error("report falló: %s", exc)
        if chat_id:
            try:
                _tg_api(cfg, "sendMessage", {
                    "chat_id": chat_id, "parse_mode": "HTML",
                    "text": f"⚠️ Reporte falló: <code>{esc(str(exc)[:200])}</code>"})
            except Exception:
                pass
    finally:
        _REPORT_STATE["running"] = False


def _mk_progress_cb(cfg: Config, chat_id: int):
    """Callback de progreso: actualiza 1 mensaje de estado (throttle 1 msg/15s — spec-enrich-lotes §6)."""
    st = {"msg_id": None, "t": 0.0}

    def cb(done: int, total: int, title: str) -> None:
        _IA_STATE.update(done=done, total=total, current=title)
        now = time.time()
        txt = (f"🧠 Batch IA — <code>{done}/{total}</code>\n"
               f"   {esc(title[:50])}")
        try:
            if st["msg_id"] or now - st["t"] >= 15:
                st["msg_id"] = _tg_edit_or_send(cfg, chat_id, st["msg_id"],
                                                {"text": txt, "parse_mode": "HTML"})
            st["t"] = now
        except Exception:
            pass
    return cb


def _ia_hours_due(cfg: Config, last_ia: dict) -> bool:
    """True si la hora UTC actual está en IA_RUN_HOURS_UTC y no corrió en esa hora."""
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc)
    key = now.strftime("%Y-%m-%d")
    return now.hour in cfg.ia.run_hours_utc and last_ia.get("day") != key


def _ia_sweep_maybe(cfg: Config, state: dict) -> None:
    """Dispara el batch IA si toca la hora agendada (3:00 UTC por defecto).
    Los runs agendados NO ejecutan ni consumen su marcador si hay enrich/search en curso."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return
    if _SEARCH_STATE["running"]:   # C1: no competir con la IA paralela del scan
        log.info("batch IA nocturno pospuesto: hay un barrido en curso")
        return
    if _IA_STATE["running"]:       # FIX: enrich manual en curso → no consumir marcador diario
        log.info("batch IA nocturno pospuesto: hay un enrich en curso")
        return
    if not _ia_hours_due(cfg, state.setdefault("ia_log", {})):
        return
    # el marcador diario lo consume el thread SOLO si pasa todas las guardas y parte de verdad
    log.info("batch IA agendado (%s) — disparando", cfg.ia.run_hours_utc)
    threading.Thread(target=_ia_batch_async, args=(cfg, None, True, state), daemon=True).start()


def _count_gate_sql(cfg: Config) -> str:
    """Conteo de candidatas del gate (para preview de /channel reset)."""
    return (f"SELECT COUNT(*) FROM ofertas WHERE active=1 AND market_score >= {int(cfg.channel.min_score)} "
            f"AND date_canonical >= date('now', '-{int(cfg.channel.max_age_days)} days')")


def _tg_api_for_channel(cfg: Config):
    def call(method: str, payload: dict) -> dict:
        try:
            if method == "sendPhoto" and payload.get("path"):
                # foto nativa: multipart con archivo local (gráficos del canal)
                return _tg_send_photo(cfg, payload)
            return _tg_api(cfg, method, payload)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    return call


def _tg_send_photo(cfg: Config, payload: dict) -> dict:
    """sendPhoto multipart/form-data (PNG nativos para el canal).

    payload: {chat_id, path, caption?, parse_mode?} — retorna el JSON de Telegram.
    """
    import mimetypes
    import uuid
    from pathlib import Path as _P

    chat_id = payload.get("chat_id")
    if chat_id is not None and not _chat_allowed(cfg, chat_id):
        raise PermissionError(f"chat {chat_id} no está en TELEGRAM_ALLOWED_CHATS")
    file = _P(payload["path"])
    if not file.exists():
        return {"ok": False, "error": "file not found"}
    mime = mimetypes.guess_type(file.name)[0] or "image/png"
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []

    def field(name: str, value: str):
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())

    field("chat_id", str(chat_id))
    if payload.get("caption"):
        field("caption", str(payload["caption"])[:1024])
        field("parse_mode", "HTML")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                 f"filename=\"{file.name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    parts.append(file.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/sendPhoto",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        log.error("sendPhoto falló: %s", e)
        return {"ok": False, "error": str(e)[:200]}


def _digests_maybe(cfg: Config, state: dict) -> None:
    """Digests del canal (B diario / C+D semanal / E mensual) — patrón _ia_sweep_maybe."""
    if not cfg.channel.enabled or not cfg.channel.chat_id:
        return
    if _SEARCH_STATE["running"]:   # C2: no leer pool a medio procesar
        return
    now = datetime.now(timezone.utc)
    keys = state.setdefault("digest_log", {})
    try:
        conn = database.connect(cfg)
    except Exception as e:
        log.warning("digests: conn falló: %s", e)
        return
    try:
        from .channel import (publish_daily_digest, publish_weekly_digests,
                              publish_trends)
        # diario
        if cfg.channel.digest_daily and now.hour >= cfg.channel.digest_daily_hour_utc:
            k = f"daily:{now.strftime('%Y-%m-%d')}"
            if keys.get(k) != 1:
                publish_daily_digest(cfg, conn, _tg_api_for_channel(cfg))
                keys[k] = 1   # OPS-9: marca SOLO si el digest no lanzó excepción
        # semanal
        if cfg.channel.digest_weekly and now.weekday() == cfg.channel.digest_weekly_day_utc \
                and now.hour >= cfg.channel.digest_weekly_hour_utc:
            k = f"weekly:{now.strftime('%G-W%V')}"
            if keys.get(k) != 1:
                publish_weekly_digests(cfg, conn, _tg_api_for_channel(cfg))
                keys[k] = 1   # OPS-9: marca solo si no lanzó excepción
        # tendencias (día 1 de mes)
        if cfg.channel.digest_tendencias and now.day == 1 and now.hour >= 13:
            k = f"trends:{now.strftime('%Y-%m')}"
            if keys.get(k) != 1:
                publish_trends(cfg, conn, _tg_api_for_channel(cfg))
                keys[k] = 1   # OPS-9: marca solo si no lanzó excepción
    except Exception as e:
        log.warning("digests falló (no tumba daemon): %s", e)
    finally:
        conn.close()


def _register_commands(cfg: Config) -> None:
    """Registra los comandos en Telegram (menú "/" del cliente)."""
    commands = [
        {"command": "search", "description": "Gatilla una búsqueda ahora"},
        {"command": "enrich", "description": "Corre el batch IA ahora (rellena datos faltantes)"},
        {"command": "enrich_all", "description": "TODAS las activas sin IA con descripción"},
        {"command": "stop", "description": "Detiene la operación en curso (corte limpio)"},
        {"command": "report", "description": "Análisis de mercado completo con PDF"},
        {"command": "latest", "description": "Últimas ofertas registradas"},
        {"command": "stats", "description": "Cobertura del pool"},
        {"command": "config", "description": "Configuración actual (tokens enmascarados)"},
        {"command": "preview", "description": "Oferta aleatoria como se vería en el canal"},
        {"command": "channel", "description": "Estado del canal"},
        {"command": "channel_publish", "description": "Publicar candidatas al canal ahora"},
        {"command": "channel_publish_ia", "description": "Publicar solo ofertas revisadas por IA"},
        {"command": "channel_daily", "description": "Daily digest ahora"},
        {"command": "channel_weekly", "description": "Digests semanales ahora (remote+rol+salary)"},
        {"command": "channel_weekly_remote", "description": "Solo digest weekly-remote"},
        {"command": "channel_weekly_rol", "description": "Solo mejor de la semana por rol"},
        {"command": "channel_weekly_techs", "description": "Solo tecnologías del mercado"},
        {"command": "channel_weekly_salary", "description": "Solo ranking salarial"},
        {"command": "channel_trends", "description": "Post mensual de tendencias"},
        {"command": "channel_all", "description": "Publish + todos los digests"},
        {"command": "channel_dry", "description": "Preview sin publicar"},
        {"command": "report_daily", "description": "Prueba DM: top del día por categoría"},
        {"command": "report_weekly_rol", "description": "Prueba DM: mejor de la semana por rol"},
        {"command": "report_weekly_techs", "description": "Prueba DM: tecnologías del mercado"},
        {"command": "report_weekly_salary", "description": "Prueba DM: ranking salarial con contexto"},
        {"command": "report_all", "description": "Prueba DM: todos los reportes"},
        {"command": "channel_reset_confirm", "description": "Limpiar marcas de publicados"},
        {"command": "channel_wipe_confirm", "description": "Borrar TODOS los mensajes del canal"},
        {"command": "db", "description": "DB stats"},
        {"command": "db_old_confirm", "description": "Purge inactivas >30d (confirm)"},
        {"command": "db_nondev_confirm", "description": "Purge no-dev (confirm)"},
        {"command": "db_all_confirm", "description": "Borrar TODO el pool (backup previo)"},
        {"command": "db_iaclear_confirm", "description": "Limpiar marca IA (re-encola todo)"},
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

def _do_sweep(cfg: Config, on_phase=None) -> tuple[list[dict], dict]:
    """Barrido completo + pool refrescado. Retorna (ofertas ≥min_score, stats del scan_log)."""
    with _sweep_lock:
        cmd_run(cfg, notify=False, on_phase=on_phase, stop_event=_STOP_EVENT)   # barrido SIN mensaje push
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
    """Barrido + refresh del pool + ancla (cron interno del daemon). Nunca mata el thread.
    Se SALTA si hay operación manual pesada en curso (reintenta la próxima hora agendada)."""
    busy = _op_busy()
    if busy:
        log.info("barrido agendado saltado: %s en curso", busy)
        return
    _SEARCH_STATE.update(running=True, t0=time.time())   # OPS-2: agendado también levanta la guarda C1/C2
    try:
        offers, _stats = _do_sweep(cfg)
        state["offers"] = offers
        _refresh_anchor(cfg, state)
    except Exception as exc:
        log.error("sweep del cron falló: %s", exc)
    finally:
        _SEARCH_STATE.update(running=False, t0=0.0)


def _refresh_anchor(cfg: Config, state: dict):
    rendered = render_page(state["offers"], 0, cfg.telegram.digest_page_size, cfg)
    kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in rendered["keyboard"]]
    body = {
        "chat_id": cfg.telegram.chat_id,
        "text": rendered["text"],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": _kb_json(kb),
    }
    try:
        if state["anchor_id"]:
            try:
                _tg_api(cfg, "editMessageText", {**body, "chat_id": cfg.telegram.chat_id,
                                                 "message_id": state["anchor_id"]})
                log.info("ancla actualizada (%d ofertas)", len(state["offers"]))
            except Exception as exc:
                # ancla borrada por el usuario → recrear (conserva el estado de página)
                if "not modified" in str(exc).lower():
                    log.info("ancla sin cambios (%d ofertas)", len(state["offers"]))
                else:
                    resp = _tg_api(cfg, "sendMessage", body)
                    state["anchor_id"] = resp.get("result", {}).get("message_id")
                    log.info("ancla recreada: message_id=%s (%d ofertas) — la anterior fue borrada",
                             state["anchor_id"], len(state["offers"]))
        else:
            resp = _tg_api(cfg, "sendMessage", body)
            state["anchor_id"] = resp.get("result", {}).get("message_id")
            log.info("ancla creada: message_id=%s (%d ofertas)", state["anchor_id"], len(state["offers"]))
    except Exception as exc:
        log.error("anchor refresh failed: %s", str(exc)[:200])


def _load_pool(cfg: Config) -> list[dict]:
    """Carga el pool activo ≥min_score desde la DB (sin barrido de fuentes)."""
    conn = database.connect(cfg)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ofertas WHERE active=1 AND score >= ? ORDER BY score DESC",
            (cfg.alerts.min_score,)).fetchall()]
    finally:
        conn.close()


def _load_state_flag(conn) -> str:
    """Lee el marcador de último barrido agendado (sobrevive restarts)."""
    try:
        row = conn.execute("SELECT value FROM state_flags WHERE key='last_sweep_key'").fetchone()
        return row[0] if row else ""
    except sqlite3.OperationalError:
        return ""


def _save_state_flag(conn, key: str, value: str) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS state_flags (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    conn.execute("""INSERT INTO state_flags (key, value) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, value))
    conn.commit()


def _sweep_hours_due(cfg: Config, state: dict) -> bool:
    """True si la hora UTC actual está agendada y no se barrió en esa hora exacta.
    El marcador es PERSISTENTE (FIX: un restart jamás re-dispara un barrido ya hecho)."""
    now = datetime.now(timezone.utc)
    key = now.strftime("%Y-%m-%d-%H")
    return now.hour in cfg.daemon.sweep_hours_utc and state.get("last_sweep_key") != key


def run_daemon(cfg: Config) -> None:
    """Proceso residente: sin barridos al arrancar (carga pool desde DB) +
    barridos por horas agendadas (DAEMON_SWEEP_HOURS_UTC) + comandos/callbacks."""
    log.info("daemon iniciado · barridos agendados %s UTC · callbacks + comandos activos",
             cfg.daemon.sweep_hours_utc)

    state: dict = {"offers": _load_pool(cfg), "anchor_id": None, "last_sweep_key": ""}
    # FIX sweep-fantasma: el marcador persiste en DB — un restart no re-barre la hora ya hecha
    try:
        _c0 = database.connect(cfg)
        try:
            state["last_sweep_key"] = _load_state_flag(_c0)
        finally:
            _c0.close()
    except Exception as exc:
        log.warning("no pude leer last_sweep_key persistente (arranca fresco): %s", exc)
    _register_commands(cfg)

    # H4: migraciones al arranque del daemon (no esperar al primer barrido)
    try:
        _c = database.connect(cfg)
        try:
            database.init_db(_c)
        finally:
            _c.close()
    except Exception as exc:
        log.warning("init_db al arranque falló (se reintenta en el barrido): %s", exc)

    # mensaje de arranque (fancy, con resumen del pool) — SIN lista de jobs;
    # la ancla se crea solo tras un barrido (search agendado o manual)
    try:
        _c = database.connect(cfg)
        try:
            _q = lambda s: _c.execute(s).fetchone()[0]
            n_act = _q("SELECT COUNT(*) FROM ofertas WHERE active=1")
            n_ia = _q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model!=''")
            n_canal = _q("SELECT COUNT(*) FROM channel_posts")
        finally:
            _c.close()
        pct = int(100 * n_ia / n_act) if n_act else 0
        bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
        _tg_api(cfg, "sendMessage", {
            "chat_id": cfg.telegram.chat_id, "parse_mode": "HTML",
            "text": (f"🚀 <b>JobHunt iniciado</b> · {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"📁 Pool: <b>{n_act}</b> activas · 🧠 IA: <b>{pct}%</b> {bar}\n"
                     f"📢 Canal: {n_canal} posts históricos\n"
                     f"⏰ Barridos agendados: {cfg.daemon.sweep_hours_utc} UTC\n"
                     f"🧠 Batch nocturno: {cfg.ia.run_hours_utc} UTC\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"✅ Comandos listos — <code>/help</code> para el menú")})
    except Exception as exc:
        log.warning("mensaje de arranque falló (no tumba daemon): %s", exc)

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

        # 2. barrido por horas agendadas (una vez por hora agendada)
        # FIX: si hay enrich/search manual en curso, el agendado NO consume su marcador
        # → cuando la operación termine DENTRO de la misma hora, el próximo tick lo corre
        try:
            if _sweep_hours_due(cfg, state) and not _op_busy():
                state["last_sweep_key"] = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
                try:
                    _cs = database.connect(cfg)
                    try:
                        _save_state_flag(_cs, "last_sweep_key", state["last_sweep_key"])
                    finally:
                        _cs.close()
                except Exception as exc:
                    log.warning("no pude persistir last_sweep_key: %s", exc)
                log.info("barrido agendado (%s UTC)", datetime.now(timezone.utc).hour)
                threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()
        except Exception:
            log.exception("barrido agendado (paso 2) falló — daemon sigue vivo")

        # 3. batch IA nocturno (hora agendada en IA_RUN_HOURS_UTC, default 03)
        try:
            _ia_sweep_maybe(cfg, state)
        except Exception:
            log.exception("batch IA (paso 3) falló — daemon sigue vivo")

        # 4. digests del canal (diario/semanal/mensual según hora agendada)
        try:
            _digests_maybe(cfg, state)
        except Exception:
            log.exception("digests (paso 4) falló — daemon sigue vivo")
