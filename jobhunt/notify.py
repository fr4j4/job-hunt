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


def build_digest_text(offers: list[dict], cfg) -> str:
    """Contenido = insight; las ofertas viven SOLO en botones."""
    if not offers:
        return f"🔍 <i>Sin ofertas nuevas con score ≥ {cfg.alerts.min_score}</i> en este barrido."
    best = max(offers, key=lambda o: o.get("score", 0))
    stamp = datetime.now(timezone.utc).strftime("%d %b")
    lines = [
        f"📬 <b>{len(offers)} ofertas nuevas</b> · {stamp}",
        "",
        f"{score_emoji(best.get('score', 0))} <b>Mejor match:</b> {esc(best['title'][:70])}",
        f"   <code>{best.get('score', '?')}%</code> · {modality_tag(best.get('modality'))} "
        f"{role_tag(best['title'])} · {age_tag(best.get('date_posted', ''))} · {esc(best.get('company', '')[:25])}",
    ]
    if best.get("ai_summary"):
        lines.append(f"   💡 <i>{esc(best['ai_summary'])}</i>")
    if best.get("ai_fit_reason"):
        lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'])}</i>")
    lines.append("")
    lines.append(f"<i>Umbral {cfg.alerts.min_score}% · toca un botón para abrir la oferta</i>")
    return "\n".join(lines)[:4000]


def build_buttons(offers: list[dict], cfg) -> list[list[dict]]:
    rows = []
    for j in offers[:cfg.alerts.max_per_digest]:
        pct = j.get("score", 0)
        emp = (j.get("company") or "").strip()[:24]
        loc = (j.get("location") or "").split(",")[0].strip()[:20]
        label = (f"{score_emoji(pct)}{pct}% {modality_tag(j.get('modality'))}"
                 f"{role_tag(j['title'])}{age_tag(j.get('date_posted', ''))}")
        if emp:
            label += f" {emp}"
        if loc:
            label += f" · {loc}"
        label = re.sub(r"\s+", " ", label).strip()[:140]
        rows.append([{"text": label, "url": j.get("url", ""), "style": score_style(pct)}])
    return rows


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