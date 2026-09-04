"""Motor de scoring paramétrico.

El score de compatibilidad se calcula contra el Profile del .env usando los
pesos de Scoring. El criterio es dato (versionado en la BD), no código:
cambiar el .env y correr `jobhunt rescore` re-evalúa todo el pool en <1s.

score = f(profile, scoring, captured_fields)
  - capturado: title, company, location, description, salary, modality,
    techs, date_posted, + campos JSON-LD (years_official, remote_official)
  - derivado: TODO lo demás
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .config import Config, Profile, Scoring


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _years_from_description(desc: str) -> int | None:
    m = re.search(r"(\d{1,2})\+?\s*(?:años|anos|years)", _norm(desc))
    return int(m.group(1)) if m else None


def _english_level(desc: str, title: str) -> str:
    """no | deseable | excluyente | '' (no menciona)"""
    t = _norm(title + " " + desc)
    if not re.search(r"english|ingl", t):
        return ""
    if re.search(r"no (?:se )?requiere ingl|english not required|sin ingl", t):
        # "no requiere" gana solo si no menciona nivel
        if not re.search(r"avanzado|advanced|intermedio|intermediate|bilingu|bilingual|fluent|\bb2\b|c1|c2", t):
            return "no"
    if re.search(r"excluyente|obligatorio|required|excellent|advanced|bilingu|bilingual|fluent|full professional|c1|c2", t):
        return "excluyente"
    if re.search(r"deseable|plus|nice to have|intermedio|intermediate|\bb2\b", t):
        return "deseable"
    return "menciona"


def _us_hours(desc: str) -> bool:
    return bool(re.search(r"\bpst\b|\best\b hours|\bcst\b hours|us business|us hours", _norm(desc)))


def _staffing(job: dict) -> bool:
    title = _norm(job.get("title", ""))
    company = _norm(job.get("company", ""))
    if re.search(r"\bref[#]?\s?\w{4,}", title):
        return True
    if re.search(r"top 1%|talent|staffing|consultora de ti|outsourcing", title + " " + company):
        return True
    return False


def compute_score(job: dict, cfg: Config) -> tuple[int, dict]:
    """Calcula el score de compatibilidad de una oferta contra el perfil.

    Retorna (score, breakdown) — breakdown para auditar por qué salió ese número.
    Los pesos salen del .env; cambiar el criterio = cambiar .env + `jobhunt rescore`.
    """
    p: Profile = cfg.profile
    s: Scoring = cfg.scoring
    t_title = _norm(job.get("title", ""))
    t_loc = _norm(job.get("location", ""))
    desc = job.get("description") or ""
    t_desc = _norm(desc)
    blob = t_title + " " + t_loc

    breakdown: dict[str, int | str] = {}
    score = s.base
    breakdown["base"] = s.base

    # ---- hard filters (red keywords del .env) ----
    ndesc = _norm(desc)
    for kw in p.red_keywords:
        if kw in blob or kw in ndesc:
            breakdown["rejected_by"] = kw
            return 0, breakdown

    # ---- techs (primarias vs secundarias) ----
    primary = [t.replace(" ", "") for t in p.techs[: s.n_tech_primary]]
    techs_hit = []
    for i, tech in enumerate(p.techs):
        pat = _norm(tech).replace(" ", "")
        if pat and pat in blob.replace(" ", ""):
            pts = s.tech_primary if i < s.n_tech_primary else s.tech_secondary
            score += pts
            techs_hit.append((tech, pts))
    if techs_hit:
        breakdown["techs"] = techs_hit

    # ---- roles en el título ----
    for kw, pts in s.role_keywords.items():
        if kw in t_title:
            score += pts
            breakdown[f"role:{kw}"] = pts

    # ---- experiencia (JSON-LD oficial > regex descripción) ----
    years = job.get("years_official") or _years_from_description(desc)
    if years is not None:
        if years <= p.years_exp and years >= 2:
            score += s.exp_match_bonus
            breakdown["exp"] = f"{years}y ok +{s.exp_match_bonus}"
        elif years >= s.exp_hard_threshold:
            score += s.exp_mismatch_hard
            breakdown["exp"] = f"{years}y excesivo {s.exp_mismatch_hard}"
        elif years > p.years_exp:
            score += s.exp_mismatch_soft
            breakdown["exp"] = f"{years}y soft {s.exp_mismatch_soft}"

    # ---- inglés ----
    lvl = _english_level(desc, job.get("title", ""))
    if lvl == "excluyente":
        score += s.english_excluyente
        breakdown["english"] = f"excluyente {s.english_excluyente}"
    elif lvl == "deseable":
        score += s.english_deseable
    elif lvl == "no":
        score += s.english_not_required
        breakdown["english"] = f"no requiere +{s.english_not_required}"

    # ---- horario USA ----
    if _us_hours(desc):
        score += s.us_hours_penalty
        breakdown["us_hours"] = s.us_hours_penalty

    # ---- modalidad ----
    modality = _norm(job.get("modality", ""))
    bonus = s.modality_bonus.get(modality, 0)
    if bonus:
        score += bonus
        breakdown[f"mod:{modality}"] = bonus

    # ---- salario declarado ----
    sal_raw = job.get("salary") or ""
    sal_clp = _salary_to_clp_monthly(sal_raw, desc)
    if sal_clp and p.salary_min:
        if p.salary_min <= sal_clp <= p.salary_max:
            score += s.salary_in_range_bonus
            breakdown[f"salary:{sal_clp}"] = f"+{s.salary_in_range_bonus}"
        elif not p.salary_flex:
            score += s.salary_out_soft
            breakdown["salary_out"] = s.salary_out_soft

    # ---- stack overlap (techs de la descripción vs tu stack) ----
    job_techs = [x for x in (job.get("techs") or "").split(";") if x]
    overlap = sum(1 for x in ("Py", "Java", "AWS", "Angular", "React", "K8s", "Docker", "Spring", "Postgres")
                  if x in job_techs and any(x.lower().startswith(t[:4]) for t in p.techs)) if job_techs else 0
    pts = min(overlap * s.stack_overlap_per, s.stack_overlap_max)
    if pts:
        score += pts
        breakdown["stack_overlap"] = pts

    # ---- green keywords ----
    for kw in p.green_keywords:
        if _norm(kw) in t_desc:
            score += 3
            breakdown[f"green:{kw}"] = 3

    # ---- staffing ----
    if _staffing(job):
        score += p.staffing_penalty
        breakdown["staffing"] = p.staffing_penalty

    # ---- locations ----
    for rej in p.locations_rejected:
        if rej in t_loc:
            breakdown["rejected_by"] = f"location:{rej}"
            return 0, breakdown

    final = max(0, min(int(score), s.cap))
    return final, breakdown



def _salary_to_clp_monthly(sal_raw: str, desc: str) -> int | None:
    """Parser ÚNICO de salario → CLP mensual (especificado en spec-canal-v3 §1.4).

    Formatos reales del pool (todos testeados):
      '$ 2.500.000,00 (Mensual)' → 2500000  (formato CB/Laborum)
      'CLP 2578680'              → 2578680  (formato que escribe la IA — NO es USD)
      'CLP 15000'                → None     (fuera de banda mensual plausible)
      'USD 4000' / '4000' / '4,000' → ×950  (heurística USD: números planos de 3-4 dígitos
                                   o con prefijo USD; 'CLP NNNNNNN' de 7 dígitos NO cae aquí)
      '' / None                  → None
    """
    import re
    raw = sal_raw or ""
    lower = raw.lower()
    m = re.search(r"([\d.,]+)", raw)
    if not m:
        # buscar en la descripción un monto con CLP
        md = re.search(r"\$\s?([\d.]{7,})", desc)
        if md:
            try:
                return int(md.group(1).replace(".", "").replace(",", ""))
            except ValueError:
                return None
        return None
    num_s = m.group(1)
    if "." in num_s and "," in num_s:
        num_s = num_s.replace(".", "").replace(",", ".")   # 2.500.000,00 → 2500000.00
    elif num_s.count(",") == 1 and re.fullmatch(r"\d{1,3},\d{3}", num_s):
        num_s = num_s.replace(",", "")                     # formato US 4,000 → 4000 (USD heurística)
    elif has_thousands(num_s):
        num_s = num_s.replace(".", "").replace(",", "")
    try:
        num = float(num_s)
    except ValueError:
        return None
    # heurística USD: prefijo explícito, o número plano de 3-4 dígitos (4,000/4000).
    # 'CLP NNNNNNN' (7 dígitos, prefijo CLP) es CLP — formato del batch IA. (P0 fix)
    is_usd = "usd" in lower or (re.fullmatch(r"[\d,]{3,4}", num_s) and "clp" not in lower)
    if num < 1000:
        return None
    if is_usd:
        clp = int(num * 950)
        return clp if 300_000 <= clp <= 20_000_000 else None   # banda mensual plausible
    clp = int(num)
    return clp if 300_000 <= clp <= 20_000_000 else None       # CLP 15000 → None (fuera de banda)


def has_thousands(s: str) -> bool:
    parts = s.split(".")
    return len(parts) > 1 and all(len(p) == 3 for p in parts[1:])


# ============ market score (objetivo, sin perfil) — spec-canal-v3 §1 ============

# Abreviaturas canónicas de la columna techs (Py;TS;K8s...) + nombres para títulos
MARKET_TECHS_ABBR = {"Py", "Java", "TS", "JS", "React", "Angular", "Node", "AWS", "GCP",
                     "Azure", "K8s", "Docker", "NiFi", "SQL", "Postgres", "Mongo", "Go",
                     ".NET", "Scala", "Spring", "CI/CD", "FastAPI", "Django", "Redis",
                     "Kafka", "TF", "Terraform"}
_MARKET_TECHS_TITLE_RE = re.compile(
    r"\bpython\b|\bjava\b|\bscala\b|\btypescript\b|\bjavascript\b|\bkubernetes\b|\bk8s\b|"
    r"\bdocker\b|\bnifi\b|\breact\b|\bangular\b|\bspring\b|\b\.net\b|\baws\b|\bgcp\b|"
    r"\bazure\b|\bgolang\b|\bgo\b(?=\s*(?:lang|developer|dev\b|engineer))|\bnode\b|"
    r"\bpostgres\b|\bgraphql\b", re.I)


def compute_market_score(job: dict, now=None) -> tuple[int, dict]:
    """Score objetivo 0-100 para el canal (comunidad), sin perfil personal.

    Componentes: salario 40 · modalidad 20 · transparencia 15 · stack 15 ·
    frescura 10 · descuentos staffing (recalculado en vivo, la col. DB está
    siempre 0) −10 y empresa genérica −5, clamps a ≥0 por subtotal.
    """
    from .channel import age_days  # import local: evita ciclo channel→scoring

    b: dict[str, int | str] = {}

    # ---- salario (40) ----
    sal = _salary_to_clp_monthly(job.get("salary") or "", job.get("description") or "")
    if sal is None:
        sal_pts = 5
        b["salario"] = "no declarado"
    elif sal >= 2_700_000:
        sal_pts = 40
    elif sal >= 1_900_000:
        sal_pts = 30
    elif sal >= 1_300_000:
        sal_pts = 15
    else:
        sal_pts = 5
    b["salario_pts"] = sal_pts

    # ---- modalidad (20) ----
    mod = _norm(job.get("modality") or "")
    if "remot" in mod:
        mod_pts = 20
    elif "híbrid" in mod or "hibrid" in mod:
        mod_pts = 10
    elif "presencial" in mod:
        mod_pts = 5
    else:
        mod_pts = 8
        b["modalidad"] = "sin dato"
    b["mod_pts"] = mod_pts

    # ---- transparencia (15, con clamp a ≥0) ----
    trans = 0
    company = (job.get("company") or "").strip()
    if company and not re.search(r"importante empresa|empresa del sector|confidencial",
                                 company, re.I):
        trans += 8
        b["empresa"] = "visible"
    else:
        b["empresa"] = "genérica/oculta"
    if _norm(job.get("description") or "") and len(job.get("description") or "") >= 400:
        trans += 3
    if _norm(job.get("employment_type") or "") or _norm(job.get("valid_through") or ""):
        trans += 4
    trans_pts = max(0, trans)
    b["trans_pts"] = trans_pts

    # ---- stack demandado (15) ----
    abbrs = {t.strip() for t in (job.get("techs") or "").split(";") if t.strip()}
    hits = len(abbrs & MARKET_TECHS_ABBR)
    title = job.get("title") or ""
    hits += len(set(_MARKET_TECHS_TITLE_RE.findall(title)))
    stack_pts = min(15, int(hits * 2.5))
    b["stack_pts"] = stack_pts
    b["stack_hits"] = hits

    # ---- frescura (10) — por date_canonical (min(date_posted, first_seen)) ----
    edad = age_days({"date_posted": job.get("date_posted"),
                     "first_seen": job.get("first_seen")}, now)
    if edad <= 2:
        fresh_pts = 10
    elif edad <= 7:
        fresh_pts = 7
    elif edad <= 14:
        fresh_pts = 5
    else:
        fresh_pts = 3
    b["fresh_pts"] = fresh_pts
    b["edad_dias"] = edad

    total = sal_pts + mod_pts + trans_pts + stack_pts + fresh_pts

    # ---- descuentos (clamped por componente) ----
    if _staffing(job):
        # staffing penaliza transparencia + stack (máx 20 del total, nunca < 0 global)
        total = max(0, total - 10)
        b["staffing"] = -10
    if trans == 0 and not company:
        total = max(0, total - 5)
        b["anonima"] = -5

    return max(0, int(total)), b