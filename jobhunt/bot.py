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
from pathlib import Path

from .config import Config
from . import db as database
from .cli import cmd_run
from .notify import (esc, score_emoji, score_style, modality_tag, role_tag, techs_tag,
                     age_tag, salary_tag, compact_label, abbr_loc, table_block, _age_short)

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


def _chat_allowed(cfg: Config, chat_id) -> bool:
    """True si el chat está en TELEGRAM_ALLOWED_CHATS (vacío = sin restricción)."""
    if not cfg.telegram.allowed_chats:
        return True
    try:
        return int(chat_id) in cfg.telegram.allowed_chats
    except (TypeError, ValueError):
        return False


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
            # incluir el body de Telegram en el error: la causa real vive ahí
            # ("message is not modified", "message ... not found", etc.)
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                detail = ""
            raise RuntimeError(f"{exc} {detail}".strip()) from None
    raise last_exc or RuntimeError("unreachable")


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


_SEARCH_STATE: dict = {"running": False, "t0": 0.0}
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
        fase = {"fuente": "preparando", "query": "", "page": 0}

        def on_phase(fuente: str, query: str = "", page: int = 0):
            """Actualiza estado y edita el MISMO mensaje (throttle 15s)."""
            fase.update(fuente=fuente, query=query, page=page)
            nonlocal msg_id
            if not msg_id or time.time() - t0 < 15:
                return
            mins = int(time.time() - t0) // 60
            linea = f"   ▸ <b>{esc(fuente)}</b>"
            if query:
                linea += f' — "{esc(query[:40])}"'
            if page:
                linea += f" · pág {page}"
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
        "/search — gatilla una búsqueda ahora (reporta inicio, término y error)",
        "/enrich — corre el batch IA ahora (modalidad, sueldo, inglés, techs…)",
        "/enrich status — avance del batch IA (o tamaño de la cola)",
        "/report — análisis completo del mercado con gráficos → PDF",
        "/report status · /report list — avance del reporte · historial de PDFs",
        "/latest — últimas ofertas registradas (default 10, /latest 20 para más)",
        "/stats — cobertura del pool (procesadas IA, datos faltantes)",
        "/channel — estado del canal (umbral, cola, distribución market score)",
        "/score N — ofertas con score ≥ N (ej: /score 60)",
        "/jobs [filtros] — filtra el pool (combinables):",
        "    remote · hybrid · onsite · salary (con sueldo publicado) ·",
        "    salary2.5 (≥$2.5M) · 2.5 / 500k / 2.500.000 ·",
        "    sinen (sin idioma excluyente) · en (pide inglés)",
        "    ubicación: stgo, temuco, valpo, conce, araucania o texto libre",
        "    ej: <code>/jobs remote salary2.5</code> · <code>/jobs sinen</code> · <code>/jobs en remote</code>",
        "/help — esta ayuda",
    ])


