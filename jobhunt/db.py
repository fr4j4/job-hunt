"""SQLite: esquema, versionado de criterios y rescore.

Datos capturados (objetivos) v/s derivados (score) están separados:
- Cambiar el criterio de match → INSERT en score_versions + rescore_all()
- Las descripciones/datos nunca se re-scrapean por cambio de criterio
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def connect(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(cfg.db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # lecturas concurrentes sin lock
    conn.execute("PRAGMA busy_timeout=60000")  # barrido + batch IA concurrentes esperan, no mueren
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS ofertas (
        group_id    TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        company     TEXT DEFAULT '',
        location    TEXT DEFAULT '',
        url         TEXT DEFAULT '',
        source      TEXT DEFAULT '',
        sources     TEXT DEFAULT '',
        found_by    TEXT DEFAULT '',
        date_posted TEXT DEFAULT '',
        valid_through TEXT DEFAULT '',
        employment_type TEXT DEFAULT '',
        industry    TEXT DEFAULT '',
        education   TEXT DEFAULT '',
        applicant_region TEXT DEFAULT '',
        years_official INTEGER,
        remote_official INTEGER DEFAULT 0,
        salary      TEXT DEFAULT '',
        modality    TEXT DEFAULT '',
        techs       TEXT DEFAULT '',
        description TEXT DEFAULT '',
        description_source TEXT DEFAULT '',
        score       INTEGER DEFAULT 0,
        score_version TEXT DEFAULT '',
        staffing    INTEGER DEFAULT 0,
        seniority_real TEXT DEFAULT '',
        ai_resumen TEXT DEFAULT '',
        ai_red_flags TEXT DEFAULT '',
        ai_green_flags TEXT DEFAULT '',
        ai_benefits TEXT DEFAULT '',
        ai_fit_reason TEXT DEFAULT '',
        ai_ingles   TEXT DEFAULT '',
        ia_model    TEXT DEFAULT '',
        ia_fields   TEXT DEFAULT '',
        first_seen  TEXT NOT NULL,
        last_seen   TEXT NOT NULL,
        occurrences INTEGER DEFAULT 1,
        active      INTEGER DEFAULT 1
    )""")
    # migraciones ligeras: columnas añadidas post-v1
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ofertas)")}
    for col, ddl in [
        ("ai_idiomas", "ALTER TABLE ofertas ADD COLUMN ai_idiomas TEXT DEFAULT ''"),
    ]:
        if col not in cols:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_score ON ofertas(score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ofertas_active ON ofertas(active)")
    conn.execute("""CREATE TABLE IF NOT EXISTS score_versions (
        version_id  TEXT PRIMARY KEY,
        criteria    TEXT NOT NULL,          -- snapshot del bloque scoring/profile del .env
        created_at  TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        total_seen INTEGER, new_count INTEGER,
        sources_summary TEXT DEFAULT ''
    )""")
    conn.commit()


def register_criteria_version(conn: sqlite3.Connection, version_id: str, cfg: Config) -> None:
    """Guarda snapshot del criterio vigente (profile + scoring del .env)."""
    criteria = json.dumps({
        "profile": {
            "title": cfg.profile.title,
            "years_exp": cfg.profile.years_exp,
            "techs": cfg.profile.techs,
            "roles": cfg.profile.roles,
            "salary_min": cfg.profile.salary_min,
            "salary_max": cfg.profile.salary_max,
            "red_keywords": cfg.profile.red_keywords,
        },
        "scoring": {
            "base": cfg.scoring.base,
            "n_tech_primary": cfg.scoring.n_tech_primary,
            "tech_primary": cfg.scoring.tech_primary,
            "tech_secondary": cfg.scoring.tech_secondary,
            "role_keywords": cfg.scoring.role_keywords,
            "modality_bonus": cfg.scoring.modality_bonus,
            "english_excluyente": cfg.scoring.english_excluyente,
        },
    }, ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute("""INSERT INTO score_versions (version_id, criteria, created_at)
                    VALUES (?,?,?)
                    ON CONFLICT(version_id) DO UPDATE SET criteria=excluded.criteria""",
                 (version_id, criteria, now))


def current_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT version_id FROM score_versions ORDER BY created_at DESC LIMIT 1").fetchone()
    return row["version_id"] if row else None


def needs_rescore(conn: sqlite3.Connection, version_id: str) -> int:
    """Cantidad de ofertas cuyo score no corresponde a la versión vigente."""
    return conn.execute(
        "SELECT COUNT(*) FROM ofertas WHERE active=1 AND (score_version != ? OR score_version IS NULL)",
        (version_id,)).fetchone()[0]


def rescore_all(conn: sqlite3.Connection, score_fn, version_id: str, cfg: Config) -> int:
    """Recalcula el score de TODAS las ofertas activas con el criterio vigente.

    score_fn(job_dict, cfg) → (score, breakdown).  Puro cómputo local.
    """
    rows = conn.execute("SELECT * FROM ofertas WHERE active=1").fetchall()
    updated = 0
    for r in rows:
        job = dict(r)
        score, _breakdown = score_fn(job, cfg)
        conn.execute("UPDATE ofertas SET score=?, score_version=? WHERE group_id=?",
                     (score, version_id, r["group_id"]))
        updated += 1
    conn.commit()
    return updated


def upsert(conn: sqlite3.Connection, job: dict, now_iso: str) -> tuple[str, bool]:
    """Inserta o fusiona. job debe traer uid (o lo genera). Retorna (group_id, is_new)."""
    if not job.get("uid"):
        job["uid"] = re_norm_uid(job["title"], job.get("company", ""))
    gid = db_find_duplicate(conn, job)
    if gid:
        src0 = (job.get("source") or "").split(":")[0]
        fb = job.get("found_by", "")
        conn.execute("""UPDATE ofertas SET
            last_seen=?, occurrences=occurrences+1,
            sources=CASE WHEN instr(sources, ?) THEN sources ELSE
                     CASE WHEN sources='' THEN ? ELSE sources || ',' || ? END END,
            found_by=CASE WHEN instr(found_by, ?) THEN found_by ELSE
                   CASE WHEN found_by='' THEN ? ELSE found_by || ',' || ? END END,
            salary=CASE WHEN salary='' OR salary IS NULL THEN ? ELSE salary END,
            modality=CASE WHEN modality='' OR modality IS NULL THEN ? ELSE modality END,
            description=CASE WHEN length(description) < 200 THEN ? ELSE description END,
            valid_through=COALESCE(NULLIF(valid_through,''), ?),
            years_official=COALESCE(years_official, ?),
            remote_official=COALESCE(remote_official, ?),
            employment_type=CASE WHEN employment_type='' THEN ? ELSE employment_type END,
            active=1
            WHERE group_id=?""",
            (now_iso, src0, src0, src0,
             fb, fb, fb,
             job.get("salary", ""), job.get("modality", ""),
             (job.get("_desc") or job.get("description") or "")[:2000],
             job.get("valid_through", ""), job.get("years_official"),
             1 if job.get("remote_official") else None,
             job.get("employment_type", ""),
             gid))
        return gid, False
    try:
        conn.execute("""INSERT INTO ofertas
            (group_id, title, company, location, url, source, sources, found_by,
             date_posted, valid_through, employment_type, years_official, remote_official,
             salary, modality, techs, description, description_source,
             first_seen, last_seen, occurrences, active, score_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,'')""",
            (job["uid"], job["title"], job.get("company", ""), job.get("location", ""),
             job.get("url", ""), job.get("source", ""),
             (job.get("source") or "").split(":")[0], job.get("found_by", ""),
             job.get("date", ""), job.get("valid_through", ""), job.get("employment_type", ""),
             job.get("years_official"), job.get("remote_official"),
             job.get("salary", ""), job.get("modality", ""), job.get("techs", ""),
             (job.get("_desc") or "")[:2000], job.get("description_source", ""),
             now_iso, now_iso))
        return job["uid"], True
    except sqlite3.IntegrityError:
        conn.execute("""UPDATE ofertas SET last_seen=?, occurrences=occurrences+1 WHERE group_id=?""",
                     (now_iso, job["uid"]))
        return job["uid"], False


def db_find_duplicate(conn: sqlite3.Connection, job: dict) -> str | None:
    from .dedup import find_duplicate
    return find_duplicate(conn, job)


def re_norm_uid(title: str, company: str) -> str:
    import re as _re
    return _re.sub(r"\s+", " ", title).strip().lower()[:80] + "|" + company[:30]


# ---- dedup helpers (migrados de buscador_v2) ----
import re as _re
import unicodedata as _unicodedata
from difflib import SequenceMatcher


def _norm_text(s: str) -> str:
    s = _unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not _unicodedata.combining(c)).lower()
    return s


_FILLER = _re.compile(
    r"\b(oferta de trabajo de|empleo de|buscamos|se busca|full[- ]?time|part[- ]?time|"
    r"remoto|remota|hibrido|híbrida|hybrid|presencial|ref[#]?\s?\w+|\d+\s*d[ií]as?|"
    r"proyecto|híbrido|jornada|cupo|fijo)\b", _re.IGNORECASE)
_SYN = {"developer": "desarrollador", "engineer": "ingeniero"}


def norm_title(t: str) -> str:
    t = _norm_text(t)
    for tech in ["typescript", "javascript", "java", "angular", "react", "node", "python", "vue"]:
        t = _re.sub(rf"([a-z])({tech})", rf"\1 {tech} \2", t)
        t = _re.sub(rf"({tech})([a-z])", rf"{tech} \2", t)
    t = _FILLER.sub(" ", t)
    t = _re.sub(r"[^a-z0-9+#. ]", " ", t)
    return " ".join(_SYN.get(w, w) for w in t.split())


def norm_company(c: str) -> str:
    c = _norm_text(c)
    c = _re.sub(r"\b(s\.?a\.?|spa|ltda|limitada|inc|llc|gmbh|grupo|importante empresa(\s\w+)?|confidencial)\b", "", c)
    return _re.sub(r"[^a-z0-9]", "", _re.sub(r"\s+", " ", c))


_GENERIC = {"", "confidencial", "empresaconfidencial", "importanteempresa"}


def companies_match(c1: str, c2: str) -> str:
    n1, n2 = norm_company(c1), norm_company(c2)
    s1, s2 = n1.replace(" ", ""), n2.replace(" ", "")
    if n1 in _GENERIC or n2 in _GENERIC:
        return "weak"
    if n1 == n2 or (s1 and s1 == s2):
        return "strong"
    if n1 and n2 and (n1 in n2 or n2 in n1 or s1 in s2 or s2 in s1):
        return "strong"
    return "different"


def similar(t1: str, t2: str) -> bool:
    n1, n2 = norm_title(t1), norm_title(t2)
    if not n1 or not n2:
        return False
    if n1 == n2 or SequenceMatcher(None, n1, n2).ratio() >= 0.86:
        return True
    a, b = set(n1.split()), set(n2.split())
    return bool(a and b) and len(a & b) / len(a | b) >= 0.55


def url_key(url: str) -> str:
    if not url:
        return ""
    import urllib.parse as _up
    u = url.split("#")[0].rstrip("/")
    parsed = _up.urlsplit(u)
    q = _up.parse_qs(parsed.query)
    keep = []
    for k in ("jk", "jl"):
        if k in q:
            keep.append(f"{k}={q[k][0]}")
    if keep:
        return parsed.path + "?" + "&".join(keep)
    return parsed.path