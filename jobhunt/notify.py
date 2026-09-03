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
        return "📅?"
    delta = datetime.now(timezone.utc) - dt
    hours = int(delta.total_seconds() // 3600)
    if hours < 1: return "📅ahora"
    if hours < 24: return f"📅{hours}h"
    days = hours // 24
    if days < 14: return f"📅{days}d"
    weeks = days // 7
    if weeks < 5: return f"📅{weeks}w"
    return f"📅{days // 30}m"


# ---------- helpers de ubicación ----------

_LOC_ABBR = [  # orden importa: específico → general
    ("región metropolitana", "RM"),
    ("metropolitana", "RM"),
    ("metropolitan", "RM"),
    ("santiago", "Stgo"),
    ("valparaíso", "Valpo"),
    ("valparaiso", "Valpo"),
    ("concepción", "Conce"),
    ("concepcion", "Conce"),
    ("antofagasta", "Antofa"),
    ("viña del mar", "Viña"),
    ("vina del mar", "Viña"),
    ("chile", "CL"),
]


def abbr_loc(loc: str) -> str:
    """'Santiago Met' → 'Stgo', 'Chile' → 'CL', 'Las Condes' queda tal cual."""
    low = (loc or "").strip().lower()
    for full, ab in _LOC_ABBR:
        if full in low:
            return ab
    return (loc or "").strip()


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


def lang_tag(j) -> str:
    """' 🗣ENreq' / ' 🗣ENint' — idiomas pedidos por la oferta (desde ai_idiomas).

    Prioridad: idiomas excluyentes primero. Abrevia: EN/AL/FR/PT/CN/JP/IT + ! (excluyente)
    o nivel inicial. Máx 2 idiomas para no saturar la línea.
    """
    import json as _json
    raw = (j.get("ai_idiomas") or "").strip()
    if not raw:
        return ""
    try:
        idiomas = _json.loads(raw)
    except Exception:
        return ""
    if not isinstance(idiomas, list):
        return ""
    ABBR = {"inglés": "EN", "ingles": "EN", "alemán": "AL", "aleman": "AL", "german": "AL",
            "francés": "FR", "frances": "FR", "portugués": "PT", "portugues": "PT",
            "chino": "CN", "mandarín": "CN", "japonés": "JP", "italiano": "IT"}
    out = []
    for i in idiomas[:2]:
        if not isinstance(i, dict):
            continue
        ab = ABBR.get((i.get("idioma") or "").lower().strip())
        if not ab:
            continue
        flag = "!" if i.get("excluyente") else ""
        out.append(f"{ab}{flag}")
    if not out:
        return ""
    return " 🗣" + "/".join(out)


def compact_label(j, cap: int = 64) -> str:
    """Botón compacto ≤cap chars:
    emoji+score+ 💵+[Mod][Rol][Techs][Edad]+ empresa · ciudad
    (el monto va justo después del %; el título NO va: la lista numerada del texto lo muestra)."""
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
    ia = "🧠" if j.get("ia_model") else ""      # marcador: procesada por IA

    emp = (j.get("company") or "").strip()
    loc = abbr_loc((j.get("location") or "").split(",")[0])
    # presupuesto: fijo ≈ 6+3+6+7+4+len(sal) → libre para emp+loc
    fixed = 6 + len(mod) + len(rol) + len(techs) + len(edad) + len(sal) + 3
    libre = max(10, cap - fixed)
    emp = emp[:max(6, int(libre * 0.62))]
    loc = loc[:max(0, int(libre * 0.38))]
    body = " · ".join(p for p in (emp, loc) if p)
    label = f"{score_emoji(pct)}{pct}%{sal}{mod}{rol}{techs}{edad}{ia} {body}".strip()
    return re.sub(r"\s+", " ", label).strip()[:cap]


def _attr_esc(u: str) -> str:
    """Escapa un URL para atributo HTML (href)."""
    return (u or "").replace("&", "&amp;").replace('"', "%22")


def _mod_short(j: dict) -> str:
    """Modalidad corta [R]/[H]/[P]/[?] para vista tabular."""
    import unicodedata as _ud
    m_raw = (j.get("modality") or "").lower()
    m_norm = "".join(c for c in _ud.normalize("NFD", m_raw) if _ud.category(c) != "Mn")
    if "remot" in m_norm or "remote" in m_norm or "telecommute" in m_norm or j.get("remote_official") == 1:
        return "[R]"
    if "hibrid" in m_norm or "hybrid" in m_norm:
        return "[H]"
    if "presencial" in m_norm or "on-site" in m_norm:
        return "[P]"
    return "[?]"


def _role_short(j: dict) -> str:
    """Cargo corto de ancho fijo ≤8: TechLead Full Back Front Data IA Mob Ops QA Dev."""
    t = (j.get("title") or "").lower()
    for tag, pat in [("TechLead", r"tech lead|\bcto\b|lider técnico|líder técnico"),
                    ("Data", r"\bdata\b|datos|etl|databricks|snowflake|analytics"),
                    ("IA", r"\bia\b|\bai\b|machine learning|\bml\b|llm|generativa"),
                    ("Mobile", r"mobile|android|ios\b|flutter|react native"),
                    ("DevOps", r"devops|sre\b|infraestructura|platform engineer"),
                    ("QA", r"\bqa\b|testing|tester|calidad"),
                    ("Front", r"frontend|front-end|front end|vue\b|svelte"),
                    ("Back", r"backend|back-end|back end|\.net|java developer|python developer|node"),
                    ("Full", r"full ?stack|fullstack|software engineer|desarrollador|developer|programador|ingeniero")]:
        if re.search(pat, t):
            return tag
    return "Dev"


def _techs_short(j: dict, width: int = 8) -> str:
    """Techs abreviadas [Py·Jav]; si excede el ancho deja solo la primera."""
    found = []
    tl = (j.get("title") or "").lower() + " " + (j.get("techs") or "").lower()
    for k, abbr in [("python", "Py"), ("java", "Jav"), ("angular", "Ang"), ("react", "Rct"),
                    ("aws", "AWS"), ("node", "Node"), ("typescript", "TS"), (".net", ".NET"),
                    ("sql", "SQL"), ("kubernetes", "K8s"), ("docker", "Dkr"), ("golang", "Go"),
                    ("vue", "Vue"), ("spring", "Spr")]:
        if k in tl and abbr.lower() not in [f.lower() for f in found]:
            found.append(abbr)
        if len(found) == 2:
            break
    if not found:
        return " " * width
    s = "[" + "·".join(found) + "]"
    if len(s) > width:
        s = f"[{found[0]}]"
    return s.ljust(width)


def _age_short(date_posted: str) -> str:
    """Edad sin emoji (para vista tabular — 📅 no renderiza en todos los clientes)."""
    return age_tag(date_posted).lstrip("📅")


def table_block(offers: list[dict], links: bool = True) -> str:
    """Vista tabular con header: columnas fijas separadas por │, blanco si falta.
    score│sueldo│M│cargo│exp│techs│edad│empresa│IA — el 🔗 final abre la oferta."""
    lines = []
    # header: mismo emoji que las filas; 3 espacios calzan con el ancho real
    # de "⭐ 98%" de los datos (pct:>3 → 1 espacio + 2 dígitos) — delta 0 medido
    lines.append("⭐<code>   %│  $$$│M│cargo       │exp │idi│techs       │ant│empresa │IA</code>")
    for j in offers:
        pct = int(j.get("score") or 0)
        emoji = score_emoji(pct)
        sal = salary_tag(j).replace("💵", "").replace("USD", "$").strip()[:5]
        mod = _mod_short(j).strip("[]")
        if mod == "?":
            mod = " "
        tl = ((j.get("title") or "") + " " + (j.get("techs") or "")).lower()
        found = []
        for k, ab in [("python", "Py"), ("java", "Jav"), ("angular", "Ang"), ("react", "Rct"),
                      ("aws", "AWS"), ("node", "Node"), ("typescript", "TS"), ("kubernetes", "K8s"),
                      ("docker", "Dkr"), ("golang", "Go"), ("vue", "Vue"), ("spring", "Spr"),
                      (".net", ".NET"), ("sql", "SQL")]:
            if k in tl and ab.lower() not in [f.lower() for f in found]:
                found.append(ab)
            if len(found) == 3:
                break
        joined = "-".join(found)
        while joined and len(joined) > 11:
            found.pop()
            joined = "-".join(found)
        techs = joined.ljust(12) if joined else " " * 12
        raw_age = _age_short(j.get("date_posted") or "")
        age = "   " if "?" in raw_age else raw_age.replace("ahora", "0h")[:3].rjust(3)
        co = (j.get("company") or "").strip()[:8].ljust(8)
        rol = _role_short(j)[:12].ljust(12)
        exp = {"lead": "Lead", "senior": "Sr", "semi": "sSr", "junior": "Jr"}.get(
            (j.get("seniority_real") or "").strip().lower(), "   ")[:4].ljust(4)
        # idiomas pedidos: EN!/PT en 3 chars (vacío = sin dato o sin idiomas)
        idi = ""
        try:
            import json as _j
            lst = _j.loads(j.get("ai_idiomas") or "[]")
            ABBR = {"inglés": "EN", "ingles": "EN", "alemán": "AL", "aleman": "AL",
                    "francés": "FR", "frances": "FR", "portugués": "PT", "portugues": "PT",
                    "chino": "CN", "mandarín": "CN", "japonés": "JP", "italiano": "IT"}
            if isinstance(lst, list) and lst:
                primero = lst[0]
                if isinstance(primero, dict):
                    ab = ABBR.get((primero.get("idioma") or "").lower().strip())
                    if ab:
                        idi = (ab + ("!" if primero.get("excluyente") else "")).ljust(3)
        except Exception:
            pass
        idi = idi.ljust(3)
        ia = "*" if j.get("ia_model") else " "
        row = f"{emoji}{pct:>3}%│{sal:>5}│{mod}│{rol}│{exp}│{idi}│{techs}│{age}│{co}│{ia} "
        url = _attr_esc(j.get("url") or "")
        link = f' <a href="{url}">🔗</a>' if links and url else ""
        lines.append(f"<code>{row}</code>{link}")
    return "\n".join(lines)


def build_digest_text(offers: list[dict], cfg) -> str:
    """Texto = contexto: mejor match con detalle + tabla tabular de la página.
    El 🔗 de cada fila abre la oferta."""
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
        f" · {_age_short(best.get('date_posted', ''))} · {esc((best.get('company') or '')[:24])}"
        f" · {esc(abbr_loc(best.get('location') or ''))}",
    ]
    if best.get("ai_fit_reason"):
        lines.append(f"   🎯 <i>{esc(best['ai_fit_reason'][:140])}</i>")
    lines += ["", "<b>Esta página</b> (toca el 🔗 para abrir):", table_block(shown)]
    lines.append("")
    lines.append("<i>⭐ ≥85 · 🟢 ≥70 · 🟡 ≥55 · ⚪ resto · * = IA · toca el 🔗 para abrir</i>")
    return "\n".join(lines)[:4000]


def build_buttons(offers: list[dict], cfg) -> list[list[dict]]:
    """Solo botones de paginación inertes (el link está en el 🔗 de cada fila).
    Mantenido por compatibilidad con send_digest (modo `jobhunt run`)."""
    n = min(len(offers), cfg.alerts.max_per_digest)
    pages = max(1, (n + 4) // 5)
    return [[{"text": f"· {n} ofertas ·", "callback_data": "noop"}]] if pages else []



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
                kw = {}
                if b.get("url"):
                    kw["url"] = b["url"]
                if b.get("callback_data"):
                    kw["callback_data"] = b["callback_data"]
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