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
    """'4,000' (USD) | '$ 2.800.000,00 (Mensual)' | 'USD 4000' → CLP mensual aproximado."""
    import re
    raw = sal_raw or ""
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
    is_usd = "usd" in raw.lower() or (re.fullmatch(r"[\d,]{3,}", num_s) and "." not in num_s and len(num_s) <= 7)
    if "." in num_s and "," in num_s:
        num_s = num_s.replace(".", "").replace(",", ".")
    elif has_thousands(num_s):
        num_s = num_s.replace(".", "").replace(",", "")
    try:
        num = float(num_s)
    except ValueError:
        return None
    if num < 1000:
        return None
    if is_usd:
        return int(num * 950)  # tasa aproximada USD→CLP
    return int(num)


def has_thousands(s: str) -> bool:
    parts = s.split(".")
    return len(parts) > 1 and all(len(p) == 3 for p in parts[1:])