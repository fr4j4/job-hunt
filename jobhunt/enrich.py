"""Enriquecimiento por anillos.

Anillo A — JSON-LD estructurado (gratis, LinkedIn/Glassdoor/Computrabajo ficha)
Anillo B — regex nuevos (seniority_real, staffing, matices)
Anillo C — IA (deepseek-v4-flash vía ollama-cloud, batch nocturno)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.request
from html import unescape as _u

import requests

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)


# ============ Anillo A: JSON-LD ============

def fetch_page(url: str) -> tuple[str, str]:
    """fetch_page v2 (spec salarios-robustos §1.4/A5): retorna (html, access).
    access ∈ {ok, not_found, blocked, error}. Nunca lanza por HTTP — el llamador
    decide según access. 'error' es transitorio (timeout/red)."""
    try:
        req = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
    except requests.exceptions.Timeout:
        return "", "error"
    except Exception:
        return "", "error"
    html = req.text or ""
    # not_found: 404/410 o patrones de oferta expirada
    if req.status_code in (404, 410):
        return html, "not_found"
    low = html.lower()
    if re.search(r"empleo no disponible|oferta expirad|job no longer|no longer available", low):
        return html, "not_found"
    # blocked: 403/429 o marcadores de desafío CF/captcha
    if req.status_code in (403, 429):
        return html, "blocked"
    if re.search(r"cf-browser-verification|just a moment|challenge-platform|captcha|"
                 r"verifying you are human|attention required", low):
        return html, "blocked"
    # HTML mínimo sin JSON-LD = probable página de bloqueo genérica
    if len(html) < 500 and "application/ld+json" not in low:
        return html, "blocked"
    return html, "ok"


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
    """Anillo A: campos oficiales del JSON-LD + badges CB. Fiabilidad 100%.
    Usa fetch_page v2: access ∈ {ok, not_found, blocked, error} — el llamador
    (enrich_pending) decide qué hacer según access (§1.4)."""
    info: dict = {"description": "", "date_posted": "", "valid_through": "",
                  "employment_type": "", "years_official": None, "remote_official": 0,
                  "industry": "", "education": "", "applicant_region": "",
                  "company": "", "company_linkedin_url": "", "modality_badge": "", "salary": "",
                  "contrato": "", "jornada": "", "techs_desc": []}
    try:
        html, access = fetch_page(url)
        info["_access"] = access
        if access != "ok":
            info["error"] = f"access={access}"
            return info
    except Exception as e:
        info["error"] = str(e)[:100]
        info["_access"] = "error"
        return info

    # CB: oferta expirada redirige a un listado genérico — fetch_page no expone la URL final,
    # así que re-petición con requests para leer la URL efectiva
    if "computrabajo" in url.lower():
        try:
            resp = requests.get(url, timeout=25, allow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            final_url = str(resp.url)
            info["_cb_expired"] = (("/ofertas-de-trabajo/" not in final_url)
                                   and "/trabajo-de-" in final_url) or \
                bool(re.search(r"empleo no disponible|oferta expirad", html, re.I))
        except Exception:
            pass

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
    # CB: empresa del subtítulo de la ficha ("Empresa - Comuna, Región")
    if "computrabajo" in url.lower():
        h1 = html.find("<h1")
        m_emp = re.search(r'</h1>\s*<p[^>]*>\s*(?:<[^>]+>\s*)*([^<]{2,80})\s*-\s*',
                          html[h1:h1 + 2500] if h1 >= 0 else "")
        if m_emp:
            emp = re.sub(r"\s+", " ", _u(m_emp.group(1))).strip()
            # descartar placeholders anónimos de CB
            if not re.search(r"importante empresa|empresa del sector|empresa líder", emp, re.I):
                info["company"] = emp[:80]
    # fallback section (LinkedIn guest sin JSON-LD)
    if not info["description"]:
        m = re.search(r'<section class="[^"]*description[^"]*"[^>]*>([\s\S]*?)</section>', html)
        if m:
            info["description"] = re.sub(r"\s+", " ", _u(re.sub(r"<[^>]+>", " ", m.group(1)))).strip()[:4000]
    # fallback Computrabajo: ficha sin JSON-LD → desc en <p class="mbB"> (antes <div class="mbB">;
    # el div ahora solo contiene badges de salario/contrato, el <p> tiene el texto completo)
    if not info["description"]:
        m = re.search(r'<(?:p|div) class="mbB">([a-zA-ZÁÉÍÓÚáéíóúÑñ¡¿][\s\S]{100,6000}?)</(?:p|div)>', html)
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
             '"idiomas": [{"idioma": "inglés|alemán|francés|portugués|chino|japonés|italiano|otro", "nivel": "básico|intermedio|avanzado|nativo|fluido", "excluyente": true|false}], '
             '"rol_categoria": "Full Stack"|"Backend"|"Frontend"|"Data"|"Mobile"|"AI/ML"|"Tech Lead"|"DevOps/Cloud"|"QA"|"Software"|"Seguridad"|"Ingeniería no-software"|"Analista/Empresa"|"Profesor/Formación"|"Soporte/TI"|"No-tech"|"Otro", '
             '"resumen": "max 120 chars", "fit_reason": "max 140 chars por qué conviene o no al perfil", '
             '"opinion": "max 160 chars — comentario editorial sobre la oferta: contexto de mercado, señal notable (empresa conocida, staffing, nicho escaso), comparación con la mediana salarial o red flag relevante. PROHIBIDO consejos al candidato (nada de destaca/pregunta/no apliques). No repitas el resumen"}')


def compute_market_context(conn) -> str:
    """Contexto de mercado para el comentario editorial — v2 spec salarios-robustos §3.
    Se calcula UNA vez por lote en el MAIN y se pasa como argumento (workers NO tocan DB).

    - Parser único _salary_to_clp_monthly (A1: maneja formato CB sin crashear)
    - Cuarentena física (stats.FLOOR/CEILING) — outliers fuera de stats, no del pool
    - Stats: mediana percentil, P75, n, % declarantes, CV (A8: SIN lista de crudos
      anómalos — la anomalía individual viaja por §4.2, no por el contexto de lote)
    - <10 muestras → modo insuficiente (ordena NO citar stats)
    """
    from . import stats as _st
    try:
        rows = conn.execute(
            "SELECT salary FROM ofertas WHERE active=1 AND salary != '' AND salary IS NOT NULL"
        ).fetchall()
        vals = []
        for (raw,) in rows:
            v = _st.parse_salary_clp(raw or "")
            if v > 0:
                vals.append(v)
        n_tot = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1").fetchone()[0]
        n_rem = conn.execute(
            "SELECT COUNT(*) FROM ofertas WHERE active=1 AND modality='remoto'").fetchone()[0]
        n_decl = len(vals)
        pct_decl = int(100 * n_decl / n_tot) if n_tot else 0
        if n_decl < _st.CV_MIN_N:
            return (f"muestra salarial insuficiente ({n_decl} ofertas declaran de {n_tot} activas) — "
                    f"NO cites estadísticas de salario en opinion; describe solo la oferta. "
                    f"remoto: {n_rem} de {n_tot} activas")
        clean = sorted(v for v in vals if _st.FLOOR <= v <= _st.CEILING)
        med = int(_st._median(clean))
        p75 = int(clean[min(len(clean) - 1, int(len(clean) * 0.75))])
        cv, cv_label = _st.cv_health(vals)
        return (f"mediana salarial dev ${med:,} (P75: ${p75:,}) calculada de {len(clean)} ofertas "
                f"con sueldo declarado · CV {cv} ({cv_label}) · {pct_decl}% declara · "
                f"remoto: {n_rem} de {n_tot} activas")
    except Exception:
        return "estadística de mercado no disponible — no cites números de mercado en opinion"


def ia_extract_detail(cfg: Config, job: dict, profile_desc: str,
                      mercado: str = "") -> tuple[dict | None, str]:
    """Igual que ia_extract pero retorna (parsed, err_kind) para el breaker B5:
    '' = ok · 'rate' = 429/5xx (cuenta para el breaker) · 'timeout' · 'other'.
    HTTP PURO — sin SQLite (P0-3). Nunca lanza."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return None, "other"
    # §4.2/A8: línea individual de anomalía — SOLO si ESTA oferta es la anómala
    from . import stats as _st
    nota_anomalia = ""
    sal_raw_esta = (job.get("salary_raw") or "").strip()
    sal_esta = _st.parse_salary_clp(job.get("salary") or "")
    if job.get("salary_status") in ("suspect", "implausible") and sal_esta > 0:
        if _st.annual_likely(sal_esta, _st.parse_salary_clp("CLP 2150000") or 2150000):
            hipotesis = f"probable cifra anual (≈ ${sal_esta // 12:,}/mes)"
        else:
            hipotesis = "error de la fuente"
        nota_anomalia = (f"\nNota: el sueldo declarado de esta oferta (${sal_raw_esta or sal_esta:,}) "
                         f"fue clasificado anómalo (motivo: {job.get('salary_note') or 'estadística'}; "
                         f"hipótesis: {hipotesis}) — coméntalo en opinion según las reglas.")
    prompt = (f'Perfil del candidato: {profile_desc}\n\n'
              f'Contexto de mercado (para el campo opinion): {mercado}\n\n'
              f'Oferta:\nTítulo: {job.get("title","")}\nEmpresa: {job.get("company","")}\n'
              f'Ubicación: {job.get("location","")}\n'
              f'Sueldo declarado: {job.get("salary") or "(no declarado — infiere rango de mercado solo si el texto lo permite)"}\n'
              f'Modalidad declarada: {job.get("modality") or "(no declarada)"}\n'
              f'Descripción: {(job.get("description") or "")[:2400]}'
              f'{nota_anomalia}\n\n'
              f'Responde SOLO JSON: {IA_SCHEMA}')
    body = {"model": cfg.ia.model,
            "messages": [{"role": "system",
                          "content": "Eres un extractor de datos de ofertas de empleo chilenas. "
                                     "Respondes SOLO JSON válido. Dato ausente → null. No inventes. "
                                     "El campo 'opinion' es un comentario editorial sobre la oferta "
                                     "(contexto de mercado, señal notable, comparación salarial) — "
                                     "NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', "
                                     "'no apliques', 'practica'). "
                                     "Si el sueldo está declarado, la opinion DEBE comentarlo "
                                     "(comparar contra la mediana del CONTEXTO provisto); no digas 'sin salario' "
                                     "si el campo Sueldo declarado trae un valor. "
                                     "ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar en "
                                     "opinion son los del CONTEXTO DE MERCADO provisto arriba — prohibido "
                                     "citar medianas, percentiles o estadísticas de tu conocimiento propio "
                                     "o de otras fuentes. Si el contexto dice que la muestra es insuficiente, "
                                     "no compares salarios: describe solo la oferta. "
                                     "Si el sueldo de ESTA oferta viene marcado como anómalo en la Nota, la "
                                     "opinion DEBE: (1) citar el valor declarado tal cual, (2) señalar la "
                                     "anomalía con la hipótesis provista (probable anual/error de fuente), "
                                     "(3) comparar contra la mediana provista. NUNCA corrijas el valor ni lo "
                                     "omitas. Prohibido comentar anomalías de OTRAS ofertas."},
                         {"role": "user", "content": prompt}],
            "temperature": 0, "format": "json"}
    if cfg.ia.reasoning_effort:          # knob opcional (default: off — flash ya responde rápido)
        body["reasoning_effort"] = cfg.ia.reasoning_effort
    for attempt in range(cfg.ia.retries + 1):
        try:
            req = requests.post(f"{cfg.ia.base_url}/chat/completions",
                                json=body, timeout=cfg.ia.timeout,
                                headers={"Authorization": f"Bearer {cfg.ia.api_key}",
                                         "Content-Type": "application/json"})
            if req.status_code >= 400:
                kind = "rate" if (req.status_code == 429 or req.status_code >= 500) else "other"
                if attempt == cfg.ia.retries:
                    log.warning("IA HTTP %d para %s: %s", req.status_code,
                                job.get("group_id", "?"), req.text[:120])
                    return None, kind
                time.sleep(2)
                continue
            d = req.json()
            content = d["choices"][0]["message"]["content"]
            data = json.loads(content)
            # DEV-3b: garantiza dict (json.loads acepta list/str)
            return (data, "") if isinstance(data, dict) else (None, "other")
        except requests.exceptions.Timeout:
            if attempt == cfg.ia.retries:
                log.warning("IA timeout para %s", job.get("group_id", "?"))
                return None, "timeout"   # B5: timeout aislado NO alimenta el breaker
            time.sleep(2)
        except Exception as e:
            if attempt == cfg.ia.retries:
                log.warning("IA batch falló: %s", e)
                return None, "other"
            time.sleep(2)
    return None, "other"