def _stats_text(cfg: Config) -> str:
    """Cobertura del pool: procesadas IA, pendientes, qué datos faltan."""
    conn = database.connect(cfg)
    try:
        q = lambda s: conn.execute(s).fetchone()[0]
        total = q("SELECT COUNT(*) FROM ofertas WHERE active=1")
        ia = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model != ''")
        con_mod = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND modality != ''")
        con_sal = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND salary != ''")
        en_cola = q("SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model='' "
                    "AND (modality='' OR salary='' OR ai_fit_reason='')")
        model = conn.execute("SELECT ia_model FROM ofertas WHERE ia_model != '' "
                             "ORDER BY last_seen DESC LIMIT 1").fetchone()
        lines = [
            "📊 <b>Estado del pool</b>",
            "",
            f"Activas: <code>{total}</code>",
            f"🧠 Procesadas por IA: <code>{ia}</code> ({ia * 100 // max(total, 1)}%)",
            f"   Modelo: <code>{model[0] if model else '—'}</code>",
            f"   En cola IA: <code>{en_cola}</code> (batch nocturno 03:00 UTC o /enrich)",
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
    arg = parts[1] if len(parts) > 1 else ""
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
                    return
                threading.Thread(target=_ia_batch_async, args=(cfg, chat_id), daemon=True).start()
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
                threading.Thread(target=_report_async, args=(cfg, chat_id), daemon=True).start()
        elif cmd == "/stats":
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": _stats_text(cfg)})
        elif cmd == "/channel":
            conn = database.connect(cfg)
            try:
                from .channel import (publish_channel, publish_daily_digest,
                                      publish_weekly_remote, publish_weekly_salary,
                                      publish_trends, channel_status)
                action = (arg or "").strip().lower()
                confirm = action.endswith(" confirm")
                if confirm:
                    action = action.replace(" confirm", "").strip()
                api = _tg_api_for_channel(cfg)
                if action == "":
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": channel_status(conn, cfg)})
                elif action in ("publish", "daily", "weekly-remote", "weekly-salary",
                                "weekly", "trends", "all", "dry"):
                    def _run():
                        import time as _t
                        if action in ("publish", "dry", "all"):
                            stats = publish_channel(cfg, conn, api, dry_run=(action == "dry"))
                            if action == "dry":
                                for p in stats.get("dry_run_preview", []):
                                    _tg_api(cfg, "sendMessage", {
                                        "chat_id": chat_id, "text": "👁 preview:\n" + p,
                                        "parse_mode": "HTML", "disable_web_page_preview": True})
                                    _t.sleep(0.5)
                            else:
                                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                             "text": f"📢 canal publish: {stats}"})
                        if action in ("daily", "all"):
                            ok = publish_daily_digest(cfg, conn, api)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "📊 daily digest enviado" if ok else
                                                                 "📊 daily digest: sin candidatas o ya enviado hoy"})
                            _t.sleep(1)
                        if action in ("weekly-remote", "weekly", "all"):
                            ok = publish_weekly_remote(cfg, conn, api)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "🌍 weekly-remote enviado" if ok else
                                                                 "🌍 weekly-remote: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("weekly-salary", "weekly", "all"):
                            ok = publish_weekly_salary(cfg, conn, api)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "💰 weekly-salary enviado" if ok else
                                                                 "💰 weekly-salary: sin candidatas o ya enviado"})
                            _t.sleep(1)
                        if action in ("trends", "all"):
                            ok = publish_trends(cfg, conn, api)
                            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                         "text": "📈 trends enviado" if ok else
                                                                 "📈 trends: sin datos o ya enviado este mes"})
                    threading.Thread(target=_run, daemon=True).start()
                elif action == "reset":
                    if confirm:
                        n = conn.execute("""UPDATE ofertas SET notified_channel_at=''
                            WHERE active=1 AND notified_channel_at != ''""").rowcount
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
                                                             f"<code>/channel reset confirm</code>"})
                else:
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": "❓ /channel <action>\n"
                                                         "<code>publish · daily · weekly-remote · "
                                                         "weekly-salary · weekly · trends · all · dry · reset</code>"})
            finally:
                conn.close()
        elif cmd == "/db":
            action = (arg or "").strip().lower()
            confirm = action.endswith(" confirm")
            if confirm:
                action = action.replace(" confirm", "").strip()
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
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                        "text": f"🗄 <b>DB</b>\n✅ activas: {act}\n🪦 inactivas: {inact} "
                                f"(>30d purgable: {old})\n🚫 no-dev activas: {nondev}\n"
                                f"purge disponible: <code>old</code> ({old}) · <code>nondev</code> ({nondev})"})
                finally:
                    conn.close()
            elif action in ("old", "nondev"):
                if confirm:
                    conn = database.connect(cfg)
                    try:
                        import shutil, pathlib
                        bak = pathlib.Path(cfg.data_dir) / "backups"
                        bak.mkdir(exist_ok=True)
                        shutil.copy2(cfg.db_path if hasattr(cfg, 'db_path') else cfg.data_dir / "ofertas.sqlite",
                                     bak / f"ofertas-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.sqlite")
                        if action == "old":
                            n = conn.execute("""DELETE FROM ofertas WHERE active=0
                                AND last_seen < datetime('now', '-30 days')""").rowcount
                        else:
                            n = conn.execute("""DELETE FROM ofertas WHERE active=1
                                AND rol_categoria IN ('Ingeniería no-software','No-tech','Profesor/Formación',
                                'Analista/Empresa')""").rowcount
                        conn.commit()
                        _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                     "text": f"🗑 {n} ofertas eliminadas ({action}) — "
                                                             f"backup previo en data/backups/"})
                        log.info("db purge %s: %d filas", action, n)
                    finally:
                        conn.close()
                else:
                    _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                                 "text": f"⚠️ /db purge {action}: DELETE físico. "
                                                         f"Confirma con: <code>/db {action} confirm</code>"})
            else:
                _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                             "text": "❓ /db <action>\n<code>stats · old · nondev</code>"})
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


