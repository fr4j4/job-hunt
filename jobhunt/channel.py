"""Modo canal (broadcast): fechas canónicas, gates, publish y digests.

Decisiones de producto implementadas (spec-canal-v3-final.md):
- Republicación = evento nuevo (dedup SOLO por group_id vía notified_channel_at)
- Ventana de antigüedad: date_canonical = min(date_posted, first_seen), 14d default
- Canal = broadcast puro: posts individuales + digests, sin paginación
"""
from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)

# ---------------- fechas ----------------

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
          "diciembre": 12, "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
          "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}


def normalize_date(raw: str | int | float | None, now: datetime | None = None) -> str:
    """Convierte los formatos de fecha de las 8 fuentes a YYYY-MM-DD ('' si no parseable).

    LinkedIn: ISO datetime · Laborum: DD-MM-YYYY · Jooble: 'Publicado el 21 de Jul, 2026'
    Computrabajo: 'Hace X horas/días' (relativo) · AIRA: publication_days (int) · epoch.
    """
    if raw is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        # publication_days (días desde publicación) o epoch
        n = int(raw)
        if 0 <= n < 400:            # publication_days
            return (now - timedelta(days=n)).date().isoformat()
        if n > 10**12:              # epoch ms
            return datetime.fromtimestamp(n / 1000, timezone.utc).date().isoformat()
        if n > 10**9:               # epoch s
            return datetime.fromtimestamp(n, timezone.utc).date().isoformat()
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # ISO completo o YYYY-MM-DD
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return s[:10]
    # DD-MM-YYYY (Laborum)
    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    # 'Publicado el 21 de Jul, 2026' (Jooble)
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóúñ]+),?\s+(\d{4})", s, re.I)
    if m:
        mes = _MESES.get(m.group(2).lower()[:3])
        if mes:
            try:
                return date(int(m.group(3)), mes, int(m.group(1))).isoformat()
            except ValueError:
                return ""
    # 'Hace X horas/días' / 'Hoy' / 'Ayer' (Computrabajo)
    m = re.search(r"[Hh]ace\s+([\d\s]+)\s*(minuto|hora|día|dia|semana)", s)
    if m:
        n = int(re.sub(r"\D", "", m.group(1)) or 1)
        unit = m.group(2).lower()
        delta = {"minuto": 0, "hora": 0, "día": n, "dia": n, "semana": n * 7}[unit]
        return (now - timedelta(days=delta)).date().isoformat()
    if re.search(r"\bhoy\b", s, re.I):
        return now.date().isoformat()
    if re.search(r"\bayer\b", s, re.I):
        return (now - timedelta(days=1)).date().isoformat()
    # epoch string
    if s.isdigit():
        return normalize_date(int(s), now)
    return ""


def canonical_date(row: dict, now: datetime | None = None) -> str:
    """Fecha canónica de la oferta: min(date_posted, first_seen) con clamp.

    - sin date_posted → first_seen (cota honesta: Indeed filtro 168h)
    - date_posted más fresca que first_seen → clamp a first_seen (anti repost-fresh)
    """
    now = now or datetime.now(timezone.utc)
    d = normalize_date(row.get("date_posted") or "", now)
    fs = str(row.get("first_seen") or "")[:10]
    if not re.match(r"\d{4}-\d{2}-\d{2}", fs):
        return d
    if not d:
        return fs
    return d if d <= fs else fs


def age_days(row: dict, now: datetime | None = None) -> int:
    """Días de antigüedad según date_canonical. Negativa → 0."""
    now = now or datetime.now(timezone.utc)
    c = canonical_date(row, now)
    if not re.match(r"\d{4}-\d{2}-\d{2}", c):
        return 0
    try:
        dd = (datetime.now(timezone.utc).date() if now is None
              else now.date()) - date.fromisoformat(c)
        return max(0, dd.days)
    except ValueError:
        return 0


# ---------------- gate dev ----------------

_DEV_CATEGORIES = {"Full Stack", "Backend", "Frontend", "Data", "Mobile", "AI/ML",
                   "Tech Lead", "DevOps/Cloud", "QA", "Software", "Seguridad"}

# stems que delatan roles dev en texto libre (modo lote puede escribir
# "Backend Developer", "Fullstack Developer", "Desarrollo Móvil", "Cloud"…)
_DEV_STEMS = ("dev", "backend", "frontend", "full", "stack", "data", "mobile", "móvil",
              "movil", "ai", "ml", "lead", "cloud", "qa", "software", "seguridad",
              "secops", "infra", "sistemas", "informátic", "informatic", "python",
              "java", "node", "react", "devops")


def _categorias_dev(rc: str) -> set[str]:
    """Normaliza rol_categoria libre → categorías dev canónicas:
    'Backend Developer' → {Backend} · 'Fullstack Developer' → {Full Stack, Backend, Frontend}
    'DevOps' → {DevOps/Cloud} · 'Desarrollo Móvil' → {Mobile} · 'Cloud / Infra' → {DevOps/Cloud}."""
    r = rc.lower()
    out: set[str] = set()
    if "backend" in r:
        out.add("Backend")
    if "frontend" in r or "front" in r:
        out.add("Frontend")
    if "full" in r or "stack" in r:
        out.add("Full Stack")
    if "móvil" in r or "movil" in r or "mobile" in r:
        out.add("Mobile")
    if "data" in r or "datos" in r:
        out.add("Data")
    if "ai" in r or "ml" in r:
        out.add("AI/ML")
    if "tech lead" in r or "lead" in r:
        out.add("Tech Lead")
    if "devops" in r:
        out.add("DevOps/Cloud")
    if "cloud" in r or "infra" in r:
        out.add("DevOps/Cloud")
    if "qa" in r or "testing" in r:
        out.add("QA")
    if "software" in r or "desarroll" in r:
        out.add("Software")
    if "seguridad" in r or "secops" in r or "security" in r:
        out.add("Seguridad")
    return out