def ia_extract(cfg: Config, job: dict, profile_desc: str, mercado: str = "") -> dict | None:
    """Llama al modelo IA con JSON forzado. Nunca lanza: retorna dict o None."""
    return ia_extract_detail(cfg, job, profile_desc, mercado)[0]


# --- Modo lote (spec-enrich-lotes §2) ---
_LOTE_SCHEMA = {
    "type": "object",
    "properties": {"ofertas": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "idx": {"type": "integer"},
            "opinion": {"type": "string"},
            "resumen": {"type": "string"},
            "fit_reason": {"type": "string"},
            "seniority_real": {"type": "string"},
            "rol_categoria": {"type": "string"},
            "ingles": {"type": "string"},
            "idiomas": {"type": "array", "items": {"type": "object",
                "properties": {"idioma": {"type": "string"}, "nivel": {"type": "string"},
                                "excluyente": {"type": "boolean"}},
                "required": ["idioma", "nivel", "excluyente"], "additionalProperties": False}},
            "modalidad": {"type": "string"},
            "salario_clp_mensual": {"type": "integer"},
            "red_flags": {"type": "array", "items": {"type": "string"}},
            "green_flags": {"type": "array", "items": {"type": "string"}},
            "benefits": {"type": "array", "items": {"type": "string"}}},
        "required": ["idx", "opinion", "resumen", "fit_reason", "seniority_real",
                      "rol_categoria", "ingles", "idiomas", "modalidad",
                      "salario_clp_mensual", "red_flags", "green_flags", "benefits"],
        "additionalProperties": False}},
        "required": ["ofertas"], "additionalProperties": False}}