_IA_STATE: dict = {"running": False, "done": 0, "total": 0, "current": "", "t0": 0.0}


def _ia_batch_async(cfg: Config, chat_id: int | None):
    """Batch IA con ACK inicial, progreso por oferta y resumen final.
    Nunca tumba el daemon."""
    if _IA_STATE["running"]:
        if chat_id:
            _tg_api(cfg, "sendMessage", {"chat_id": chat_id, "parse_mode": "HTML",
                                         "text": "🧠 Ya hay un batch IA corriendo — "
                                                 "<code>/enrich status</code> para ver el avance"})
        return
    try:
        conn = database.connect(cfg)
        try:
            from .enrich import run_ia_batch, profile_description, ia_queue_count
            cola = ia_queue_count(conn)
        finally:
            conn.close()
        if chat_id:
            _tg_api(cfg, "sendMessage", {
                "chat_id": chat_id, "parse_mode": "HTML",
                "text": f"🧠 <b>Batch IA iniciado</b> — {cola} ofertas en cola · "
                        f"avance con <code>/enrich status</code>"})
        _IA_STATE.update(running=True, done=0, total=0, t0=time.time())
        t0 = time.time()
        conn = database.connect(cfg)
        try:
            done = run_ia_batch(conn, cfg, profile_description(cfg),
                                progress=(_mk_progress_cb(cfg, chat_id) if chat_id else None))
        finally:
            conn.close()
        dur = int(time.time() - t0)
        _IA_STATE["running"] = False
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
        _IA_STATE["running"] = False
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
    """Callback de progreso: actualiza 1 mensaje de estado (throttle 1 msg/30s)."""
    st = {"msg_id": None, "t": 0.0}

    def cb(done: int, total: int, title: str) -> None:
        _IA_STATE.update(done=done, total=total, current=title)
        now = time.time()
        txt = (f"🧠 Batch IA — <code>{done}/{total}</code>\n"
               f"   {esc(title[:50])}")
        try:
            if st["msg_id"] or now - st["t"] >= 30:
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
    """Dispara el batch IA si toca la hora agendada (3:00 UTC por defecto)."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return
    if not _ia_hours_due(cfg, state.setdefault("ia_log", {})):
        return
    state["ia_log"]["day"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("batch IA agendado (%s) — disparando", cfg.ia.run_hours_utc)
    threading.Thread(target=_ia_batch_async, args=(cfg, None), daemon=True).start()


def _count_gate_sql(cfg: Config) -> str:
    """Conteo de candidatas del gate (para preview de /channel reset)."""
    return (f"SELECT COUNT(*) FROM ofertas WHERE active=1 AND market_score >= {int(cfg.channel.min_score)} "
            f"AND date_canonical >= date('now', '-{int(cfg.channel.max_age_days)} days')")


def _tg_api_for_channel(cfg: Config):
    def call(method: str, payload: dict) -> dict:
        try:
            return _tg_api(cfg, method, payload)
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    return call


def _digests_maybe(cfg: Config, state: dict) -> None:
    """Digests del canal (B diario / C+D semanal / E mensual) — patrón _ia_sweep_maybe."""
    if not cfg.channel.enabled or not cfg.channel.chat_id:
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
                keys[k] = 1
                publish_daily_digest(cfg, conn, _tg_api_for_channel(cfg))
        # semanal
        if cfg.channel.digest_weekly and now.weekday() == cfg.channel.digest_weekly_day_utc \
                and now.hour >= cfg.channel.digest_weekly_hour_utc:
            k = f"weekly:{now.strftime('%G-W%V')}"
            if keys.get(k) != 1:
                keys[k] = 1
                publish_weekly_digests(cfg, conn, _tg_api_for_channel(cfg))
        # tendencias (día 1 de mes)
        if cfg.channel.digest_tendencias and now.day == 1 and now.hour >= 13:
            k = f"trends:{now.strftime('%Y-%m')}"
            if keys.get(k) != 1:
                keys[k] = 1
                publish_trends(cfg, conn, _tg_api_for_channel(cfg))
    except Exception as e:
        log.warning("digests falló (no tumba daemon): %s", e)
    finally:
        conn.close()


def _register_commands(cfg: Config) -> None:
    """Registra los comandos en Telegram (menú "/" del cliente)."""
    commands = [
        {"command": "search", "description": "Gatilla una búsqueda ahora"},
        {"command": "enrich", "description": "Corre el batch IA ahora (rellena datos faltantes)"},
        {"command": "report", "description": "Análisis de mercado completo con PDF"},
        {"command": "latest", "description": "Últimas ofertas registradas"},
        {"command": "stats",  "description": "Cobertura IA y datos del pool"},
        {"command": "channel", "description": "Canal: status, publish, daily, weekly-remote/salary, trends, all, dry, reset"},
        {"command": "db", "description": "DB: stats y purge (old/nondev) con confirm"},
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
        cmd_run(cfg, notify=False, on_phase=on_phase)   # barrido SIN mensaje push
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


def _sweep_hours_due(cfg: Config, state: dict) -> bool:
    """True si la hora UTC actual está agendada y no se barrió en esa hora exacta."""
    now = datetime.now(timezone.utc)
    key = now.strftime("%Y-%m-%d-%H")
    return now.hour in cfg.daemon.sweep_hours_utc and state.get("last_sweep_key") != key


def run_daemon(cfg: Config) -> None:
    """Proceso residente: sin barridos al arrancar (carga pool desde DB) +
    barridos por horas agendadas (DAEMON_SWEEP_HOURS_UTC) + comandos/callbacks."""
    log.info("daemon iniciado · barridos agendados %s UTC · callbacks + comandos activos",
             cfg.daemon.sweep_hours_utc)

    state: dict = {"offers": _load_pool(cfg), "anchor_id": None, "last_sweep_key": ""}
    _register_commands(cfg)

    # ancla inicial con el pool existente — SIN barrido de fuentes al arrancar
    _refresh_anchor(cfg, state)

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
        if _sweep_hours_due(cfg, state):
            state["last_sweep_key"] = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
            log.info("barrido agendado (%s UTC)", datetime.now(timezone.utc).hour)
            threading.Thread(target=lambda: sweep(cfg, state), daemon=True).start()

        # 3. batch IA nocturno (hora agendada en IA_RUN_HOURS_UTC, default 03)
        _ia_sweep_maybe(cfg, state)

        # 4. digests del canal (diario/semanal/mensual según hora agendada)
        try:
            _digests_maybe(cfg, state)
        except Exception as exc:
            log.warning("digests error: %s", str(exc)[:120])