_NONDEV_CATEGORIES = {"Ingeniería no-software", "Analista/Empresa", "Profesor/Formación",
                      "Soporte/TI", "No-tech", "Otro"}


def is_dev(rol_categoria: str | None, title: str, cfg: Config, description: str = "") -> bool:
    """Gate dev: rol_categoria IA primero; regex SOLO en modo degradado (IA apagada).

    spec-techs-dev-gate §2.3: con IA activa (cfg.ia.enabled=true), rol_categoria es
    la ÚNICA fuente — sin rol → no dev (espera a que la IA procese). Con IA apagada,
    la regex corregida (word boundaries + lookahead) se ejecuta sobre título+descripción.
    La IA es la autoridad: categoría no-dev explícita → False siempre."""
    rc = (rol_categoria or "").strip()
    if rc:
        if rc in _NONDEV_CATEGORIES:
            return False
        if rc in _DEV_CATEGORIES:
            return True
        if _categorias_dev(rc) & _DEV_CATEGORIES:
            return True
    if cfg.ia.enabled:
        # IA activa: sin rol_categoria → no dev (la regex NO se ejecuta — §2.3)
        return False
    # modo degradado (IA apagada): regex corregida sobre título + descripción
    t = (title or "").lower()
    d = (description or "").lower()
    if re.search(cfg.relevance.nontech_titles, t, re.I):
        return False
    return bool(re.search(
        r"\bdev(?:eloper|ops)?\b|\bdesarroll\w*\b(?=\s+(?:de\s+)?(?:software|aplicaciones|web|backend|frontend|api|sistemas|app))|"
        r"\bsoftware\b|\bbackend\b|\bfrontend\b|\bfull.?stack\b|\bdata\b|\bpython\b|\bjava\b|\bqa\b|\bdevops\b|\bsistemas\b|"
        r"\binformátic\w*\b|\binformatic\w*\b", t + " " + d, re.I))


# ---------------- render de posts ----------------

_ABBR_FUENTE = {"linkedin": "LinkedIn", "computrabajo": "CB", "indeed": "Indeed",
                "glassdoor": "Glassdoor", "laborum": "Laborum", "jooble": "Jooble",
                "accenture": "Accenture", "aira": "AIRA"}


def _fuente(row: dict) -> str:
    src = (row.get("source") or "").split(":")[0].lower()
    return _ABBR_FUENTE.get(src, src[:10] or "?")


def render_offer_post(row: dict) -> tuple[str, dict | None]:
    """Post individual de oferta al canal (spec v3 §5-A) con botón URL.

    Retorna (texto, reply_markup | None). Líneas sin dato se omiten.
    """
    from .notify import esc
    from .scoring import _salary_to_clp_monthly

    lines: list[str] = []
    ms = row.get("market_score") or 0
    lines.append(f"🎯 [<b>{ms}</b>] {esc((row.get('title') or 'Sin título')[:110])}")
    meta: list[str] = []
    if row.get("company"):
        meta.append(f"🏢 {esc(row['company'][:40])}")
    mod = (row.get("modality") or "").strip()
    loc = esc((row.get("location") or "").strip()[:40])
    if mod:
        meta.append(f"📍 {esc(mod)}" + (f" · {loc}" if loc else ""))
    elif loc:
        meta.append(f"📍 {loc}")
    if meta:
        lines.append(" · ".join(meta))
    sal = _salary_to_clp_monthly(row.get("salary") or "", row.get("description") or "")
    if sal:
        lines.append(f"💰 ${sal:,}".replace(",", "."))
    techs = [t.strip() for t in (row.get("techs") or "").split(";") if t.strip()][:6]
    if techs:
        lines.append("🧰 " + esc(" · ".join(techs)))
    opinion = (row.get("ai_opinion") or "").strip()
    if opinion:
        lines.append(f"💬 {esc(opinion[:200])}")
    tail: list[str] = []
    ing = (row.get("ai_idiomas") or "").strip()
    if ing and "inglés" in ing.lower():
        excl = '"excluyente": true' in ing
        tail.append("🗣 EN!" if excl else "🗣 EN")
    edad = age_days(row)
    if edad or row.get("date_canonical"):
        tail.append(f"📅 {edad}d" if edad < 14 else "📅 >2 sem")
    tail.append(f"🌐 {_fuente(row)}")
    if tail:
        lines.append(" · ".join(tail))
    text = "\n".join(lines)
    url = (row.get("url") or "").strip()
    if url:
        kb = {"inline_keyboard": [[{"text": "🔗 Ver y postular", "url": url}]]}
        return text, kb
    return text, None


# ---------------- publish ----------------