def _lote_prompt(rows: list[dict], profile_desc: str, mercado: str) -> str:
    """Prompt de lote: N bloques --- OFERTA {i} --- con idx 1..N (spec §2.1)."""
    from . import stats as _st
    bloques = []
    for i, r in enumerate(rows, 1):
        nota = ""
        sal_esta = _st.parse_salary_clp(r.get("salary") or "")
        if r.get("salary_status") in ("suspect", "implausible") and sal_esta > 0:
            if _st.annual_likely(sal_esta, _st.parse_salary_clp("CLP 2150000") or 2150000):
                hip = f"probable cifra anual (≈ ${sal_esta // 12:,}/mes)"
            else:
                hip = "error de la fuente"
            raw_mostrar = (r.get('salary_raw') or r.get('salary') or "").strip()
            monto_mostrar = raw_mostrar if raw_mostrar else f"{sal_esta:,}"
            nota = (f"\nNota: el sueldo declarado de esta oferta (${monto_mostrar}) "
                    f"fue clasificado anómalo (motivo: {r.get('salary_note') or 'estadística'}; "
                    f"hipótesis: {hip}) — coméntalo en opinion según las reglas.")
        bloques.append(
            f"--- OFERTA {i} ---\n"
            f"Título: {r.get('title', '')}\nEmpresa: {r.get('company', '')}\n"
            f"Ubicación: {r.get('location', '')}\n"
            f"Sueldo declarado: {r.get('salary') or '(no declarado — infiere rango de mercado solo si el texto lo permite)'}\n"
            f"Modalidad declarada: {r.get('modality') or '(no declarada)'}\n"
            f"Descripción: {(r.get('description') or '')[:2400]}{nota}")
    return (f"Perfil del candidato: {profile_desc}\n\n"
            f"Contexto de mercado (para el campo opinion): {mercado}\n\n"
            + "\n\n".join(bloques) +
            "\n\nResponde un JSON array con UN objeto por oferta (idx 1..N), cada uno con: "
            "idx, opinion, resumen, fit_reason, seniority_real, rol_categoria, ingles, idiomas, "
            "modalidad, salario_clp_mensual, red_flags, green_flags, benefits. "
            "Si una oferta no declara salario, salario_clp_mensual = 0 (cero, nunca inventes un monto).")


