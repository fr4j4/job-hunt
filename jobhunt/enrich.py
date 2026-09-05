# compat: re-export — eliminar en v6 cuando los imports apunten al paquete nuevo
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

import requests

from .config import Config
from .domain.roles import _NONDEV_CATEGORIES  # noqa: F401 (compat: monkeypatch/import viejo)
from .domain.techs import ABBR_BY_ALIAS as _TECH_ABBR  # noqa: F401 (compat)
from .domain.techs import _extract_techs, _TECH_PATTERNS  # noqa: F401 (compat)
from .domain.texto import _norm
from .fetch.page import fetch_page, parse_jobposting, _extract_aira_spa  # noqa: F401 (compat)
from .fetch.page import _jsonld_blocks, _jobposting  # noqa: F401 (compat)
from .ia.client import CloudClient, LocalClient
from .ia.coercion import _coerce_salario, _normalizar_extract_local  # noqa: F401 (compat)
from .ia.prompts import (  # noqa: F401 (compat: los tests leen estos nombres en jobhunt.enrich)
    IA_SCHEMA,
    LOTE_SYSTEM,
    _PROMPT_EXTRACT_LOCAL,
    _PROMPT_OPINION_LOCAL,
    _lote_prompt,
    _prompt_extract_local,
    _prompt_opinion_local,
)
from .ia.schemas import _LOTE_SCHEMA  # noqa: F401 (compat)
from .logging_setup import get_logger
from .salarios.arbiter import SalaryArbitrator

log = get_logger(__name__)


# ============ Anillo A: JSON-LD ============

def extract_structured(url: str) -> dict:
    """Anillo A: campos oficiales del JSON-LD + badges CB. Fiabilidad 100%.
    Usa fetch_page v2: access ∈ {ok, not_found, blocked, error} — el llamador
    (enrich_pending) decide qué hacer según access (§1.4).
    Resuelve fetch_page vía el namespace de este módulo (compat: tests monkeypatchean
    en.fetch_page); el parseo puro vive en jobhunt.fetch.page.parse_jobposting."""
    try:
        html, access = fetch_page(url)
    except Exception as e:
        return {"_access": "error", "error": str(e)[:100]}
    if access != "ok":
        return {"_access": access, "error": f"access={access}"}
    info = parse_jobposting(html, url)
    info["_access"] = access
    return info


# ============ Anillo C: batch IA (deepseek-v4-flash) ============

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


DETAIL_SYSTEM = (
    "Eres un extractor de datos de ofertas de empleo chilenas. "
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
    "omitas. Prohibido comentar anomalías de OTRAS ofertas.")


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
    messages = [{"role": "system",
                 "content": DETAIL_SYSTEM},
                {"role": "user", "content": prompt}]
    data, err = CloudClient(cfg, tag=f"IA {job.get('group_id', '?')}").chat_json(
        messages, extra={"format": "json"})
    if err:
        return None, err   # B5: timeout aislado NO alimenta el breaker
    # DEV-3b: garantiza dict (json.loads acepta list/str)
    return (data, "") if isinstance(data, dict) else (None, "other")


def ia_extract(cfg: Config, job: dict, profile_desc: str, mercado: str = "") -> dict | None:
    """Llama al modelo IA con JSON forzado. Nunca lanza: retorna dict o None."""
    return ia_extract_detail(cfg, job, profile_desc, mercado)[0]


# ============ IA local (spec-ia-local v2.1) — 2 tareas por oferta ============

def _llm_local(cfg: Config, prompt: str) -> tuple[dict | None, str]:
    """Llamada HTTP al endpoint local (Ollama/llama.cpp/vLLM). HTTP puro, sin SQLite.

    Retorna (dict, "") si OK, (None, "timeout"|"rate"|"other") en fallo.
    Connection refused → fallback inmediato (sin esperar timeout — P2-3).
    """
    data, err = LocalClient(cfg).chat_json(
        [{"role": "user", "content": prompt}],
        response_format={"type": "json_object"})
    if err:
        return None, err
    return (data, "") if isinstance(data, dict) else (None, "other")


