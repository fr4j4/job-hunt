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
                  "company": "", "company_linkedin_url": "", "modality_badge": "", "salary": "",
                  "contrato": "", "jornada": "", "techs_desc": []}
    try:
        html = fetch_page(url)
    except Exception as e:
        info["error"] = str(e)[:100]
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
    """Contexto de mercado para el comentario editorial — se calcula UNA vez por lote
    en el MAIN y se pasa como argumento a ia_extract (los workers NO tocan la DB).

    Stats honestas: mediana real (percentil sobre salarios parseados), outliers
    en cuarentena (no se descartan del pool, solo de la estadística), P75 y
    % declarantes calculados — nada hardcodeado.
    """
    try:
        rows = conn.execute(
            "SELECT salary FROM ofertas WHERE active=1 AND salary LIKE 'CLP %' AND salary != ''"
        ).fetchall()
        vals = []
        for (raw,) in rows:
            m = re.search(r"(\d[\d.]*)", raw or "")
            if not m:
                continue
            v = int(m.group(1))
            # cuarentena estadística: <400k o >15M/mes no es salario mensual dev creíble
            if 400_000 <= v <= 15_000_000:
                vals.append(v)
        n_tot = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1").fetchone()[0]
        n_rem = conn.execute(
            "SELECT COUNT(*) FROM ofertas WHERE active=1 AND modality='remoto'").fetchone()[0]
        n_decl = len(vals)
        pct_decl = int(100 * n_decl / n_tot) if n_tot else 0
        if n_decl < 10:
            return (f"muestra salarial insuficiente ({n_decl} ofertas declaran de {n_tot} activas) — "
                    f"NO cites estadísticas de salario en opinion; describe solo la oferta. "
                    f"remoto: {n_rem} de {n_tot} activas")
        vals.sort()
        med = vals[n_decl // 2]
        p75 = vals[min(n_decl - 1, int(n_decl * 0.75))]
        return (f"mediana salarial dev ${med:,} (P75: ${p75:,}) calculada de {n_decl} ofertas "
                f"con sueldo declarado · {pct_decl}% declara · remoto: {n_rem} de {n_tot} activas")
    except Exception:
        return "estadística de mercado no disponible — no cites números de mercado en opinion"


def ia_extract_detail(cfg: Config, job: dict, profile_desc: str,
                      mercado: str = "") -> tuple[dict | None, str]:
    """Igual que ia_extract pero retorna (parsed, err_kind) para el breaker B5:
    '' = ok · 'rate' = 429/5xx (cuenta para el breaker) · 'timeout' · 'other'.
    HTTP PURO — sin SQLite (P0-3). Nunca lanza."""
    if not cfg.ia.enabled or not cfg.ia.api_key:
        return None, "other"
    prompt = (f'Perfil del candidato: {profile_desc}\n\n'
              f'Contexto de mercado (para el campo opinion): {mercado}\n\n'
              f'Oferta:\nTítulo: {job.get("title","")}\nEmpresa: {job.get("company","")}\n'
              f'Ubicación: {job.get("location","")}\n'
              f'Sueldo declarado: {job.get("salary") or "(no declarado — infiere rango de mercado solo si el texto lo permite)"}\n'
              f'Modalidad declarada: {job.get("modality") or "(no declarada)"}\n'
              f'Descripción: {(job.get("description") or "")[:2400]}\n\n'
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
                                     "no compares salarios: describe solo la oferta."},
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


def enrich_pending(conn, cfg: Config, max_n: int | None = None,
                   groups: set[str] | None = None) -> int:
    """Enriquece ofertas activas con descripción corta: Anillo A primero, C si sigue vacío.

    groups: si se pasa, SOLO esas group_id (acota el scope al lote — P1-5 de la
    revisión de arquitectura). Commit POR FICHA: no retiene el write lock minutos
    (P1-5 de la revisión de concurrencia) y un crash no pierde el trabajo previo.
    """
    if groups:
        qs = ",".join("?" for _ in groups)
        rows = conn.execute(
            f"SELECT group_id, title, company, location, url, description FROM ofertas "
            f"WHERE active=1 AND (description IS NULL OR length(description)<200) "
            f"AND group_id IN ({qs})", tuple(groups)).fetchall()
    else:
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
            # AIRA: ficha SPA client-side → browser (el feed no trae desc en ningún formato)
            if "airavirtual.com" in (r.get("url") or ""):
                info = _extract_aira_spa(r["url"])
            else:
                info = extract_structured(r["url"])
        except Exception as e:
            log.warning("enrich falló para %s (%s): %s", r["group_id"], (r.get("title") or "")[:40], e)
            continue
        # CB: ficha redirige a listado genérico = oferta expirada → desactivar
        if info.get("_cb_expired"):
            conn.execute("UPDATE ofertas SET active=0 WHERE group_id=?", (r["group_id"],))
            conn.commit()   # B4: commit por ficha también en la rama expired (DEV-5)
            log.info("CB expirada (redirect a listado): %s — active=0", (r.get("title") or "")[:40])
            done += 1
            time.sleep(1)
            continue
        new_desc = (info.get("description") or "")[:1800]
        extra = (f" · {info['contrato']}" if info.get("contrato") else "") + \
                (f" · {info['jornada']}" if info.get("jornada") else "")
        desc = ((new_desc + extra) if new_desc else r.get("description") or "")[:2000]
        conn.execute("""UPDATE ofertas SET
            description=?,
            company=CASE WHEN company='' OR company IS NULL THEN ? ELSE company END,
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
            (desc, info.get("company") or "", info.get("modality_badge") or "", info.get("salary") or "",
             ";".join(info.get("techs_desc", [])), info.get("date_posted") or "",
             info.get("valid_through") or "", info.get("years_official"),
             info.get("remote_official"), info.get("employment_type") or "",
             "jsonld" if info.get("description") else "section",
             r["group_id"]))
        conn.commit()   # commit POR FICHA — no retiene el write lock (P1-5 conc)
        done += 1
        time.sleep(2)
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


def apply_ia_result(conn, cfg: Config, r: dict, parsed: dict | None) -> bool:
    """Escribe los campos IA de UNA oferta en la DB. Solo el MAIN la llama
    (patrón conexión única). Retorna True si escribió algo. parsed=None → no-op."""
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
    if not r.get("salary") and parsed.get("salario_clp_mensual"):
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
    if groups:
        qs = ",".join("?" for _ in groups)
        rows = conn.execute(
            f"SELECT group_id, title, company, location, description, modality, salary "
            f"FROM ofertas WHERE active=1 AND group_id IN ({qs})",
            tuple(groups)).fetchall()
    else:
        # C9 (v4.1): cola relajada — la IA puede trabajar con lo que haya
        # (desc corta OK si la fuente ya dejó algo); solo excluye desc vacía total
        rows = conn.execute(
            "SELECT group_id, title, company, location, description, modality, salary FROM ofertas "
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
    for i, r in enumerate(pending, 1):
        # /stop: corte limpio ENTRE ofertas — lo procesado ya está commiteado
        if stop_event is not None and stop_event.is_set():
            log.warning("batch IA detenido por /stop tras %d/%d", done, total)
            break
        t0 = time.time()
        parsed, err_kind = ia_extract_detail(cfg, r, profile_desc, mercado)
        log.info("IA %d/%d %s — %s (%.1fs)", i, total, r["group_id"][:20],
                 (r.get("title") or "")[:40], time.time() - t0)
        if err_kind == "rate":
            rate_racha += 1
            if rate_racha >= 10:
                log.warning("batch IA nocturno: %d fallos 429/5xx seguidos — circuito cortado, "
                            "resto queda en cola para el próximo batch", rate_racha)
                break
            time.sleep(min(60, 5 * rate_racha))   # backoff progresivo: 5s→60s
            continue
        rate_racha = 0
        if progress:
            try:
                progress(i, total, r.get("title") or "")
            except Exception:
                pass
        if apply_ia_result(conn, cfg, r, parsed):
            conn.commit()   # libera el lock entre ofertas — el batch tarda minutos
            done += 1
        time.sleep(3)
    conn.commit()
    return done