def ia_extract_lote(cfg: Config, rows: list[dict], profile_desc: str,
                    mercado: str = "") -> tuple[list[dict] | None, str]:
    """Llamada IA por LOTE (spec-enrich-lotes §2). HTTP PURO — sin SQLite.
    Retorna (lista de dicts con idx, o None) + err_kind ('' | 'rate' | 'timeout' | 'other').
    El schema estricto garantiza estructura; el mapeo idx→fila lo hace el llamador."""
    if not cfg.ia.enabled or not cfg.ia.api_key or not rows:
        return None, "other"
    body = {"model": cfg.ia.model,
            "messages": [{"role": "system",
                          "content": "Eres un extractor de datos de ofertas de empleo chilenas. "
                                     "Respondes SOLO JSON válido. Dato ausente → null. No inventes. "
                                     "El campo 'opinion' es un comentario editorial sobre la oferta "
                                     "(contexto de mercado, señal notable, comparación salarial) — "
                                     "NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', "
                                     "'no apliques', 'practica'). "
                                     "Si el sueldo está declarado, la opinion DEBE comentarlo "
                                     "(comparar contra la mediana del CONTEXTO provisto); no digas 'sin salario' "
                                     "si el campo Sueldo declarado trae un valor. "
                                     "ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar en "
                                     "opinion son los del CONTEXTO DE MERCADO provisto arriba — prohibido "
                                     "citar medianas, percentiles o estadísticas de tu conocimiento propio "
                                     "o de otras fuentes. Si el contexto dice que la muestra es insuficiente, "
                                     "no compares salarios: describe solo la oferta. "
                                     "Si el sueldo de UNA oferta viene marcado como anómalo en su Nota, la "
                                     "opinion DEBE: (1) citar el valor declarado tal cual, (2) señalar la "
                                     "anomalía con la hipótesis provista (probable anual/error de fuente), "
                                     "(3) comparar contra la mediana provista. NUNCA corrijas el valor ni lo "
                                     "omitas. Prohibido comentar anomalías de OTRAS ofertas."},
                        {"role": "user", "content": _lote_prompt(rows, profile_desc, mercado)}],
            "temperature": 0,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "lote_ofertas", "strict": True,
                                                "schema": _LOTE_SCHEMA}}}
    if cfg.ia.reasoning_effort:
        body["reasoning_effort"] = cfg.ia.reasoning_effort
    for attempt in range(cfg.ia.retries + 1):
        try:
            req = requests.post(f"{cfg.ia.base_url}/chat/completions",
                                json=body, timeout=cfg.ia.timeout,
                                headers={"Authorization": f"Bearer {cfg.ia.api_key}",
                                         "Content-Type": "application/json"})
            if req.status_code >= 400:
                kind = "rate" if (req.status_code == 429 or req.status_code >= 500) else "other"
                if attempt == cfg.ia.retries:
                    log.warning("IA lote HTTP %d: %s", req.status_code, req.text[:120])
                    return None, kind
                time.sleep(2)
                continue
            d = req.json()
            content = d["choices"][0]["message"]["content"]
            data = json.loads(content)
            arr = data.get("ofertas") if isinstance(data, dict) else data
            if not isinstance(arr, list):
                return None, "other"
            # normalizar idiomas: el modelo puede devolver strings o dicts (spike verificado)
            for item in arr:
                if isinstance(item, dict) and isinstance(item.get("idiomas"), list):
                    item["idiomas"] = [
                        {"idioma": str(i).lower()[:20], "nivel": "", "excluyente": False}
                        if isinstance(i, str) else i
                        for i in item["idiomas"] if i]
            return arr, ""
        except requests.exceptions.Timeout:
            if attempt == cfg.ia.retries:
                log.warning("IA lote timeout (%d ofertas)", len(rows))
                return None, "timeout"
            time.sleep(2)
        except Exception as e:
            if attempt == cfg.ia.retries:
                log.warning("IA lote falló: %s", e)
                return None, "other"
            time.sleep(2)
    return None, "other"