_GATE_SQL = """SELECT * FROM ofertas
WHERE active=1 AND market_score >= :min_score
  AND notified_channel_at = ''
  AND date_canonical >= date('now', '-' || :max_age || ' days')
  ORDER BY market_score DESC, first_seen DESC"""


def select_channel_offers(conn, cfg: Config, require_ia: bool = False) -> list[dict]:
    """Candidatas al canal (market gate + edad + dev + no notificadas).

    require_ia=True: solo ofertas ya revisadas por IA (ia_model != '') —
    garantiza que todo post lleve comentario/editorial generado.
    """
    sql = _GATE_SQL
    if require_ia:
        sql = _GATE_SQL.replace("WHERE active=1", "WHERE active=1 AND ia_model != ''")
    rows = conn.execute(sql, {
        "min_score": cfg.channel.min_score,
        "max_age": cfg.channel.max_age_days,
    }).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if cfg.channel.require_dev and not is_dev(d.get("rol_categoria"), d.get("title") or "", cfg,
                                                  d.get("description") or ""):
            continue
        if cfg.channel.max_first_seen_hours:
            fs = str(d.get("first_seen") or "")
            try:
                dt = datetime.fromisoformat(fs.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                if hours > cfg.channel.max_first_seen_hours:
                    continue
            except ValueError:
                pass
        out.append(d)
    return out[:cfg.channel.max_posts]


def publish_channel(cfg: Config, conn, tg_api, dry_run: bool = False,
                    budget: int | None = None, require_ia: bool = False,
                    drain: bool = False) -> dict:
    """Publica al canal las candidatas (gates spec v3 §4, presupuesto v4.1 §3.3). Retorna stats.

    tg_api: callable (method, payload) → dict (el bot inyecta _tg_api).
    notified_channel_at se setea SOLO si Telegram aceptó (ok=true).
    budget: tope de posts de ESTA invocación (presupuesto restante del barrido —
    v4.1 A3); si None, rige cfg.channel.max_posts.
    drain=True: IGNORA max_posts/budget y drena TODA la cola pendiente
    (comando manual /channel_publish*; el barrido automático sigue con budget).
    Commit POR POST (C3): un crash entre send y commit no pierde las marcas ya enviadas.
    require_ia=True: solo ofertas revisadas por IA (ia_model != '').
    """
    stats: dict = {"candidates": 0, "posted": 0, "skipped_age": 0, "skipped_score": 0,
                   "skipped_notified": 0, "skipped_dev": 0}
    if not cfg.channel.enabled or not cfg.channel.chat_id:
        return stats

    # todas las que cumplen score+notified (sin tope) para stats reales de skips
    gate_sql = _GATE_SQL
    if require_ia:
        gate_sql = _GATE_SQL.replace("WHERE active=1", "WHERE active=1 AND ia_model != ''")
    rows = [dict(r) for r in conn.execute(gate_sql, {
        "min_score": cfg.channel.min_score, "max_age": cfg.channel.max_age_days}).fetchall()]
    stats["candidates"] = len(rows)

    if drain:
        tope = len(rows)                 # drena todo lo pendiente (manual)
    elif budget is not None:
        tope = min(cfg.channel.max_posts, budget)
    else:
        tope = cfg.channel.max_posts
    if tope <= 0:
        return stats

    posteadas = []
    for r in rows:
        if cfg.channel.require_dev and not is_dev(r.get("rol_categoria"), r.get("title") or "", cfg,
                                                  r.get("description") or ""):
            stats["skipped_dev"] += 1
            continue
        if len(posteadas) >= tope:
            break
        posteadas.append(r)

    if dry_run:
        stats["dry_run_preview"] = [{"text": t, "kb": kb} for t, kb in
                                    (render_offer_post(r) for r in posteadas)]
        return stats

    now = datetime.now(timezone.utc).isoformat()
    for r in posteadas:
        try:
            text, kb = render_offer_post(r)
            payload: dict = {
                "chat_id": int(cfg.channel.chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True}
            if kb:
                payload["reply_markup"] = kb
            resp = tg_api("sendMessage", payload)
            if resp.get("ok"):
                conn.execute("UPDATE ofertas SET notified_channel_at=? WHERE group_id=?",
                             (now, r["group_id"]))
                # bucket = group_id para kind='offer' (el índice único (kind,bucket) exige
                # unicidad; para digests el bucket es la fecha/semana/mes)
                conn.execute("""INSERT INTO channel_posts (message_id, group_id, kind, bucket,
                    body_hash, posted_at) VALUES (?, ?, 'offer', ?, '', ?)""",
                             (resp["result"]["message_id"], r["group_id"], r["group_id"], now))
                # commit POR POST (C3): crash entre send y commit no pierde marcas
                conn.commit()
                stats["posted"] += 1
                import time
                time.sleep(cfg.channel.sleep_s)
            else:
                log.warning("canal: sendMessage sin ok → %s", str(resp)[:120])
        except Exception as e:
            log.warning("canal: post falló (%.40s): %s", r.get("title") or "", e)
    log.info("canal: %d/%d publicadas (dev-skip %d, sobrantes por tope %d)",
             stats["posted"], stats["candidates"], stats["skipped_dev"],
             stats["candidates"] - stats["skipped_dev"] - stats["posted"])
    return stats


# ---------------- digests (B/C/D/E — spec v3 §5) ----------------

def _bucket(kind: str, now: datetime) -> str:
    if kind == "daily":
        return now.date().isoformat()
    if kind in ("weekly-remote", "weekly-salary"):
        iso = now.date().isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if kind == "trends":
        return now.date().strftime("%Y-%m")
    return now.date().isoformat()


def _send_digest(cfg: Config, tg_api, kind: str, text: str, conn, now: datetime,
                 chat_id: int | None = None, force: bool = False) -> bool:
    """Envía un digest con idempotencia (kind, bucket) + guard body_hash. True si envió.

    chat_id: destino override (DM de prueba) — SIN idempotencia: cada ejecución
    envía (el operador puede repetir el comando cuantas veces quiera) y no
    registra nada en channel_posts (no ensucia el historial del canal).
    force=True (comando manual /channel_*): envía SIEMPRE, sin idempotencia
    ni registro — el operador pidió explícitamente el envío. El daemon
    automático (force=False, sin chat_id) conserva la idempotencia por bucket.
    """
    if chat_id is not None or force:
        cid = int(chat_id) if chat_id is not None else int(cfg.channel.chat_id)
        try:
            resp = tg_api("sendMessage", {
                "chat_id": cid, "text": text,
                "parse_mode": "HTML", "disable_web_page_preview": True})
            return bool(resp.get("ok"))
        except Exception as e:
            log.warning("canal: digest %s (manual/DM) falló: %s", kind, e)
            return False
    bucket = _bucket(kind, now)
    body_hash = hashlib.sha1(text.encode()).hexdigest()
    try:
        conn.execute("INSERT INTO channel_posts (kind, bucket, body_hash, posted_at) VALUES (?,?,?,?)",
                     (kind, bucket, body_hash, now.isoformat()))
    except Exception:
        return False  # (kind, bucket) ya existe → doble envío bloqueado
    prev = conn.execute(
        "SELECT body_hash FROM channel_posts WHERE kind=? AND bucket<>? ORDER BY id DESC LIMIT 1",
        (kind, bucket)).fetchone()
    if prev and prev["body_hash"] == body_hash and cfg.channel.digest_daily:
        # pool quieto → digest idéntico al anterior → skip (el INSERT de arriba se revierte abajo)
        conn.rollback()
        return False
    if dry_ok := True:
        pass
    try:
        resp = tg_api("sendMessage", {
            "chat_id": int(cfg.channel.chat_id), "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True})
        if not resp.get("ok"):
            conn.rollback()
            return False
    except Exception as e:
        log.warning("canal: digest %s falló: %s", kind, e)
        conn.rollback()
        return False
    # actualizar message_id del registro de bucket
    conn.execute("""UPDATE channel_posts SET message_id=? WHERE kind=? AND bucket=?""",
                 (resp["result"]["message_id"], kind, bucket))
    conn.commit()
    log.info("canal: digest %s enviado (bucket %s)", kind, bucket)
    return True


def _top_row_line(r: dict) -> str:
    from .notify import esc
    from .scoring import _salary_to_clp_monthly
    sal = _salary_to_clp_monthly(r.get("salary") or "", r.get("description") or "")
    s = f"🎯 [<b>{r.get('market_score') or 0}</b>] {esc((r.get('title') or '')[:80])}"
    if r.get("company"):
        s += f"\n   🏢 {esc(r['company'][:35])}" + (f" · 📍 {esc(r['modality'][:20])}" if r.get("modality") else "")
    if sal:
        s += f"\n   💰 ${sal:,}".replace(",", ".")
    op = (r.get("ai_opinion") or "").strip()
    if op:
        s += f"\n   💡 {esc(op[:140])}"
    if r.get("url"):
        s += f"\n   🔗 {esc(r['url'])}"
    return s


def _enviar_fotos(cfg: Config, tg_api, fotos: list[tuple[Path, str]],
                  chat_id: int | None = None) -> None:
    """Envía PNGs con caption (complemento visual de un digest).

    fotos: [(path, caption_html), ...] — falla silenciosa por foto (no tumba el digest).
    chat_id: destino override (DM de prueba); None → canal configurado.
    """
    from pathlib import Path
    cid = int(chat_id) if chat_id is not None else int(cfg.channel.chat_id)
    for p, caption in fotos:
        try:
            tg_api("sendPhoto", {"chat_id": cid, "path": str(p),
                                 "caption": caption, "parse_mode": "HTML"})
        except Exception as e:
            log.warning("canal: foto %s falló: %s", Path(p).name, e)


def _dir_charts(cfg: Config) -> Path:
    from pathlib import Path
    d = Path(cfg.report.out_dir) / "digest_charts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def publish_daily_digest(cfg: Config, conn, tg_api, dry_run: bool = False,
                         chat_id: int | None = None, force: bool = False) -> bool:
    """Digest B: top del día — 1 oferta por categoría (rol_categoria), la mejor de cada una.

    A1: diversifica por rol (Backend, Data, DevOps, ...) en vez de top-N por score
    (que terminaba siendo N ofertas del mismo rol). Umbral digest_min_score.
    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    now = datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute(_GATE_SQL.replace(":min_score", ":min_score").replace(
        ":max_age", ":max_age"), {"min_score": cfg.channel.digest_min_score,
                                  "max_age": cfg.channel.max_age_days}).fetchall()]
    # 1 por rol_categoria (mejor score de cada uno), excluye no-dev y posteadas 24h
    por_rol: dict[str, dict] = {}
    for r in rows:
        if cfg.channel.require_dev and not is_dev(r.get("rol_categoria"), r.get("title") or "", cfg,
                                                  r.get("description") or ""):
            continue
        rol = (r.get("rol_categoria") or "Otro").strip() or "Otro"
        cur = por_rol.get(rol)
        if cur is None or (r.get("market_score") or 0) > (cur.get("market_score") or 0):
            por_rol[rol] = r
    out = sorted(por_rol.values(), key=lambda r: -(r.get("market_score") or 0))
    if not out:
        log.info("canal: digest diario sin candidatas — silencio honesto")
        return False
    header = f"📊 <b>Top del día</b> · mejor de cada categoría · {now.strftime('%d %b')}"
    text = header + "\n\n" + "\n\n".join(_top_row_line(r) for r in out)
    if dry_run:
        log.info("canal: dry-run digest diario:\n%s", text[:500])
        return False
    ok = _send_digest(cfg, tg_api, "daily", text, conn, now, chat_id=chat_id, force=force)
    if ok:
        # B1+B2a+B2b+B4: gráficos de contexto (falla silenciosa por foto)
        try:
            from . import charts
            d = _dir_charts(cfg)
            fotos = []
            for p, cap in (
                (charts.chart_salarios_por_rol(conn, d), "💰 Mediana salarial por rol"),
                (charts.chart_ofertas_por_rol(conn, d), "🧑‍💻 Ofertas activas por rol"),
                (charts.chart_seniority_mix(conn, d), "📈 Seniority del pool"),
                (charts.chart_modalidad(conn, d), "🏢 Modalidad del pool"),
            ):
                if p:
                    fotos.append((p, cap))
            _enviar_fotos(cfg, tg_api, fotos, chat_id=chat_id)
        except Exception as e:
            log.warning("canal: gráficos daily fallaron: %s", e)
    return ok


def _weekly_remote_rows(conn, cfg: Config) -> dict[str, list[dict]]:
    rows = [dict(r) for r in conn.execute("""SELECT * FROM ofertas WHERE active=1
        AND market_score >= :min_score AND date_canonical >= date('now', '-' || :max_age || ' days')
        AND (modality LIKE '%remot%' OR remote_official LIKE '%TELECOMMUTE%')
        AND seniority_real != '' AND notified_channel_at = ''
        ORDER BY market_score DESC, first_seen DESC LIMIT 60""",
        {"min_score": cfg.channel.min_score - 5, "max_age": cfg.channel.max_age_days}).fetchall()]
    featured = set()
    try:
        featured = {r["group_id"] for r in conn.execute("""SELECT group_id FROM channel_posts
            WHERE kind IN ('daily','weekly-remote','weekly-salary') AND group_id != ''
            AND posted_at >= datetime('now', '-28 days')""").fetchall()}
    except Exception:
        pass
    by_sen: dict[str, list[dict]] = {}
    for r in rows:
        sen = r["seniority_real"]
        if r["group_id"] in featured or len(by_sen.get(sen, [])) >= 3:
            continue
        by_sen.setdefault(sen, []).append(r)
    return by_sen


def publish_weekly_remote(cfg: Config, conn, tg_api, dry_run: bool = False,
                          chat_id: int | None = None, force: bool = False) -> bool:
    """Digest C: mejor remoto de la semana × seniority (junior/semi/senior/lead).

    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    from .notify import esc
    now = datetime.now(timezone.utc)
    by_sen = _weekly_remote_rows(conn, cfg)
    sections = []
    for sen, label in (("junior", "🟢 Junior"), ("semi", "🟡 Semi"),
                       ("senior", "🟠 Senior"), ("lead", "🔴 Lead")):
        items = by_sen.get(sen)
        if not items:
            continue
        body = "\n\n".join(_top_row_line(r) for r in items)
        sections.append(f"{label}\n{body}")
    if not sections:
        log.info("canal: weekly-remote sin candidatas — silencio honesto")
        return False
    text = "🌍 <b>Mejor remoto de la semana</b> · por seniority\n\n" + "\n\n".join(sections)
    if dry_run:
        log.info("canal: dry-run weekly-remote:\n%s", text[:400])
        return False
    return _send_digest(cfg, tg_api, "weekly-remote", text, conn, now, chat_id=chat_id, force=force)


def publish_weekly_rol(cfg: Config, conn, tg_api, dry_run: bool = False,
                       chat_id: int | None = None, force: bool = False) -> bool:
    """Digest C2: mejor oferta de la semana POR ROL (Backend, Data, DevOps, ...).

    A3: complementa weekly-remote (por seniority) con diversidad por categoría —
    1 oferta por rol_categoria, la de mejor market_score de cada una.
    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    from .notify import esc
    now = datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute("""SELECT * FROM ofertas WHERE active=1
        AND market_score >= :min_score AND date_canonical >= date('now', '-' || :max_age || ' days')
        AND notified_channel_at = ''
        ORDER BY market_score DESC, first_seen DESC LIMIT 80""",
        {"min_score": cfg.channel.min_score - 5, "max_age": cfg.channel.max_age_days}).fetchall()]
    por_rol: dict[str, dict] = {}
    for r in rows:
        if cfg.channel.require_dev and not is_dev(r.get("rol_categoria"), r.get("title") or "", cfg,
                                                  r.get("description") or ""):
            continue
        rol = (r.get("rol_categoria") or "Otro").strip() or "Otro"
        cur = por_rol.get(rol)
        if cur is None or (r.get("market_score") or 0) > (cur.get("market_score") or 0):
            por_rol[rol] = r
    out = sorted(por_rol.values(), key=lambda r: -(r.get("market_score") or 0))
    if not out:
        log.info("canal: weekly-rol sin candidatas — silencio honesto")
        return False
    text = "🏆 <b>Mejor de la semana por rol</b>\n\n" + "\n\n".join(_top_row_line(r) for r in out)
    if dry_run:
        log.info("canal: dry-run weekly-rol:\n%s", text[:400])
        return False
    ok = _send_digest(cfg, tg_api, "weekly-rol", text, conn, now, chat_id=chat_id, force=force)
    if ok:
        try:
            from . import charts
            d = _dir_charts(cfg)
            p = charts.chart_actividad(conn, d)
            if p:
                _enviar_fotos(cfg, tg_api, [(p, "📅 Ofertas nuevas por día")], chat_id=chat_id)
        except Exception as e:
            log.warning("canal: gráfico weekly-rol falló: %s", e)
    return ok


def publish_weekly_salary(cfg: Config, conn, tg_api, dry_run: bool = False,
                          chat_id: int | None = None, force: bool = False) -> bool:
    """Digest D: ranking salarial semanal (top 5 declarados) CON contexto de mediana por rol.

    A2: cada línea compara contra la mediana del mismo rol (stats robustas de
    stats.py) — '$2.8M (mediana DevOps $2.1M, +33%)' en vez de un número plano.
    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    from .notify import esc
    from .scoring import _salary_to_clp_monthly
    from .stats import _median as mediana_robusta
    now = datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute("""SELECT * FROM ofertas WHERE active=1
        AND market_score >= :min_score AND date_canonical >= date('now', '-' || :max_age || ' days')
        AND salary != '' AND notified_channel_at = ''
        ORDER BY market_score DESC, first_seen DESC LIMIT 60""",
        {"min_score": cfg.channel.min_score - 5, "max_age": cfg.channel.max_age_days}).fetchall()]
    sal_rows = []
    for r in rows:
        sal = _salary_to_clp_monthly(r.get("salary") or "", r.get("description") or "")
        if sal:
            sal_rows.append((sal, r))
    sal_rows.sort(key=lambda x: -x[0])
    if not sal_rows:
        log.info("canal: weekly-salary sin candidatas — silencio honesto")
        return False
    # mediana por rol (pool activo completo, robusta)
    # mediana por rol (pool activo completo, robusta)
    _acum: dict = {}
    for rc, sal, desc in conn.execute(
            "SELECT rol_categoria, salary, description FROM ofertas WHERE active=1 AND salary != ''"):
        v = _salary_to_clp_monthly(sal or "", desc or "")
        if v:
            _acum.setdefault(rc or "Otro", []).append(v)
    med_por_rol = {k: mediana_robusta(v) for k, v in _acum.items()}
    lines = []
    for i, (sal, r) in enumerate(sal_rows[:5], 1):
        rol = (r.get("rol_categoria") or "Otro").strip() or "Otro"
        med = med_por_rol.get(rol)
        ctx = ""
        if med:
            diff = (sal - med) * 100 // med
            signo = "+" if diff >= 0 else ""
            ctx = f" · mediana {rol}: ${med:,} ({signo}{diff}%)".replace(",", ".")
        lines.append(f"{i}. ${sal:,}".replace(",", ".") +
                     f" — {esc(r['title'][:60])} ({esc((r.get('company') or '?')[:25])}){ctx}")
    text = "💰 <b>Top salarios de la semana</b> (vs mediana del rol)\n" + "\n".join(lines)
    if dry_run:
        log.info("canal: dry-run weekly-salary:\n%s", text[:400])
        return False
    ok = _send_digest(cfg, tg_api, "weekly-salary", text, conn, now, chat_id=chat_id, force=force)
    if ok:
        try:
            from . import charts
            d = _dir_charts(cfg)
            p = charts.chart_salarios_por_rol(conn, d)
            if p:
                _enviar_fotos(cfg, tg_api, [(p, "💰 Mediana salarial por rol")], chat_id=chat_id)
        except Exception as e:
            log.warning("canal: gráfico weekly-salary falló: %s", e)
    return ok


def publish_weekly_techs(cfg: Config, conn, tg_api, dry_run: bool = False,
                         chat_id: int | None = None, force: bool = False) -> bool:
    """Digest F: tecnologías del mercado — qué pide el mercado y qué paga.

    T1: top techs (columna + títulos, normalizadas) · T2: emergentes 7d vs mes
    anterior · T3: mediana salarial por tech. Texto con insights + 3 PNG.
    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    from .notify import esc
    from .scoring import _salary_to_clp_monthly
    from . import charts
    from collections import Counter
    now = datetime.now(timezone.utc)
    rows = charts._techs_pool(conn, 30)
    if not rows:
        log.info("canal: weekly-techs sin candidatas — silencio honesto")
        return False
    c = charts._frecuencia_techs(rows)
    top = c.most_common(8)
    # emergentes (7d vs 8-30d)
    from datetime import date, timedelta
    hoy = date.today()
    recientes, anteriores = Counter(), Counter()
    for r in rows:
        try:
            d = date.fromisoformat((r.get("date_canonical") or "")[:10])
        except Exception:
            continue
        techs = charts._techs_de_fila(r)
        if d >= hoy - timedelta(days=7):
            recientes.update(techs)
        elif d >= hoy - timedelta(days=30):
            anteriores.update(techs)
    emergentes = [(t, recientes[t], anteriores.get(t, 0)) for t in recientes
                  if recientes[t] >= 2 and recientes[t] > anteriores.get(t, 0)]
    emergentes.sort(key=lambda x: -x[1])
    # mediana salarial por tech
    por_tech: dict[str, list[int]] = {}
    for r in rows:
        v = _salary_to_clp_monthly(r.get("salary") or "", r.get("description") or "")
        if not v:
            continue
        for t in charts._techs_de_fila(r):
            por_tech.setdefault(t, []).append(v)
    sal_tech = []
    for t, vals in por_tech.items():
        if len(vals) >= 3:
            vals.sort()
            sal_tech.append((t, vals[len(vals) // 2], len(vals)))
    sal_tech.sort(key=lambda x: -x[1])

    lines = [f"🧰 <b>Mercado de tecnologías</b> · últimos 30 días · {now.strftime('%d %b')}"]
    if top:
        lines.append("\n🔥 <b>Más pedidas</b>")
        lines.append(" · ".join(f"{esc(t)} ({n})" for t, n in top))
    if emergentes:
        lines.append("\n🚀 <b>Emergentes</b> (7d vs mes anterior)")
        for t, n7, n30 in emergentes[:5]:
            x = n7 / max(1, n30)
            lines.append(f"  {esc(t)}: {n7} ofertas (x{x:.1f})")
    if sal_tech:
        lines.append("\n💰 <b>Mejor pagadas</b> (mediana, n≥3)")
        for t, med, n in sal_tech[:5]:
            lines.append(f"  {esc(t)}: ${med:,}".replace(",", ".") + f" (n={n})")
    text = "\n".join(lines)
    if dry_run:
        log.info("canal: dry-run weekly-techs:\n%s", text[:500])
        return False
    ok = _send_digest(cfg, tg_api, "weekly-techs", text, conn, now, chat_id=chat_id, force=force)
    if ok:
        try:
            d = _dir_charts(cfg)
            fotos = []
            for p, cap in (
                (charts.chart_techs_top(conn, d), "🔥 Tecnologías más pedidas (30d)"),
                (charts.chart_techs_emergentes(conn, d), "🚀 Emergentes — 7d vs mes anterior"),
                (charts.chart_techs_salario(conn, d), "💰 Mediana salarial por tecnología"),
            ):
                if p:
                    fotos.append((p, cap))
            _enviar_fotos(cfg, tg_api, fotos, chat_id=chat_id)
        except Exception as e:
            log.warning("canal: gráficos weekly-techs fallaron: %s", e)
    return ok


def publish_weekly_digests(cfg: Config, conn, tg_api, dry_run: bool = False) -> bool:
    """Wrapper compat: dispara C, C2, F y D (daemon semanal)."""
    sent = publish_weekly_remote(cfg, conn, tg_api, dry_run=dry_run)
    sent = publish_weekly_rol(cfg, conn, tg_api, dry_run=dry_run) or sent
    sent = publish_weekly_techs(cfg, conn, tg_api, dry_run=dry_run) or sent
    return publish_weekly_salary(cfg, conn, tg_api, dry_run=dry_run) or sent


def publish_trends(cfg: Config, conn, tg_api, dry_run: bool = False,
                   chat_id: int | None = None, force: bool = False) -> bool:
    """Digest E: tendencias mensuales (SQL puro; IA opcional con fallback).

    chat_id: destino override (DM de prueba); None → canal.
    force: envía siempre (comando manual), sin idempotencia.
    """
    from .notify import esc
    now = datetime.now(timezone.utc)
    rows = [dict(r) for r in conn.execute("""SELECT title, company, techs, seniority_real,
        rol_categoria, salary FROM ofertas
        WHERE active=1 AND date_canonical >= date('now', '-30 days') LIMIT 1500""").fetchall()]
    if not rows:
        return False
    from collections import Counter
    comp = Counter((r.get("company") or "").strip() for r in rows
                   if r.get("company") and not re.search(r"importante|confidencial", r["company"], re.I))
    top_emp = [f"{c} ({n})" for c, n in comp.most_common(5) if n >= 2]
    def techs_pct(subset):
        c = Counter()
        for r in subset:
            for t in (r.get("techs") or "").split(";"):
                t = t.strip()
                if t:
                    c[t] += 1
        tot = sum(c.values()) or 1
        return " · ".join(f"{t} {100*n//tot}%" for t, n in c.most_common(5))
    data = Counter(r.get("rol_categoria") or "" for r in rows)
    pct_dev = 100 * sum(v for k, v in data.items() if k in _DEV_CATEGORIES) // max(1, len(rows))
    pct_sal = 100 * sum(1 for r in rows if r.get("salary")) // max(1, len(rows))
    lines = [f"📈 <b>Mercado tech · últimos 30 días</b>"]
    if top_emp:
        lines.append("🏢 Más activas: " + esc(" · ".join(top_emp[:4])))
    for sen in ("Data", "Senior", "Junior"):
        sub = [r for r in rows if (sen.lower() in (r.get("seniority_real") or "") or
                                   (sen == "Data" and (r.get("rol_categoria") or "") == "Data"))]
        tp = techs_pct(sub)
        if tp:
            lines.append(f"🧰 {sen}: {esc(tp)}")
    lines.append(f"✅ {pct_dev}% del pool es dev · {pct_sal}% declara salario")
    text = "\n".join(lines)
    # IA opcional: 2-3 bullets de interpretación (fallback: bloque numérico igual)
    try:
        from .market import _ia_call
        out = _ia_call(cfg, "En 2 bullets de máx 15 palabras, interpreta estos datos del mercado "
                             "dev chileno (sin inventar cifras):\n" + text, temperature=0.3)
        if out and isinstance(out, dict) and out.get("bullets"):
            text += "\n\n" + "\n".join(f"• {esc(str(b)[:90])}" for b in out["bullets"][:2])
    except Exception as e:
        log.info("canal: trends sin IA (%s) — fallback determinístico", str(e)[:60])
    if dry_run:
        log.info("canal: dry-run trends:\n%s", text[:500])
        return False
    return _send_digest(cfg, tg_api, "trends", text, conn, now, chat_id=chat_id, force=force)


def channel_wipe(cfg: Config, conn, tg_api, dry_run: bool = False) -> dict:
    """Borra TODOS los mensajes publicados en el canal (usa channel_posts.message_id).

    Telegram no tiene bulk-delete: 1 deleteMessage por post, sleep 0.3s (rate limit).
    Los posts cuyo delete falla (borrados a mano, >48h en algunos casos) se ignoran.
    Limpia channel_posts y resetea notified_channel_at de las activas → el canal
    queda en cero y el próximo publish arranca limpio.
    """
    stats = {"total": 0, "deleted": 0, "skipped": 0}
    if not cfg.channel.enabled or not cfg.channel.chat_id:
        return stats
    rows = [dict(r) for r in conn.execute(
        "SELECT id, message_id FROM channel_posts WHERE message_id IS NOT NULL ORDER BY id")]
    stats["total"] = len(rows)
    if dry_run:
        stats["preview"] = [r["message_id"] for r in rows]
        return stats
    import time as _t
    for r in rows:
        try:
            resp = tg_api("deleteMessage", {
                "chat_id": int(cfg.channel.chat_id), "message_id": r["message_id"]})
            if resp.get("ok"):
                stats["deleted"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            stats["skipped"] += 1
        _t.sleep(0.3)
    conn.execute("DELETE FROM channel_posts")
    conn.execute("UPDATE ofertas SET notified_channel_at='' WHERE active=1")
    conn.commit()
    log.info("canal wipe: %d/%d borrados, %d skipped", stats["deleted"], stats["total"], stats["skipped"])
    return stats


def channel_status(conn, cfg: Config) -> str:
    """Observabilidad (H10): estado del canal para el comando admin /channel (al GRUPO)."""
    from .notify import esc
    from collections import Counter as _C
    ch = cfg.channel
    if not ch.enabled or not ch.chat_id:
        return "📢 Canal: DESACTIVADO (sin TELEGRAM_CHANNEL_ID)"
    last = conn.execute("""SELECT posted_at, kind FROM channel_posts ORDER BY id DESC LIMIT 1""").fetchone()
    cola = conn.execute(_GATE_SQL, {"min_score": ch.min_score, "max_age": ch.max_age_days}).fetchall()
    dev_ok = [r for r in cola if is_dev(r["rol_categoria"], r["title"], cfg, r.get("description") or "")]
    dist = Counter()
    for (ms,) in conn.execute("SELECT market_score FROM ofertas WHERE active=1"):
        dist[(ms or 0) // 10 * 10] += 1
    reposts = conn.execute("""SELECT COUNT(*) FROM channel_posts p1 JOIN channel_posts p2
        ON p1.id < p2.id AND p1.kind='offer' AND p2.kind='offer'
        AND p1.posted_at >= date('now','-14 days') AND p2.posted_at >= date('now','-14 days')
        WHERE p1.group_id IN (SELECT o1.group_id FROM ofertas o1 WHERE o1.title = (SELECT o2.title FROM ofertas o2 WHERE o2.group_id = p2.group_id))
        AND p1.group_id != p2.group_id""").fetchone()[0]
    lines = [f"📢 <b>Canal</b> — {'ON' if ch.enabled else 'OFF'} · {esc(str(ch.chat_id))}",
             f"🎯 Umbral {ch.min_score} · ventana {ch.max_age_days}d · dev-gate {'ON' if ch.require_dev else 'OFF'}",
             f"📤 Última publicación: {esc(last['posted_at'][:16] if last else 'nunca')} ({last['kind'] if last else '-'})",
             f"⏳ En cola: {len(dev_ok)} dev de {len(cola)} candidatas",
             f"📈 market_score pool: " + " · ".join(f"{k}s:{v}" for k, v in sorted(dist.items())),
             f"🔁 Posibles republicaciones (14d): {reposts} (informativo)"]
    return "\n".join(lines)