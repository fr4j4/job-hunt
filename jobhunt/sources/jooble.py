"""Jooble — fuente vía scraping headless (Playwright + Xvfb).

La API REST /api/{key} exige sesión de usuario logueado (403 "sólo usuarios
registrados" con la key sola, incluso con curl-impersonate). El SERP en cambio
renderiza server-side y el browser headless lo resuelve (challenge CF incluido).

Selector validado: h2 a.job_card_link; el texto del contenedor padre trae
empresa/ubicación/fecha/snippet. Paginación: /SearchResult?ukw=<query>&page=N.

Dependencias: playwright (pip) + xvfb (apt). Lanzamiento headed bajo Xvfb
(el challenge CF resuelve mejor que en headless puro).
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone

from ..logging_setup import get_logger
from ..salarios.stats import FLOOR, CEILING
from . import errores

log = get_logger(__name__)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

_EXTRACT_JS = """
[...document.querySelectorAll('h2 a.job_card_link')].map(a => {
  let el = a;
  for (let i = 0; i < 5; i++) {
    el = el.parentElement;
    if (!el) break;
    const t = el.innerText || '';
    if ((t.match(/\\n/g) || []).length >= 3) {
      return {titulo: a.innerText.trim(), url: a.href, texto: t.slice(0, 600)};
    }
  }
  return {titulo: a.innerText.trim(), url: a.href, texto: ''};
})
"""


_RE_REL_ATRAS = re.compile(r"(\d+|un[ao]?)\s+(minutos?|horas?|d[ií]as?|semanas?|mes(?:es)?)\s+atr[aá]s", re.I)


def _parse_card(texto: str) -> dict:
    """Tarjeta del SERP (layout 2026-10): 'Título\n[$monto]\nextracto...\xa0\nReportar\n[empresa]\nciudad\nfecha'.
    Tras 'Reportar' vienen 2 o 3 líneas: [empresa], ubicación y antigüedad ('25 días atrás',
    'Hace un mes'). El monto suelto bajo el título es el sueldo mensual declarado."""
    lineas = [p.strip() for p in texto.split("\n") if p.strip() and p.strip() != "\xa0..."]
    company = location = fecha = ""
    if "Reportar" in lineas:
        cola = lineas[lineas.index("Reportar") + 1:]
        if cola:
            fecha = _fecha_relativa(cola[-1]) or _fecha_absoluta(texto)
            resto = cola[:-1] if _fecha_relativa(cola[-1]) else cola
            if len(resto) >= 2:
                company, location = resto[-2], resto[-1]
            elif len(resto) == 1:
                location = resto[0]
    if not fecha:
        fecha = _fecha_absoluta(texto) or _fecha_relativa(texto)
    snippet = ""
    msnip = re.search(r"\xa0\.\.\.(.{80,})", texto)
    if msnip:
        snippet = msnip.group(1)[:1500]
    elif len(lineas) > 1:      # extracto = la línea más larga entre el título y 'Reportar'
        previas = lineas[1:lineas.index("Reportar")] if "Reportar" in lineas else lineas[1:]
        snippet = max(previas, key=len, default="")[:1500]
    # sueldo: monto suelto bajo el título ('$1600000') o con contexto de sueldo mensual;
    # siempre dentro de la banda física (el regex viejo tomaba cualquier '$')
    salary = ""
    suelto = next((l for l in lineas[1:3] if re.fullmatch(r"\$\s?[\d.]+", l)), "")
    monto_suelto = int(re.sub(r"\D", "", suelto) or 0)
    if suelto and FLOOR <= monto_suelto <= CEILING:
        salary = f"CLP {monto_suelto}"
    else:
        for msal in re.finditer(r"\$\s?(\d{1,3}(?:\.\d{3}){1,2})(?!\d)", texto):
            ctx = texto[max(0, msal.start() - 40): msal.end() + 40].lower()
            monto = int(msal.group(1).replace(".", ""))
            if (FLOOR <= monto <= CEILING and re.search(
                    r"sueldo|salario|renta|remuneraci|l[ií]quid|brut|mensual|al mes|por mes", ctx)
                    and not re.search(r"usd|us\$|d[oó]lar|anual|bono|comisi", ctx)):
                salary = f"CLP {monto}"
                break
    return {"company": company, "location": location, "date": fecha, "snippet": snippet,
            "salary": salary}


def _fecha_relativa(txt: str) -> str:
    """'25 días atrás' → 'hace 25 días' (formato que resuelve db.upsert); 'Hace un mes' pasa tal cual."""
    m = _RE_REL_ATRAS.search(txt or "")
    if m:
        return f"hace {m.group(1)} {m.group(2)}"
    m = re.search(r"(?:hace|publicad[oa] hace)\s+(?:\d+|un[ao]?)\s+(?:minutos?|horas?|d[ií]as?|semanas?|mes(?:es)?)",
                  txt or "", re.I)
    if m:
        return m.group(0)
    return {"hoy": "hoy", "ayer": "ayer"}.get((txt or "").strip().lower(), "")


def _fecha_absoluta(texto: str) -> str:
    mfecha = re.search(r"Publicado el (\d{1,2}) de (\w+)(?:,? (\d{4}))?", texto or "")
    if not mfecha:
        return ""
    meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
             "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
             "noviembre": 11, "diciembre": 12}
    mes = meses.get(mfecha.group(2).lower())
    if not mes:
        return ""
    anio = int(mfecha.group(3) or datetime.now(timezone.utc).year)
    return f"{anio}-{mes:02d}-{int(mfecha.group(1)):02d}"


def jobs(queries: list[str], found_by_prefix: str = "", max_pages: int = 2, on_query=None) -> list[dict]:
    """Ofertas de Jooble Chile vía browser headless. ~25s por página SERP."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("jooble: playwright no instalado "
                    "(pip install playwright && playwright install chromium && apt install xvfb) — saltado")
        return []

    out: dict[str, dict] = {}
    now = datetime.now(timezone.utc)
    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,  # headed bajo Xvfb: el challenge CF resuelve
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=_UA, locale="es-CL", viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        for q in queries:
            if on_query:
                try:
                    on_query(q, 1)
                except Exception:
                    pass
            try:
                page.goto(f"https://cl.jooble.org/SearchResult?ukw={q}",
                          wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(7000)
            except Exception as e:
                log.warning("jooble SERP falló (%s): %s", q[:30], e)
                errores.registrar("jooble")
                continue
            # paginación por SCROLL infinito (el &page=N de la URL es cosmético):
            # cada scroll al fondo carga +20, techo observado ~100
            prev = 0
            for sc in range(max_pages * 2):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2500)
                n = len(page.evaluate("[...document.querySelectorAll('h2 a.job_card_link')]"))
                if on_query and (sc == 0 or n // 20 > prev // 20):
                    try:
                        on_query(q, min(n // 20 + 1, 5))
                    except Exception:
                        pass
                if n == prev:
                    break
                prev = n
            cards = page.evaluate(_EXTRACT_JS)
            fb = f"{found_by_prefix}{q}"
            for c in cards:
                parsed = _parse_card(c.get("texto", ""))
                # dedup por url de redirección (el /away/<id> es estable por oferta)
                m = re.search(r"/away/(-?\d+)", c.get("url", ""))
                uid = m.group(1) if m else c.get("url", "")[:120]
                if not uid or uid in out:
                    continue
                out[uid] = {
                    "title": (c.get("titulo") or "")[:150],
                    "company": parsed["company"],
                    "location": parsed["location"],
                    "date": parsed["date"],   # sin fecha real → vacío (antes: hoy, falseaba la frescura)
                    "url": c.get("url") or "",
                    "source": f"jooble:{q}",
                    "found_by": fb,
                    "salary": parsed.get("salary", ""),
                    "modality": "",
                    "_desc": parsed["snippet"],
                    "description_source": "jooble-serp",
                }
            time.sleep(3)
    except Exception as e:
        log.warning("jooble: error en scraping: %s", e)
        errores.registrar("jooble")
    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
    return list(out.values())