def ia_extract_local_con_fallback(cfg: Config, job: dict, profile_desc: str,
                                  mercado: str = "") -> tuple[dict | None, str, str]:
    """Local (2 tareas) y, si falla y local_fallback_cloud, cloud individual.
    Retorna (parsed, err_kind, modelo_real) — IA-1: ia_model etiqueta SIEMPRE
    el modelo que produjo el resultado, nunca local_model para un fallback cloud."""
    parsed, err = ia_extract_local(cfg, job, profile_desc, mercado)
    if parsed is not None:
        return parsed, err, cfg.ia.local_model
    if cfg.ia.local_fallback_cloud:
        parsed, err = ia_extract_detail(cfg, job, profile_desc, mercado)
    return parsed, err, cfg.ia.model


def ia_extract_local(cfg: Config, job: dict, profile_desc: str,
                     mercado: str = "") -> tuple[dict | None, str]:
    """TAREA 1 (EXTRACT) + TAREA 2 (OPINION) en 2 llamadas locales.

    Retorna (dict combinado, "") si ambas tareas OK; (None, err_kind) si alguna falla.
    El dict combinado tiene la MISMA forma que ia_extract_detail (incluye ingles).
    """
    if not cfg.ia.local_enabled:
        return None, "other"
    from . import stats as _st
    # TAREA 1 — EXTRACT
    d1, err1 = _llm_local(cfg, _prompt_extract_local(job))
    if d1 is None:
        return None, err1
    d1 = _normalizar_extract_local(d1)
    # TAREA 2 — OPINION (input: datos extraídos + contexto + perfil)
    nota = ""
    sal_esta = _st.parse_salary_clp(job.get("salary") or "")
    if job.get("salary_status") in ("suspect", "implausible") and sal_esta > 0:
        if _st.annual_likely(sal_esta, _st.parse_salary_clp("CLP 2150000") or 2150000):
            hip = f"probable cifra anual (≈ ${sal_esta // 12:,}/mes)"
        else:
            hip = "error de la fuente"
        raw = (job.get("salary_raw") or job.get("salary") or "").strip()
        monto = raw if raw else f"{sal_esta:,}"
        nota = (f"\nNota: el sueldo declarado de esta oferta (${monto}) fue clasificado "
                f"anómalo (motivo: {job.get('salary_note') or 'estadística'}; hipótesis: {hip}) "
                f"— coméntalo en opinion según las reglas.")
    d2, err2 = _llm_local(cfg, _prompt_opinion_local(job, profile_desc, mercado, d1, nota))
    if d2 is None:
        return None, err2
    d1.update(d2)
    return d1, ""


# --- Modo lote (spec-enrich-lotes §2) ---
def ia_extract_lote(cfg: Config, rows: list[dict], profile_desc: str,
                    mercado: str = "") -> tuple[list[dict] | None, str]:
    """Llamada IA por LOTE (spec-enrich-lotes §2). HTTP PURO — sin SQLite.
    Retorna (lista de dicts con idx, o None) + err_kind ('' | 'rate' | 'timeout' | 'other').
    El schema estricto garantiza estructura; el mapeo idx→fila lo hace el llamador."""
    if not cfg.ia.enabled or not cfg.ia.api_key or not rows:
        return None, "other"
    data, err = CloudClient(cfg, tag=f"IA lote ({len(rows)} ofertas)").chat_json(
        [{"role": "system", "content": LOTE_SYSTEM},
         {"role": "user", "content": _lote_prompt(rows, profile_desc, mercado)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "lote_ofertas", "strict": True,
                                         "schema": _LOTE_SCHEMA}})
    if err:
        return None, err
    arr = data.get("ofertas") if isinstance(data, dict) else data
    if not isinstance(arr, list):
        return None, "other"
    arr = [i for i in arr if isinstance(i, dict)]   # IA-3: item basura no aborta el lote
    # normalizar idiomas: el modelo puede devolver strings o dicts (spike verificado)
    for item in arr:
        if isinstance(item.get("idiomas"), list):
            item["idiomas"] = [
                {"idioma": str(i).lower()[:20], "nivel": "", "excluyente": False}
                if isinstance(i, str) else i
                for i in item["idiomas"] if i]
    return arr, ""


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