def _extract_aira_spa(url: str) -> dict:
    """Ficha AIRA (SPA client-side): requiere browser. Extrae el <p> más largo = descripción."""
    info: dict = {"description": "", "description_source": "aira-spa"}
    try:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            br = pw.chromium.launch(headless=False, args=["--no-sandbox"])
            pg = br.new_page()
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=25000)
                pg.wait_for_timeout(3500)
                ps = pg.evaluate(
                    "() => Array.from(document.querySelectorAll('p'))"
                    ".filter(e => e.innerText && e.innerText.length > 200)"
                    ".map(e => e.innerText.trim())"
                    ".sort((a, b) => b.length - a.length).slice(0, 2).join(' ')")
                info["description"] = re.sub(r"\s+", " ", ps or "").strip()[:2000]
            finally:
                br.close()
        finally:
            pw.stop()
    except Exception as e:
        log.warning("aira-spa falló (%s): %s", url[:50], e)
    return info


def get_salary_pool(conn) -> list[int]:
    """Pool de salarios activos, calculado UNA vez por invocación (spec-enrich-lotes §4).
    Leave-one-out se aplica en el llamador (excluir el valor evaluado)."""
    from . import stats as _st
    return [v for (s,) in conn.execute(
        "SELECT salary FROM ofertas WHERE active=1 AND salary!='' AND salary IS NOT NULL")
        if (v := _st.parse_salary_clp(s)) > 0]


def _fetch_ficha(r: dict) -> dict:
    """Solo HTTP — NUNCA toca la DB (spec-enrich-lotes §3.1.2, patrón P0-3).
    Retorna info (con _access) o {"_access": "error"}."""
    try:
        if "airavirtual.com" in (r.get("url") or ""):
            return _extract_aira_spa(r["url"])
        return extract_structured(r["url"])
    except Exception as e:
        log.warning("enrich falló para %s (%s): %s", r["group_id"], (r.get("title") or "")[:40], e)
        return {"_access": "error"}


def _aplicar_ficha(conn, r: dict, info: dict, pool: list[int]) -> str:
    """Aplica la ficha en el MAIN (árbitro + UPDATE + commit). Retorna 'ok' | 'expired' | 'blocked'."""
    from . import stats as _st
    access = info.get("_access", "ok")
    if info.get("_cb_expired") or access == "not_found":
        conn.execute("UPDATE ofertas SET active=0 WHERE group_id=?", (r["group_id"],))
        conn.commit()
        log.info("oferta expirada (%s): %s — active=0", access, (r.get("title") or "")[:40])
        return "expired"
    if access in ("blocked", "error"):
        conn.execute("UPDATE ofertas SET fetch_fails=COALESCE(fetch_fails,0)+1 WHERE group_id=?",
                     (r["group_id"],))
        conn.commit()
        log.info("enrich bloqueado (%s) para %s — fetch_fails+1, reintenta después",
                 access, (r.get("title") or "")[:40])
        return "blocked"
    conn.execute("UPDATE ofertas SET fetch_fails=0, last_fetch_ok=datetime('now') WHERE group_id=?",
                 (r["group_id"],))
    new_desc = (info.get("description") or "")[:1800]
    extra = (f" · {info['contrato']}" if info.get("contrato") else "") + \
            (f" · {info['jornada']}" if info.get("jornada") else "")
    desc = ((new_desc + extra) if new_desc else r.get("description") or "")[:2000]
    # ---- árbitro de salario (§1.3, ACCESO_OK únicamente) ----
    sal_fila = conn.execute(
        "SELECT salary, salary_raw, salary_source, salary_status FROM ofertas WHERE group_id=?",
        (r["group_id"],)).fetchone()
    sal_actual = sal_fila["salary"] if sal_fila else ""
    sal_source = sal_fila["salary_source"] if sal_fila else ""
    sal_status = sal_fila["salary_status"] if sal_fila else ""
    sal_texto_crudo = (info.get("salary") or "").strip()
    sal_texto_val = _st.parse_salary_clp(sal_texto_crudo) if sal_texto_crudo else 0
    arb_salary, arb_source, arb_status, arb_note = None, None, None, None
    if sal_texto_val > 0:
        if re.search(r"/año|/year|anual", sal_texto_crudo, re.I) and sal_texto_val > _st.CEILING:
            sal_texto_val = sal_texto_val // 12
            arb_note = "annual_likely"
        sal_feed_val = _st.parse_salary_clp(sal_actual)
        feed_implausible = sal_status == "implausible" or \
            (sal_feed_val > 0 and (sal_feed_val < _st.FLOOR or sal_feed_val > _st.CEILING))
        coincide = sal_feed_val > 0 and abs(sal_feed_val - sal_texto_val) <= max(1, int(0.01 * sal_texto_val))
        if feed_implausible or sal_source in ("", "feed") and sal_feed_val == 0:
            arb_salary, arb_source, arb_status = sal_texto_crudo[:40], "text", "trusted"
            arb_note = arb_note or ("text_wins" if feed_implausible else "text_confirms")
        elif coincide:
            arb_source, arb_status, arb_note = "text", "trusted", arb_note or "text_confirms"
        else:
            arb_salary, arb_source, arb_status, arb_note = sal_texto_crudo[:40], "text", "trusted", "text_wins"
    elif sal_status == "implausible":
        arb_salary, arb_source, arb_status, arb_note = "", "feed", "implausible", "source_unverifiable"
    # clasificación estadística con pool cacheado + leave-one-out (spec-enrich-lotes §4)
    if arb_salary is not None and arb_salary:
        v = _st.parse_salary_clp(arb_salary)
        pool_loo = [x for x in pool if x != v]
        stat_status, stat_note = _st.classify_salary(v, pool_loo)
        arb_status, arb_note = stat_status, stat_note or arb_note
    conn.execute("""UPDATE ofertas SET
        description=?,
        company=CASE WHEN company='' OR company IS NULL THEN ? ELSE company END,
        modality=COALESCE(NULLIF(modality,''), ?),
        salary=COALESCE(?, salary),
        salary_source=COALESCE(?, salary_source),
        salary_status=COALESCE(?, salary_status),
        salary_note=COALESCE(?, salary_note),
        salary_raw=CASE WHEN salary_raw='' OR salary_raw IS NULL THEN COALESCE(salary, '') ELSE salary_raw END,
        techs=COALESCE(NULLIF(techs,''), ?),
        date_posted=COALESCE(NULLIF(date_posted,''), ?),
        valid_through=COALESCE(NULLIF(valid_through,''), ?),
        employment_type=COALESCE(NULLIF(employment_type,''), ?),
        years_official=COALESCE(years_official, ?),
        remote_official=COALESCE(remote_official, ?),
        description_source=?
        WHERE group_id=?""",
        (desc, info.get("company") or "", info.get("modality_badge") or "", arb_salary,
         arb_source, arb_status, arb_note,
         ";".join(info.get("techs_desc", [])), info.get("date_posted") or "",
         info.get("valid_through") or "", info.get("years_official"),
         info.get("remote_official"), info.get("employment_type") or "",
         "jsonld" if info.get("description") else "section",
         r["group_id"]))
    conn.commit()
    return "ok"


