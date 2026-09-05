"""Fetch + parseo de ficha (Anillo A: JSON-LD estructurado).

Cliente HTTP puro (fetch_page) y parseo puro de HTML ya descargado
(parse_jobposting). Sin dependencias de jobhunt.enrich/db/config.
"""
from __future__ import annotations

import json
import re
from html import unescape as _u

import requests

from ..logging_setup import get_logger

log = get_logger(__name__)


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
    if req.status_code >= 500:            # F1-fetch: 5xx es transitorio, no "ok"
        return html, "error"
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


def parse_jobposting(html: str, url: str) -> dict:
    """Parseo puro (sin red) del HTML ya descargado de una ficha: campos
    oficiales del JSON-LD + badges CB. Fiabilidad 100% cuando hay JSON-LD."""
    info: dict = {"description": "", "date_posted": "", "valid_through": "",
                  "employment_type": "", "years_official": None, "remote_official": 0,
                  "industry": "", "education": "", "applicant_region": "",
                  "company": "", "company_linkedin_url": "", "modality_badge": "", "salary": "",
                  "contrato": "", "jornada": "", "techs_desc": []}

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
    # techs de la desc — ELIMINADO (spec-techs-dev-gate v2): la regex de la ficha
    # ya no se ejecuta aquí. La IA es la única fuente de techs con IA activa;
    # en modo degradado (IA apagada) se usa _extract_techs(title, desc) desde
    # enrich_pending (donde r["title"] existe — P1-2 de auditoría).
    info["techs_desc"] = []
    return info


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