def _aplicar_ficha(conn, r: dict, info: dict, pool: list[int], cfg: Config | None = None) -> str:
    """Aplica la ficha en el MAIN (árbitro + UPDATE + commit). Retorna 'ok' | 'expired' | 'blocked'.

    cfg: opcional — si cfg.ia.enabled=false (modo degradado), extrae techs con
    _extract_techs(title, desc) (spec-techs-dev-gate §2.1). Con IA activa, la
    regex NO se ejecuta (la IA es la única fuente de techs)."""
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
    dec = SalaryArbitrator(cfg).decide(
        {**r,
         "db_salary": sal_fila["salary"] if sal_fila else "",
         "db_source": sal_fila["salary_source"] if sal_fila else "",
         "db_status": sal_fila["salary_status"] if sal_fila else ""},
        info, pool)
    arb_salary, arb_source = dec.salary, dec.salary_source
    arb_status, arb_note = dec.salary_status, dec.salary_note
    # clasificación estadística con pool cacheado + leave-one-out (spec-enrich-lotes §4)
    # (se queda aquí: los tests parchean _st.classify_salary sobre jobhunt.stats)
    if dec.extra:
        stat_status, stat_note = _st.classify_salary(dec.extra["value"], dec.extra["pool_loo"])
        arb_status, arb_note = stat_status, stat_note or arb_note
    # ---- techs: modo degradado (IA apagada) — spec-techs-dev-gate §2.1 ----
    # Con IA activa, techs_desc viene vacío (la regex de la ficha se eliminó) y
    # el COALESCE preserva el feed; la IA escribe techs vía apply_ia_result.
    techs_join = ";".join(info.get("techs_desc", []))
    if cfg and not cfg.ia.enabled:
        techs_join = ";".join(_extract_techs(r.get("title") or "", info.get("description") or ""))
    # ---- regla §2.5: salario llegó después de la IA → desmarcar (auto-curativo) ----
    if dec.unmark_ia:
        conn.execute("UPDATE ofertas SET ia_model='' WHERE group_id=?", (r["group_id"],))
        log.info("salario llegó después de la IA (%s) — desmarcada para re-enriquecer",
                 (r.get("title") or "")[:40])
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
         techs_join, info.get("date_posted") or "",
         info.get("valid_through") or "", info.get("employment_type") or "",
         info.get("years_official"), info.get("remote_official"),
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
            "salary_raw, salary_status, salary_note, ai_opinion, ia_model")
    if groups:
        qs = ",".join("?" for _ in groups)
        rows = conn.execute(
            f"SELECT {cols} FROM ofertas "
            f"WHERE active=1 AND (description IS NULL OR length(description)<200 OR "
            f"salary_status IN ('implausible','suspect')) "
            f"AND COALESCE(fetch_fails,0) < 3 "
            f"AND group_id IN ({qs}) ORDER BY score DESC", tuple(groups)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM ofertas "
            "WHERE active=1 AND (description IS NULL OR length(description)<200 OR "
            "salary_status IN ('implausible','suspect')) "
            "AND COALESCE(fetch_fails,0) < 3 "
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
    local = bool(cfg and cfg.ia.local_enabled)
    N_fetch = 5 if local else N   # P1-7: fetch agrupado en paralelo aunque la IA sea individual
    done = 0
    for i in range(0, len(pending), N_fetch):
        if stop_event is not None and stop_event.is_set():
            log.info("enrich_pending: stop_event seteado — corte entre grupos")
            break
        grupo = pending[i:i + N_fetch]
        # fetch paralelo: threads SOLO HTTP (P0-3); AIRA serializado (5 chromium = no)
        if len(grupo) > 1 and not any("airavirtual.com" in (r.get("url") or "") for r in grupo):
            with ThreadPoolExecutor(max_workers=min(len(grupo), 5)) as ex:
                infos = list(ex.map(_fetch_ficha, grupo))
        else:
            infos = [_fetch_ficha(r) for r in grupo]
        # el MAIN aplica árbitro + UPDATE por ficha (los threads no tocan la DB)
        vivas = []
        for r, info in zip(grupo, infos):
            estado = _aplicar_ficha(conn, r, info, pool, cfg)
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
            f"salary_raw, salary_status, salary_note, techs FROM ofertas "
            f"WHERE active=1 AND group_id IN ({qs})", tuple(vivas))]
        if not recargadas:
            continue
        if local:
            # spec-ia-local: IA individual (2 tareas) con fallback cloud por oferta
            for r in recargadas:
                parsed, _, modelo = ia_extract_local_con_fallback(cfg, r, perfil, mercado)
                if parsed:
                    apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version, model=modelo)
        elif N > 1:
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
    """Ofertas en cola para el batch IA (con descripción, faltan datos, sin IA).
    C9 ampliado: salarios implausible/suspect SIEMPRE califican (auto-curativo).
    C9 extendido (spec-techs-dev-gate §2.5, P1-6): opinions que dicen "sin salario"
    con salary presente también califican (salario llegó después de la IA)."""
    return conn.execute(
        "SELECT COUNT(*) FROM ofertas WHERE active=1 AND ia_model='' AND "
        "(length(description)>200 OR description_source!='') AND "
        "(modality='' OR salary='' OR description IS NULL OR "
        "salary_status IN ('implausible','suspect') OR "
        "(salary != '' AND (ai_opinion LIKE '%sin salario%' OR ai_opinion LIKE '%sin sueldo%' "
        "OR ai_opinion LIKE '%no declara%' OR ai_opinion LIKE '%carece de datos monetarios%')))"
    ).fetchone()[0]


