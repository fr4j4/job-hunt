"""Enriquecimiento por anillos.

Anillo A — JSON-LD estructurado (gratis, LinkedIn/Glassdoor/Computrabajo ficha)
Anillo B — regex nuevos (seniority_real, staffing, matices)
Anillo C — IA (deepseek-v4-flash vía ollama-cloud, batch nocturno)
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from html import unescape as _u

import requests

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)


# ============ Anillo A: JSON-LD ============

def fetch_page(url: str) -> str:
    req = requests.get(url, timeout=25, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
    return req.text


def _jsonld_blocks(html: str) -> list[dict]:
    out = []
    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            d = json.loads(_u(b))
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def _jobposting(data: dict) -> dict | None:
    if data.get("@type") == "JobPosting":
        return data
    for n in data.get("@graph") or []:
        if isinstance(n, dict) and n.get("@type") == "JobPosting":
            return n
    return None


def extract_structured(url: str) -> dict:
    """Anillo A: campos oficiales del JSON-LD + badges CB. Fiabilidad 100%."""
    info: dict = {"description": "", "date_posted": "", "valid_through": "",
                  "employment_type": "", "years_official": None, "remote_official": 0,
                  "industry": "", "education": "", "applicant_region": "",
                  "company_linkedin_url": "", "modality_badge": "", "salary": "",
                  "contrato": "", "jornada": "", "techs_desc": []}
    try:
        html = fetch_page(url)
    except Exception as e:
        info["error"] = str(e)[:100]
        return info

    jp = None
    for b in _jsonld_blocks(html):
        jp = _jobposting(b)
        if jp:
            break
    if jp:
        info["date_posted"] = (jp.get("datePosted") or "")[:10]
        info["valid_through"] = (jp.get("validThrough") or "")[:10]
        info["employment_type"] = str(jp.get("employmentType") or "")
        info["industry"] = (jp.get("industry") or "")[:60]
        if jp.get("jobLocationType") == "TELECOMMUTE":
            info["remote_official"] = 1
            info["modality_badge"] = "remoto"
        # Schema.org permite experienceRequirements/educationRequirements como dict
        # (EducationalOccupationalCredential) O como string plano ("3 years") — cubrir ambos
        exp = jp.get("experienceRequirements")
        if isinstance(exp, dict):
            months = exp.get("monthsOfExperience")
            if months:
                info["years_official"] = round(months / 12)
        else:
            m_exp = re.search(r"(\d+)\s*year", str(exp or ""), re.I)
            if m_exp:
                info["years_official"] = int(m_exp.group(1))
        edu = jp.get("educationRequirements")
        info["education"] = (edu.get("credentialCategory")
                             if isinstance(edu, dict) and edu.get("credentialCategory")
                             else str(edu)[:60] if edu and not isinstance(edu, dict) else "")
        alr = jp.get("applicantLocationRequirements")
        if isinstance(alr, dict):
            info["applicant_region"] = alr.get("name", "")
        if jp.get("baseSalary"):
            sal = jp["baseSalary"]
            val = sal.get("value") if isinstance(sal, dict) else None
            val = val if isinstance(val, dict) else {}
            if val.get("value"):
                unit = {"MONTH": "/mes", "YEAR": "/año"}.get(val.get("unitText", ""), "")
                info["salary"] = f"{sal.get('currencyCode','') if isinstance(sal, dict) else ''} {val.get('value','')}{unit}".strip()[:40]
        desc_html = jp.get("description") or ""
        if desc_html:
            info["description"] = re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", desc_html))).strip()[:1800]
    # badges Computrabajo
    for b in re.findall(r'<span class="tag base mb10">([^<]+)</span>', html):
        bl = _u(b).strip()
        lbl = bl.lower()
        if "presencial y remoto" in lbl:
            info["modality_badge"] = "híbrido"
        elif "remoto" in lbl:
            info["modality_badge"] = "remoto"
        elif "presencial" in lbl:
            info["modality_badge"] = "presencial"
        elif "contrato" in lbl:
            info["contrato"] = bl
        elif "jornada" in lbl:
            info["jornada"] = bl
        if "$" in bl:
            info["salary"] = bl[:40]
    # fallback section (LinkedIn guest sin JSON-LD)
    if not info["description"]:
        m = re.search(r'<section class="[^"]*description[^"]*"[^>]*>([\s\S]*?)</section>', html)
        if m:
            info["description"] = re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()[:4000]
    # techs de la desc
    dl = info["description"].lower()
    found = []
    for pat, ab in [("python", "Py"), ("java", "Java"), ("aws", "AWS"), ("angular", "Angular"),
                    ("react", "React"), ("kubernetes", "K8s"), ("k8s", "K8s"), ("docker", "Docker"),
                    ("golang", "Go"), ("node", "Node"), ("typescript", "TS"), ("vue", "Vue"),
                    (".net", ".NET"), ("sql", "SQL"), ("fastapi", "FastAPI"), ("django", "Django"),
                    ("kafka", "Kafka"), ("gcp", "GCP"), ("azure", "Azure"), ("scala", "Scala"),
                    ("spring", "Spring"), ("nifi", "NiFi"), ("terraform", "TF"), ("jenkins", "Jenkins"),
                    ("ci/cd", "CI/CD"), ("redis", "Redis"), ("postgres", "Postgres"), ("mongo", "Mongo")]:
        if pat in dl:
            found.append(ab)
        if len(found) >= 10:
            break
    info["techs_desc"] = found
    return info


# ============ Anillo C: batch IA (deepseek-v4-flash) ============

IA_SCHEMA = ('{"modalidad": "R"|"H"|"P"|"?", "salario_clp_mensual": numero|null, '
             '"ingles": "no"|"deseable"|"requerido"|"desconocido", "years_exp": numero|null, '
             '"seniority_real": "junior"|"semi"|"senior"|"lead", '
             '"techs": ["Py","Java","AWS","React","Angular","K8s","Docker","SQL","Node","TS","NiFi","Spring"], '
             '"red_flags": ["..."], "green_flags": ["..."], "benefits": ["..."], '
             '"resumen": "max 120 chars", "fit_reason": "max 140 chars por qué conviene o no al perfil"}')


def ia_extract(cfg: Config, job: dict, profile_desc: str) -> dict | None:
    """Llama al modelo IA con JSON forzado. Retorna dict o None."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return None
    prompt = (f'Perfil del candidato: {profile_desc}\n\n'
              f'Oferta:\nTítulo: {job.get("title","")}\nEmpresa: {job.get("company","")}\n'
              f'Ubicación: {job.get("location","")}\nDescripción: {(job.get("description") or "")[:1400]}\n\n'
              f'Responde SOLO JSON: {IA_SCHEMA}')
    body = {"model": cfg.ia.model,
            "messages": [{"role": "system",
                          "content": "Eres un extractor de datos de ofertas de empleo chilenas. "
                                     "Respondes SOLO JSON válido. Dato ausente → null. No inventes."},
                         {"role": "user", "content": prompt}],
            "temperature": 0, "format": "json"}
    for attempt in range(cfg.ia.retries + 1):
        try:
            req = requests.post(f"{cfg.ia.base_url}/chat/completions",
                                json=body, timeout=cfg.ia.timeout,
                                headers={"Authorization": f"Bearer {cfg.ia.api_key}",
                                         "Content-Type": "application/json"})
            d = req.json()
            content = d["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as e:
            if attempt == cfg.ia.retries:
                log.warning("IA batch falló: %s", e)
                return None
            time.sleep(2)
    return None


def enrich_pending(conn, cfg: Config, max_n: int | None = None) -> int:
    """Enriquece ofertas activas con descripción corta: Anillo A primero, C si sigue vacío."""
    rows = conn.execute(
        "SELECT group_id, title, company, location, url, description FROM ofertas "
        "WHERE active=1 AND (description IS NULL OR length(description)<200)").fetchall()
    pending = [dict(r) for r in rows]
    if max_n:
        pending = pending[:max_n]
    done = 0
    for r in pending:
        if not r.get("url"):
            continue
        try:
            info = extract_structured(r["url"])
        except Exception as e:
            log.warning("enrich falló para %s (%s): %s", r["group_id"], (r.get("title") or "")[:40], e)
            continue
        new_desc = (info.get("description") or "")[:1800]
        extra = (f" · {info['contrato']}" if info.get("contrato") else "") + \
                (f" · {info['jornada']}" if info.get("jornada") else "")
        desc = ((new_desc + extra) if new_desc else r.get("description") or "")[:2000]
        conn.execute("""UPDATE ofertas SET
            description=?,
            modality=COALESCE(NULLIF(modality,''), ?),
            salary=COALESCE(NULLIF(salary,''), ?),
            techs=COALESCE(NULLIF(techs,''), ?),
            date_posted=COALESCE(NULLIF(date_posted,''), ?),
            valid_through=COALESCE(NULLIF(valid_through,''), ?),
            employment_type=COALESCE(NULLIF(employment_type,''), ?),
            years_official=COALESCE(years_official, ?),
            remote_official=COALESCE(remote_official, ?),
            description_source=?
            WHERE group_id=?""",
            (desc, info.get("modality_badge") or "", info.get("salary") or "",
             ";".join(info.get("techs_desc", [])), info.get("date_posted") or "",
             info.get("valid_through") or "", info.get("years_official"),
             info.get("remote_official"), info.get("employment_type") or "",
             "jsonld" if info.get("description") else "section",
             r["group_id"]))
        done += 1
        time.sleep(2)
    conn.commit()
    return done


def run_ia_batch(conn, cfg: Config, profile_desc: str, max_n: int | None = None) -> int:
    """Anillo C: IA para los que A+B no resolvieron. 1x/día."""
    if not cfg.ia.enabled:
        return 0
    rows = conn.execute(
        "SELECT group_id, title, company, location, description, modality, salary FROM ofertas "
        "WHERE active=1 AND length(description)>400 AND "
        "(modality='' OR salary='' OR description IS NULL)").fetchall()
    pending = [dict(r) for r in rows][:max_n or cfg.ia.batch_size]
    done = 0
    for r in pending:
        parsed = ia_extract(cfg, r, profile_desc)
        if not parsed:
            continue
        mod = {"R": "remoto", "H": "híbrido", "P": "presencial"}.get(parsed.get("modalidad"), "")
        ia_fields = []
        sets, params = [], []
        if not r["modality"] and mod:
            sets.append("modality=?"); params.append(mod); ia_fields.append("modality")
        if not r["salary"] and parsed.get("salario_clp_mensual"):
            sets.append("salary=?"); params.append(f"CLP {parsed['salario_clp_mensual']}")
            ia_fields.append("salary")
        if parsed.get("seniority_real") or parsed.get("seniority"):
            sets.append("seniority_real=?"); params.append(parsed.get("seniority_real") or parsed.get("seniority"))
            ia_fields.append("seniority")
        for field in ("resumen", "fit_reason", "ingles"):
            if parsed.get(field):
                sets.append(f"ai_{field}=?")
                params.append(str(parsed[field])[:300])
                ia_fields.append(field)
        for field in ("red_flags", "green_flags", "benefits"):
            if parsed.get(field):
                sets.append(f"ai_{field}=?")
                params.append(json.dumps(parsed[field], ensure_ascii=False))
                ia_fields.append(field)
        if sets:
            sets.append("ia_model=?"); params.append(cfg.ia.model)
            sets.append("ia_fields=?"); params.append(",".join(ia_fields))
            params.append(r["group_id"])
            conn.execute(f"UPDATE ofertas SET {', '.join(sets)} WHERE group_id=?", params)
            conn.commit()   # libera el lock entre ofertas — el batch tarda minutos
            done += 1
        time.sleep(3)
    conn.commit()
    return done