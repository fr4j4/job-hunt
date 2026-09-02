"""Digest Telegram: insight en texto + 1 botón por oferta (score >= umbral)."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)




def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def score_emoji(p: int) -> str:
    if p >= 85: return "⭐"
    if p >= 70: return "🟢"
    if p >= 55: return "🟡"
    return "⚪"


def score_style(p: int) -> str:
    if p >= 85: return "success"
    if p >= 70: return "primary"
    return "danger"


def modality_tag(m: str) -> str:
    return {"remoto": "[R]", "híbrido": "[H]", "presencial": "[P]"}.get((m or "").lower(), "[?]")


def role_tag(title: str) -> str:
    t = (title or "").lower()
    roles = [("[TechLead]", r"tech lead|\bcto\b|lider técnico|líder técnico"),
             ("[Data]", r"\bdata\b|datos|etl|databricks|snowflake|analytics"),
             ("[IA]", r"\bia\b|\bai\b|machine learning|\bml\b|llm|generativa"),
             ("[Mobile]", r"mobile|android|ios\b|flutter|react native"),
             ("[DevOps]", r"devops|sre\b|infraestructura|platform engineer"),
             ("[QA]", r"\bqa\b|testing|tester|calidad"),
             ("[Front]", r"frontend|front-end|front end|vue\b|svelte|angular developer|react developer"),
             ("[Back]", r"backend|back-end|back end|\.net|java developer|python developer|node"),
             ("[Full]", r"full ?stack|fullstack|software engineer|desarrollador|developer|programador|ingeniero")]
    for tag, pat in roles:
        if re.search(pat, t):
            return tag
    return "[Dev]"


def techs_tag(techs: str) -> str:
    found = [ab for ab in (techs.split(";") if techs else [])][:3]
    return "[" + "·".join(found) + "]" if found else ""


def age_tag(date_posted: str) -> str:
    try:
        dt = datetime.fromisoformat((date_posted or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "[?]"
    delta = datetime.now(timezone.utc) - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1: return "[ahora]"
    if hours < 24: return f"[{hours}h]"
    days = hours // 24
    if days < 14: return f"[{days}d]"
    weeks = days // 7
    if weeks < 5: return f"[{weeks}w]"
    return f"[{days // 30}m]"


# ---------- helpers de salario ----------

def salary_tag(j) -> str:
    """' 💵USD4k' / ' 💵$2.1M' — parsea CLP chileno ($ 2.100.000,00) y USD plano (4000)."""
    raw = (j.get("salary") or "").strip()
    if not raw or raw == "-":
        return ""
    is_usd = "usd" in raw.lower()
    # heurística: número plano < 10000 y sin prefijo $/CLP → sueldo mensual USD (ofertas remotas LinkedIn)
    if not is_usd and not raw.strip().startswith(("$", "clp", "CLP")):
        digits = re.sub(r"\D", "", raw.split(",")[0])
        if digits and 0 < int(digits) < 10000:
            is_usd = True
    m = re.search(r"(\d[\d.,]*)", raw)
    if not m:
        return ""
    rawnum = m.group(1)
    # formato chileno: '.' miles + ',' decimales → quitar puntos, coma=decimal
    if rawnum.count(".") >= 1 and "," in rawnum:
        num = rawnum.replace(".", "").replace(",", ".")
    else:
        num = rawnum.replace(",", "")
    try:
        val = float(num)
    except ValueError:
        return ""
    if is_usd:
        return f" 💵USD{int(val)//1000}k" if val >= 1000 else f" 💵USD{int(val)}"
    if val >= 1_000_000:
        return f" 💵${val/1_000_000:.1f}M"
    if val >= 1000:
        return f" 💵${int(val)//1000}k"
    return f" 💵${int(val)}"


def compact_label(j, cap: int = 64) -> str:
    """Botón compacto ≤cap chars:
    emoji+score+[Mod][Rol][Techs][Edad]+ 💵 · empresa · ciudad
    (el título NO va: la lista numerada del texto lo muestra)."""
    pct = int(j.get("score") or 0)
    # modalidad abreviada
    m_raw = (j.get("modality") or "").lower()
    import unicodedata as _ud
    m_norm = "".join(c for c in _ud.normalize("NFD", m_raw) if _ud.category(c) != "Mn")
    if "remot" in m_norm or "remote" in m_norm or "telecommute" in m_norm:
        mod = "[R]"
    elif "hibrid" in m_norm or "hybrid" in m_norm:
        mod = "[H]"
    elif "presencial" in m_norm or "on-site" in m_norm:
        mod = "[P]"
    else:
        mod = "[?]"
    rol = role_tag(j.get("title") or "")
    # techs máx 2 abreviadas
    found = []
    tl = (j.get("title") or "").lower()
    for k, abbr in [("python","Py"),("java","Jav"),("angular","Ang"),("react","Rct"),
                    ("aws","AWS"),("node","Node"),("typescript","TS"),("sql","SQL"),
                    ("kubernetes","K8s"),("docker","Dkr"),("golang","Go")]:
        if k in tl:
            found.append(abbr)
        if len(found) == 2:
            break
    techs = "[" + "·".join(found) + "]" if found else ""
    edad = age_tag(j.get("date_posted") or "")
    sal = salary_tag(j)

    emp = (j.get("company") or "").strip()
    loc = (j.get("location") or "").split(",")[0].strip()
    # presupuesto: fijo ≈ 6+3+6+7+4+len(sal) → libre para emp+loc
    fixed = 6 + len(mod) + len(rol) + len(techs) + len(edad) + len(sal) + 3
    libre = max(10, cap - fixed)
    emp = emp[:max(6, int(libre * 0.62))]
    loc = loc[:max(0, int(libre * 0.38))]
    body = f"{emp}{' · ' + loc if loc else ''}"
    label = f"{score_emoji(pct)}{pct}%{mod}{rol}{techs}{edad}{sal} {body}".strip()
    return re.sub(r"\s+", " ", label).strip()[:cap]


def build_digest_text(offers: list[dict], cfg) -> str:
    """Texto = contexto: mejor match con detalle + lista numerada de la página.
    La fila N del texto = botón N (los botones solo llevan tags, sin título)."""
    if not offers:
        return f"🔍 <i>Sin ofertas con score ≥ {cfg.alerts.min_score}</i> en este barrido."
    best = max(offers, key=lambda o: o.get("score", 0))
    stamp = datetime.now(timezone.utc).strftime("%d %b %H:%M")
    shown = offers[:cfg.alerts.max_per_digest]
    lines = [
        f"📬 <b>Ofertas ≥{cfg.alerts.min_score}</b> · <i>{len(offers)} activas</i> · {stamp}",
        "",
        f"{score_emoji(best.get('score', 0))} <b>Mejor match:</b> {esc(best['title'][:70])}",
        f"   <code>{best.get('score', '?')}%</code>{salary_tag(best)}"
        f" · {modality_tag(best.get('modality'))} {role_tag(best['title'])}"
        f" · {age_tag(best.get('date_posted', ''))} · {esc((best.get('company') or '')[:24])}",
    ]
    if best.get("ai_fit_reason"):
        lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'][:140])}</i>")
    lines += ["", "<b>Esta página:</b>"]
    for i, j in enumerate(shown, 1):
        money = salary_tag(j)
        title = esc((j.get("title") or "?")[:52])
        lines.append(f"{i}. {title} ({j.get('score', '?')}%){money}")
    lines.append("")
    lines.append("<i>Toca el botón para abrir la oferta — fila N = botón N</i>")
    return "\n".join(lines)[:4000]


def build_buttons(offers: list[dict], cfg) -> list[list[dict]]:
    """1 botón por fila (ancho completo): tags compactos, sin título, cap 64 chars."""
    return [[{"text": compact_label(j), "url": j.get("url", ""), "style": score_style(j.get("score", 0))}]
            for j in offers[:cfg.alerts.max_per_digest]]



def send_digest(cfg, offers: list[dict]) -> bool:
    """Envía digest. True si Telegram aceptó."""
    if not cfg.telegram.enabled or not cfg.telegram.bot_token or not cfg.telegram.chat_id:
        return False
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for row in build_buttons(offers, cfg):
            btns = []
            for b in row:
                kw = {"url": b["url"]}
                if b.get("style"):
                    kw["style"] = b["style"]
                btns.append(InlineKeyboardButton(b["text"], **kw))
            rows.append(btns)
        markup = InlineKeyboardMarkup(rows).to_json()
    except ImportError:
        # sin PTB: serializar markup a mano (style puede fallar en PTB viejas)
        kb = [[{k: v for k, v in b.items() if k != "style"} for b in row] for row in build_buttons(offers, cfg)]
        markup = json.dumps({"inline_keyboard": kb})
    body = {
        "chat_id": cfg.telegram.chat_id,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "text": build_digest_text(offers, cfg),
        "reply_markup": markup,
    }
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{cfg.telegram.bot_token}/sendMessage",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        log.warning("telegram send falló: %s", e)
        return False