def apply_ia_result(conn, cfg: Config, r: dict, parsed: dict | None,
                    ctx_version: str = "", model: str | None = None) -> bool:
    """Escribe los campos IA de UNA oferta en la DB. Solo el MAIN la llama
    (patrón conexión única). Retorna True si escribió algo. parsed=None → no-op.

    Guard anti-alucinación (spec-techs-dev-gate §2.2): rol no-software → techs=''
    (la IA no puede contradecirse). Import local de channel — sin ciclo (P2-7).

    ctx_version: hash8 del contexto de mercado usado (spec salarios-robustos §7.4)
    — trazabilidad para regenerar opinions de eras obsoletas.
    model: etiqueta ia_model a escribir (spec-ia-local P1-2) — default cfg.ia.model
    (cloud); en modo local el dispatch pasa cfg.ia.local_model para que la
    trazabilidad diga la verdad.
    Guard A3: SOLO escribe salary si salary_source='' — nunca pisa procedencia
    (feed/text) ni rellena un salary='' que el árbitro vació."""
    if not parsed:
        return False
    # F1/F3: el dict r viene de SELECTs que no traen salary_source/ia_model/ai_*
    # (guard A3 y sanitización de fósiles eran no-op). Se lee la fila fresca UNA
    # vez aquí — todos los callers quedan cubiertos sin tocar la firma.
    fila = conn.execute(
        "SELECT salary, salary_source, modality, ia_model, ai_opinion, ai_resumen, "
        "ai_fit_reason FROM ofertas WHERE group_id=?", (r["group_id"],)).fetchone()
    if fila is not None:
        cols = ("salary", "salary_source", "modality", "ia_model", "ai_opinion",
                "ai_resumen", "ai_fit_reason")
        r = {**dict(r), **dict(zip(cols, tuple(fila)))}
    model = model or parsed.get("_ia_model") or None
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
    sal_ia = _coerce_salario(parsed.get("salario_clp_mensual"))
    if not r.get("salary") and not r.get("salary_source") and sal_ia:
        sets.append("salary=?"); params.append(f"CLP {sal_ia}")
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
        params.append(str(parsed["opinion"])[:300])
        ia_fields.append("opinion")
    if parsed.get("rol_categoria"):
        sets.append("rol_categoria=?")
        params.append(str(parsed["rol_categoria"])[:40])
        ia_fields.append("rol_categoria")
    # techs de la IA: SIEMPRE se regeneran (decisión usuario 2026-09-05) — si la
    # IA detecta [] limpia la columna, no preserva lo existente (una run anterior
    # pudo detectar techs que ya no aplican o alucinadas).
    if "techs" in parsed and isinstance(parsed["techs"], list):
        techs_limpio = []
        for t in parsed["techs"][:8]:
            t = str(t).strip()
            if not t:
                continue
            techs_limpio.append(_TECH_ABBR.get(_norm(t), t[:20]))
        sets.append("techs=?")
        params.append(";".join(techs_limpio))
        ia_fields.append("techs")
    # Guard anti-alucinación (spec-techs-dev-gate §2.2, P1-1): DESPUÉS del REFRESCA
    # — si la IA clasifica el rol como no-software, NO puede haber techs (contradicción).
    # En SQLite gana la ÚLTIMA asignación del SET, así que este append pisa la lista
    # alucinada. No agrega techs a ia_fields (la IA no escribió un valor válido).
    rol_ia = str(parsed.get("rol_categoria") or "").strip()
    if rol_ia in _NONDEV_CATEGORIES:
        sets.append("techs=?")
        params.append("")
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
    sets.append("ia_model=?"); params.append(model or cfg.ia.model)
    sets.append("ia_fields=?"); params.append(",".join(ia_fields))
    if ctx_version:
        sets.append("ctx_version=?"); params.append(ctx_version)
    params.append(r["group_id"])
    conn.execute(f"UPDATE ofertas SET {', '.join(sets)} WHERE group_id=?", params)
    return True


