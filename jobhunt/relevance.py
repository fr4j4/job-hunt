"""Filtro de relevancia — gate de entrada para fuentes tipo-feed.

Las fuentes con queries (LinkedIn, Indeed…) ya filtran por construcción:
la query es el filtro. Las fuentes tipo-feed (AIRA: JSON completo del
empleador) traen TODO el catálogo → necesitan este gate antes de indexar.

4 capas (de barata a cara):
  1. Área estructurada del feed (RELEVANCE_AREAS_TECH / _NOUTECH regex)
  2. Frases de QUERIES_*/PROFILE_* en el título (word boundaries)
  2b. Título obvio no-tech (RELEVANCE_NOUNTECH) → rechazo directo
  3. IA en lote solo para lo ambiguo

Todo configurable en .env — agnóstico de sector.
"""
from __future__ import annotations

import re
import time

from .config import Config
from .logging_setup import get_logger

log = get_logger(__name__)

_STOPWORDS = {"de", "en", "del", "la", "el", "los", "las", "para", "con", "y", "a"}


def build_keywords(cfg: Config) -> list[str]:
    """Frases completas de QUERIES_* + PROFILE_TECHS + PROFILE_ROLES (lowercase)."""
    kw: list[str] = []
    seen: set[str] = set()
    def add(s: str):
        s = s.strip().lower()
        if s and len(s) > 2 and s not in seen:
            seen.add(s)
            kw.append(s)
    for tech in cfg.profile.techs:
        add(tech)
    for role in cfg.profile.roles:
        add(role)
    for q in (cfg.search.queries_linkedin + cfg.search.queries_computrabajo +
              cfg.search.queries_indeed + cfg.search.queries_glassdoor +
              cfg.search.queries_laborum + cfg.search.queries_jooble +
              cfg.search.queries_accenture + cfg.search.queries_jooble):
        add(q)
    return kw


def _frase_match(texto: str, frase: str) -> bool:
    """Match de frase con word boundaries (evita 'nifi' ⊂ 'planificación')."""
    return re.search(rf"\b{re.escape(frase)}\b", texto) is not None


def title_is_relevant(title: str, keywords: list[str]) -> bool:
    t = title.lower()
    return any(_frase_match := (re.search(rf"\b{re.escape(k)}\b", t) is not None) for k in keywords)


def area_is_tech(area: str, cfg: Config) -> bool | None:
    """Clasifica el área estructurada del feed. None = área desconocida (seguir a capa 2)."""
    a = (area or "").lower()
    if not a:
        return None
    if re.search(cfg.relevance.areas_tech, a, re.I):
        return True
    if re.search(cfg.relevance.areas_nontech, a, re.I):
        return False
    return None


def title_is_obvious_nontech(title: str, cfg: Config) -> bool:
    return bool(re.search(cfg.relevance.nontech_titles, (title or "").lower(), re.I))


def batch_relevance(items: list[dict], cfg: Config) -> dict[int, bool]:
    """items: [{idx, title, area?}] → {idx: es_tech}. 1 llamada IA por ≤30 items."""
    if not items:
        return {}
    import requests
    try:
        from .market import _ia_call
    except ImportError:
        return {}
    lista = "\n".join(f"[{it['idx']}] {it['title'][:90]}" for it in items)
    cats = ("Full Stack, Backend, Frontend, Data, Mobile, AI/ML, Tech Lead, DevOps/Cloud, "
            "QA, Software, Seguridad, Soporte/TI, Ingeniería no-software, Analista/Empresa, "
            "Profesor/Formación, No-tech")
    prompt = (f"Para cada oferta chilena responde si es una oferta de TECNOLOGÍA/DESARROLLO "
              f"DE SOFTWARE (dev, data, devops, qa, seguridad TI, infra, soporte TI) → tech=true. "
              f"Ventas, guardias, cobranza, logística, RRHH, contabilidad, ingeniería civil/mecánica "
              f"→ tech=false. Oferta ambigua o genérica → tech=false.\n\n"
              f"{lista}\n\nResponde SOLO JSON: "
              f'{{"resultados": [{{"idx": 0, "tech": true}}]}}')
    out = _ia_call(cfg, prompt, temperature=0.0)
    res: dict[int, bool] = {}
    for ritem in (out or {}).get("resultados", []):
        try:
            res[int(ritem["idx"])] = bool(ritem.get("tech"))
        except (KeyError, TypeError, ValueError):
            continue
    return res


def filter_offers(offers: list[dict], cfg: Config, on_progress=None) -> tuple[list[dict], dict]:
    """Gate de relevancia sobre una lista de ofertas de feed.
    Retorna (relevantes, stats{capa: n})."""
    mode = cfg.relevance.mode
    stats = {"area_tech": 0, "area_nontech": 0, "keyword": 0, "ia_tech": 0,
             "ia_nontech": 0, "ambiguo_sin_ia": 0}
    if mode == "off":
        return offers, stats

    keywords = build_keywords(cfg) if mode in ("keywords", "hybrid") else []
    relevantes: list[dict] = []
    ambiguas: list[tuple[int, dict]] = []

    for i, o in enumerate(offers):
        # capa 1: área estructurada
        area_v = area_is_tech(o.get("_aira_area") or o.get("area") or "", cfg)
        if area_v is True:
            relevantes.append(o); stats["area_tech"] += 1; continue
        if area_v is False:
            stats["area_nontech"] += 1; continue
        # capa 2: keywords en título
        if keywords and title_is_relevant(o.get("title") or "", keywords):
            relevantes.append(o); stats["keyword"] += 1; continue
        # capa 1b: título obvio no-tech
        if title_is_obvious_nontech(o.get("title") or "", cfg):
            stats["ia_nontech"] += 1; continue
        ambiguas.append((i, o))

    # capa 3: IA en lote
    if mode in ("ia", "hybrid") and ambiguas and cfg.ia.enabled and cfg.ia.api_key:
        for start in range(0, len(ambiguas), cfg.relevance.ia_batch):
            batch = ambiguas[start:start + cfg.relevance.ia_batch]
            res = batch_relevance([{"idx": k, "title": o.get("title", "")} for k, (i, o) in enumerate(batch)], cfg)
            for k, is_tech in res.items():
                _, o = batch[k]
                if is_tech:
                    relevantes.append(o); stats["ia_tech"] += 1
                else:
                    stats["ia_nontech"] += 1
            if on_progress:
                try:
                    on_progress(start + len(batch), len(ambiguas))
                except Exception:
                    pass
            time.sleep(2)
    else:
        stats["ambiguo_sin_ia"] = len(ambiguas)
        # sin IA: lo ambiguo se descarta (conservador) o se acepta según modo
        if mode == "keywords":
            pass  # descartadas

    return relevantes, stats