def enrich_pending(conn, cfg: Config | None, max_n: int | None = None,
                   groups: set[str] | None = None, stop_event=None,
                   solo_fetch: bool = False) -> int:
    """Enriquece ofertas activas con descripción corta: Anillo A primero, C si sigue vacío.

    spec-enrich-lotes v2 (§3-5): fetch paralelo (threads SOLO HTTP, el main escribe),
    recarga N' tras fetch, pool de salarios cacheado con LOO, modo lote IA si
    cfg.ia.batch_prompt > 1, sleeps reducidos, stop_event entre grupos, ctx_version.
    groups: si se pasa, SOLO esas group_id. Commit POR FICHA.
    solo_fetch: True = solo Anillo A + árbitro (FIX B del scan, sin IA — P1-3)."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    from . import stats as _st
    cols = ("group_id, title, company, location, url, description, salary, modality, "
            "salary_raw, salary_status, salary_note")
    if groups:
        qs = ",".join("?" for _ in groups)
        rows = conn.execute(
            f"SELECT {cols} FROM ofertas "
            f"WHERE active=1 AND (description IS NULL OR length(description)<200) "
            f"AND group_id IN ({qs}) ORDER BY score DESC", tuple(groups)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM ofertas "
            "WHERE active=1 AND (description IS NULL OR length(description)<200) "
            "ORDER BY score DESC").fetchall()
    pending = [dict(r) for r in rows]
    if max_n:
        pending = pending[:max_n]
    if not pending:
        return 0
    # pool cacheado UNA vez por invocación (§4) + contexto + ctx_version
    pool = get_salary_pool(conn)
    mercado = compute_market_context(conn) if cfg else ""
    ctx_version = "ctx-" + hashlib.sha256(mercado.encode()).hexdigest()[:8] if cfg else ""
    perfil = profile_description(cfg) if cfg else ""
    N = cfg.ia.batch_prompt if (cfg and cfg.ia.batch_prompt > 1 and not solo_fetch) else 1
    done = 0
    for i in range(0, len(pending), N):
        if stop_event is not None and stop_event.is_set():
            log.info("enrich_pending: stop_event seteado — corte entre grupos")
            break
        grupo = pending[i:i + N]
        # fetch paralelo: threads SOLO HTTP (P0-3); AIRA serializado (5 chromium = no)
        if len(grupo) > 1 and not any("airavirtual.com" in (r.get("url") or "") for r in grupo):
            with ThreadPoolExecutor(max_workers=min(len(grupo), 5)) as ex:
                infos = list(ex.map(_fetch_ficha, grupo))
        else:
            infos = [_fetch_ficha(r) for r in grupo]
        # el MAIN aplica árbitro + UPDATE por ficha (los threads no tocan la DB)
        vivas = []
        for r, info in zip(grupo, infos):
            estado = _aplicar_ficha(conn, r, info, pool)
            done += 1
            if estado == "ok":
                vivas.append(r["group_id"])
            time.sleep(0.5)
        if solo_fetch or not vivas or not cfg:
            continue
        # recarga N' (active=1) — el árbitro actualizó salary_status/note (§3.1.4)
        qs = ",".join("?" for _ in vivas)
        recargadas = [dict(x) for x in conn.execute(
            f"SELECT group_id, title, company, location, description, salary, modality, "
            f"salary_raw, salary_status, salary_note FROM ofertas "
            f"WHERE active=1 AND group_id IN ({qs})", tuple(vivas))]
        if not recargadas:
            continue
        if N > 1:
            arr, err = ia_extract_lote(cfg, recargadas, perfil, mercado)
            if arr is None:
                if err == "rate":
                    log.warning("IA lote rate-limit — grupo queda en cola para el batch nocturno")
                    continue
                # fallback individual (timeout/other) — §2.5
                for r in recargadas:
                    parsed, _ = ia_extract_detail(cfg, r, perfil, mercado)
                    if parsed:
                        apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version)
            else:
                idxs: dict[int, dict] = {}
                for item in arr:
                    try:
                        k = int(item.get("idx") or 0)
                    except (TypeError, ValueError):
                        continue
                    if k not in idxs:      # política de duplicados: gana el primero (§2.3)
                        idxs[k] = item
                for i, r in enumerate(recargadas, 1):
                    if i in idxs:
                        apply_ia_result(conn, cfg, r, idxs[i], ctx_version=ctx_version)
                    else:
                        parsed, _ = ia_extract_detail(cfg, r, perfil, mercado)
                        if parsed:
                            apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version)
        else:
            for r in recargadas:
                parsed, _ = ia_extract_detail(cfg, r, perfil, mercado)
                if parsed:
                    apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version)
    return done


def profile_description(cfg: Config) -> str:
    """Descriptor del candidato para el prompt IA (única fuente)."""
    p = cfg.profile
    return (f"{p.title}, {p.years_exp} años exp, stack {', '.join(p.techs[:6])}, "
            f"inglés {p.english_level}, prefiere {'/'.join(p.modality_pref[:2])}, "
            f"banda {p.salary_min}-{p.salary_max} CLP")


def ia_queue_count(conn) -> int:
    """Ofertas en cola para el batch IA (con descripción, faltan datos, sin IA)."""
    return conn.execute(
        "SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model='' AND "
        "(length(description)>200 OR description_source!='') AND "
        "(modality='' OR salary='' OR description IS NULL)").fetchone()[0]


def apply_ia_result(conn, cfg: Config, r: dict, parsed: dict | None,
                    ctx_version: str = "") -> bool:
    """Escribe los campos IA de UNA oferta en la DB. Solo el MAIN la llama
    (patrón conexión única). Retorna True si escribió algo. parsed=None → no-op.

    ctx_version: hash8 del contexto de mercado usado (spec salarios-robustos §7.4)
    — trazabilidad para regenerar opinions de eras obsoletas.
    Guard A3: SOLO escribe salary si salary_source='' — nunca pisa procedencia
    (feed/text) ni rellena un salary='' que el árbitro vació."""
    if not parsed:
        return False
    mod = {"R": "remoto", "H": "híbrido", "P": "presencial"}.get(parsed.get("modalidad"), "")
    ia_fields = []
    sets, params = [], []
    # registro sin marca IA pero con contenido generado previamente (fósil de otra
    # era del pool): la opinión nueva reemplaza SIEMPRE — si la IA no trae un campo
    # regenerable, el fósil se limpia (iaclear solo desmarca; el enrich sanitiza)
    if not r.get("ia_model") and (r.get("ai_opinion") or r.get("ai_resumen") or r.get("ai_fit_reason")):
        sets += ["ai_opinion=?", "ai_resumen=?", "ai_fit_reason=?"]
        params += ["", "", ""]
    if not r.get("modality") and mod:
        sets.append("modality=?"); params.append(mod); ia_fields.append("modality")
    # guard A3: la IA propone salary SOLO si no hay procedencia ya establecida
    if not r.get("salary") and not r.get("salary_source") and parsed.get("salario_clp_mensual"):
        sets.append("salary=?"); params.append(f"CLP {parsed['salario_clp_mensual']}")
        sets.append("salary_source=?"); params.append("ia")
        ia_fields.append("salary")
    if parsed.get("seniority_real") or parsed.get("seniority"):
        sets.append("seniority_real=?"); params.append(parsed.get("seniority_real") or parsed.get("seniority"))
        ia_fields.append("seniority")
    for field in ("resumen", "fit_reason", "ingles"):
        if parsed.get(field):
            sets.append(f"ai_{field}=?")
            params.append(str(parsed[field])[:300])
            ia_fields.append(field)
    if parsed.get("opinion"):
        sets.append("ai_opinion=?")
        params.append(str(parsed["opinion"])[:200])
        ia_fields.append("opinion")
    if parsed.get("rol_categoria"):
        sets.append("rol_categoria=?")
        params.append(str(parsed["rol_categoria"])[:40])
        ia_fields.append("rol_categoria")
    if parsed.get("idiomas") and isinstance(parsed["idiomas"], list) and parsed["idiomas"]:
        idiomas_limpio = [
            {"idioma": str(i.get("idioma", ""))[:20].lower(),
             "nivel": str(i.get("nivel", ""))[:20].lower(),
             "excluyente": bool(i.get("excluyente"))}
            for i in parsed["idiomas"] if isinstance(i, dict) and i.get("idioma")
        ]
        if idiomas_limpio:
            sets.append("ai_idiomas=?")
            params.append(json.dumps(idiomas_limpio, ensure_ascii=False)[:400])
            ia_fields.append("idiomas")
    for field in ("red_flags", "green_flags", "benefits"):
        if parsed.get(field):
            sets.append(f"ai_{field}=?")
            params.append(json.dumps(parsed[field], ensure_ascii=False))
            ia_fields.append(field)
    if not sets:
        return False
    sets.append("ia_model=?"); params.append(cfg.ia.model)
    sets.append("ia_fields=?"); params.append(",".join(ia_fields))
    if ctx_version:
        sets.append("ctx_version=?"); params.append(ctx_version)
    params.append(r["group_id"])
    conn.execute(f"UPDATE ofertas SET {', '.join(sets)} WHERE group_id=?", params)
    return True


def run_ia_batch(conn, cfg: Config, profile_desc: str, max_n: int | None = None,
                 groups: set[str] | None = None, progress=None,
                 stop_event=None) -> int:
    """Anillo C: IA para los que A+B no resolvieron. 1x/día (o grupos específicos).

    groups: si se pasa, procesa SOLO esos group_id (ofertas recién indexadas),
    sin exigir descripción larga — la IA trabaja con lo que haya.
    progress: callback opcional (done, total, título) para reportar avance.
    """
    if not cfg.ia.enabled:
        return 0
    mercado = compute_market_context(conn)
    import hashlib
    ctx_version = "ctx-" + hashlib.sha256(mercado.encode()).hexdigest()[:8]
    if groups:
        qs = ",".join("?" for _ in groups)
        rows = conn.execute(
            f"SELECT group_id, title, company, location, description, modality, salary, "
            f"salary_raw, salary_status, salary_note FROM ofertas WHERE active=1 AND group_id IN ({qs})",
            tuple(groups)).fetchall()
    else:
        # C9 (v4.1): cola relajada — la IA puede trabajar con lo que haya
        # (desc corta OK si la fuente ya dejó algo); solo excluye desc vacía total
        rows = conn.execute(
            "SELECT group_id, title, company, location, description, modality, salary, "
            "salary_raw, salary_status, salary_note FROM ofertas "
            "WHERE active=1 AND ia_model='' AND (length(description)>200 OR description_source!='') AND "
            "(modality='' OR salary='' OR description IS NULL) "
            "ORDER BY score DESC").fetchall()   # primero las de mejor score (las visibles)
    # max_n=None con groups = TODAS las ofertas del grupo (sin tope de batch);
    # sin groups, el default sigue siendo cfg.ia.batch_size (batch nocturno controlado)
    limit = max_n if (max_n is not None or groups) else cfg.ia.batch_size
    pending = [dict(r) for r in rows][:limit] if limit else [dict(r) for r in rows]
    total = len(pending)
    done = 0
    rate_racha = 0   # OPS-4: circuito nocturno ante tormenta 429/5xx (backoff + corte)
    N = cfg.ia.batch_prompt if cfg.ia.batch_prompt > 1 else 1
    for i in range(0, total, N):
        # /stop: corte limpio ENTRE grupos — lo procesado ya está commiteado
        if stop_event is not None and stop_event.is_set():
            log.warning("batch IA detenido por /stop tras %d/%d", done, total)
            break
        grupo = pending[i:i + N]
        if N > 1:
            arr, err = ia_extract_lote(cfg, grupo, profile_desc, mercado)
            if arr is None:
                if err == "rate":
                    rate_racha += 1
                    if rate_racha >= 10:
                        log.warning("batch IA nocturno: %d fallos 429/5xx seguidos — circuito cortado, "
                                    "resto queda en cola para el próximo batch", rate_racha)
                        break
                    time.sleep(min(60, 5 * rate_racha))
                    continue
                rate_racha = 0
                for r in grupo:   # fallback individual (timeout/other) — §2.5
                    parsed, _ = ia_extract_detail(cfg, r, profile_desc, mercado)
                    if apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version):
                        conn.commit()
                        done += 1
                    time.sleep(0.5)
                continue
            rate_racha = 0
            idxs: dict[int, dict] = {}
            for item in arr:
                try:
                    k = int(item.get("idx") or 0)
                except (TypeError, ValueError):
                    continue
                if k not in idxs:
                    idxs[k] = item
            for j, r in enumerate(grupo, 1):
                if j in idxs:
                    ok = apply_ia_result(conn, cfg, r, idxs[j], ctx_version=ctx_version)
                else:
                    parsed, _ = ia_extract_detail(cfg, r, profile_desc, mercado)
                    ok = apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version)
                if ok:
                    conn.commit()
                    done += 1
                time.sleep(0.5)
            if progress:
                try:
                    progress(min(i + N, total), total, f"lote {i // N + 1}")
                except Exception:
                    pass
        else:
            for r in grupo:
                t0 = time.time()
                parsed, err_kind = ia_extract_detail(cfg, r, profile_desc, mercado)
                log.info("IA %d/%d %s — %s (%.1fs)", i + 1, total, r["group_id"][:20],
                         (r.get("title") or "")[:40], time.time() - t0)
                if err_kind == "rate":
                    rate_racha += 1
                    if rate_racha >= 10:
                        log.warning("batch IA nocturno: %d fallos 429/5xx seguidos — circuito cortado, "
                                    "resto queda en cola para el próximo batch", rate_racha)
                        return done
                    time.sleep(min(60, 5 * rate_racha))
                    continue
                rate_racha = 0
                if progress:
                    try:
                        progress(i + 1, total, r.get("title") or "")
                    except Exception:
                        pass
                if apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version):
                    conn.commit()
                    done += 1
                time.sleep(0.5)
    conn.commit()
    return done