def run_ia_batch(conn, cfg: Config, profile_desc: str, max_n: int | None = None,
                 groups: list[str] | None = None, progress=None,
                 stop_event=None, all_pending: bool = False) -> int:
    """Anillo C: IA para los que A+B no resolvieron. 1x/día (o grupos específicos).

    all_pending=True → TODAS las activas sin IA con descripción (enrich_all —
    incluye las completas, no solo la cola C9).

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
        qs = ",".join("?" * len(groups))
        rows = conn.execute(
            f"SELECT group_id, title, company, location, description, modality, salary, "
            f"salary_raw, salary_status, salary_note, techs FROM ofertas "
            f"WHERE active=1 AND group_id IN ({qs})", list(groups)).fetchall()
    elif all_pending:
        # enrich_all: TODAS las activas sin IA con descripción (incluye completas)
        rows = conn.execute(
            "SELECT group_id, title, company, location, description, modality, salary, "
            "salary_raw, salary_status, salary_note, techs FROM ofertas "
            "WHERE active=1 AND ia_model='' AND "
            "(length(description)>200 OR description_source!='') "
            "ORDER BY score DESC").fetchall()
    else:
        # C9 (v4.1) ampliado: cola relajada — la IA puede trabajar con lo que haya
        # (desc corta OK si la fuente ya dejó algo); solo excluye desc vacía total.
        # Salarios implausible/suspect SIEMPRE califican (auto-curativo — el árbitro
        # intenta corregir con la ficha y la IA comenta la anomalía).
        # C9 extendido (spec-techs-dev-gate §2.5, P1-6): opinions "sin salario" con
        # salary presente también califican (salario llegó después de la IA).
        rows = conn.execute(
            "SELECT group_id, title, company, location, description, modality, salary, "
            "salary_raw, salary_status, salary_note, techs FROM ofertas "
            "WHERE active=1 AND ia_model='' AND (length(description)>200 OR description_source!='') AND "
            "(modality='' OR salary='' OR description IS NULL OR "
            "salary_status IN ('implausible','suspect') OR "
            "(salary != '' AND (ai_opinion LIKE '%sin salario%' OR ai_opinion LIKE '%sin sueldo%' "
            "OR ai_opinion LIKE '%no declara%' OR ai_opinion LIKE '%carece de datos monetarios%'))) "
            "ORDER BY score DESC").fetchall()   # primero las de mejor score (las visibles)
    # max_n=None con groups = TODAS las ofertas del grupo (sin tope de batch);
    # all_pending=True = TODAS las pendientes (enrich_all procesa todo, no 40);
    # sin groups/all_pending, el default sigue siendo cfg.ia.batch_size (batch nocturno controlado)
    limit = max_n if (max_n is not None or groups or all_pending) else cfg.ia.batch_size
    pending = [dict(r) for r in rows][:limit] if limit else [dict(r) for r in rows]
    total = len(pending)
    done = 0
    rate_racha = 0   # OPS-4: circuito nocturno ante tormenta 429/5xx (backoff + corte)
    local = cfg.ia.local_enabled
    N = 1 if local else (cfg.ia.batch_prompt if cfg.ia.batch_prompt > 1 else 1)
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
                modelo = None
                if local:
                    # spec-ia-local: 2 tareas con fallback cloud por oferta
                    parsed, err_kind, modelo = ia_extract_local_con_fallback(
                        cfg, r, profile_desc, mercado)
                else:
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
                if apply_ia_result(conn, cfg, r, parsed, ctx_version=ctx_version, model=modelo):
                    conn.commit()
                    done += 1
                time.sleep(0.5)
    conn.commit()
    return done