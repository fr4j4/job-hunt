"""Análisis de mercado: pipeline de 4 fases → PDF con gráficos + narrativa IA.

Fases (cada una reporta vía callback `on_phase(fase_num, total, msg)`):
  1. agregar     — SQL puro sobre ofertas.sqlite → dict de agregados
  2. graficos    — 6 PNG matplotlib (Agg, headless)
  3. narrativa   — 2 llamadas IA (relato + tldr) con fallback sin IA
  4. pdf         — reportlab platypus compone el informe final

CLI:  python -m jobhunt market   (pipeline completo, sin Telegram)
Bot:  /report                    (mismo pipeline + sendDocument)
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import time
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .logging_setup import get_logger

log = get_logger("jobhunt.market")

# ------------------------------------------------------------------ utilidades

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


ROL_CATEGORIAS = [   # (nombre, regex sobre título normalizado)
    ("Full Stack", r"full.?stack|fullstack"),
    ("Backend", r"backend|back.end"),
    ("Frontend", r"frontend|front.end"),
    ("Data", r"data (engineer|scientist|analyst)|ingenier[oa] de datos|analista de datos|bi\b"),
    ("Mobile", r"mobile|android|\bios\b|react native"),
    ("AI/ML", r"\bai\b|\bml\b|machine learning|inteligencia artificial|\bllm\b|genai|generativ"),
    ("Tech Lead", r"tech lead|lider t[ée]cnic|l[íi]der t[ée]cnic"),
    ("DevOps/Cloud", r"devops|cloud|\bsre\b|platform"),
    ("QA", r"\bqa\b|tester|quality"),
]

REGIONES = [   # (nombre, regex sobre location normalizada)
    ("Santiago", r"santiago|metropolitana|huechuraba|las condes|providencia|quilicura|lampa"),
    ("Valparaíso/Viña", r"valparai|vi[ñn]a"),
    ("Concepción/Ñuble", r"conce|biob|chillan|uble"),
    ("Sur (Los Lagos/Ríos)", r"los lagos|valdivia|puerto (varas|montt)|osorno|rios"),
    ("Temuco/Araucanía", r"temuco|araucan"),
    ("Norte", r"antofagasta|calama|mejillones|atacama|iquique|tarapaca|coquimbo|la serena"),
    ("O'Higgins/Maule", r"o'higgins|maule|rancagua|talca|curico|lima\b"),
]

TRAMOS_SALARIO = [   # (etiqueta, límite inferior CLP)
    ("<$1M", 0), ("$1M-$1.3M", 1_000_000), ("$1.3M-$1.8M", 1_300_000),
    ("$1.8M-$2.3M", 1_800_000), ("$2.3M-$2.8M", 2_300_000),
    ("$2.8M-$3.5M", 2_800_000), ("$3.5M+", 3_500_000),
]


def _extraer_salarios_clp(rows: list[dict], max_n: int) -> list[int]:
    """Extrae rentas CLP mensuales razonables del campo salary (dev-only)."""
    vals: dict[int, str] = {}
    for r in rows:
        if not _norm(r["title"]).__contains__("dev") and not re.search(
            r"software|desarroll|full|backend|front|data|python|java|\.net|devops|"
            r"cloud|qa|mobile|programador|analista programador|tech lead|ingeniero",
            _norm(r["title"])):
            continue
        s = (r["salary"] or "").replace("\xa0", " ")
        for n in re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d{5,8}", s):
            v = int(re.sub(r"[.,]", "", n))
            if 300_000 <= v <= 20_000_000:
                vals.setdefault(v, (r["title"] or "")[:60])
    out = sorted(vals)
    return out[:max_n]


# ==================================================================== FASE 1

def fase_agregar(cfg: Config) -> dict:
    """SQL puro sobre la DB (read-only, con retry por lock). → dict de agregados."""
    db = cfg.db_path
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            con.row_factory = sqlite3.Row
            try:
                return _agregar(con, cfg)
            finally:
                con.close()
        except sqlite3.OperationalError as e:
            last_exc = e
            time.sleep(2 * (attempt + 1))
    raise last_exc  # type: ignore[misc]


def _agregar(con: sqlite3.Connection, cfg: Config) -> dict:
    rows = [dict(r) for r in con.execute("SELECT * FROM ofertas WHERE active=1")]
    n = len(rows)
    agg: dict = {"total": n, "generado": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def pct(x: int) -> int:
        return x * 100 // max(n, 1)

    # fuentes por plataforma
    plat = Counter((r["source"] or "?").split(":")[0] for r in rows)
    agg["fuentes"] = {k: [v, pct(v)] for k, v in plat.most_common()}

    # seniority (campo IA)
    sen = Counter(r["seniority_real"] for r in rows if r["seniority_real"])
    agg["seniority"] = {k: [v, pct(v)] for k, v in sen.most_common()}
    agg["seniority_clasificadas"] = sum(sen.values())

    # modalidad
    mod = Counter(r["modality"] for r in rows if r["modality"])
    agg["modalidad"] = {k: [v, pct(v)] for k, v in mod.most_common()}
    agg["modalidad_sin_dato"] = sum(1 for r in rows if not r["modality"])

    # techs
    tech = Counter()
    for r in rows:
        for t in (r["techs"] or "").split(";"):
            t = t.strip()
            if t:
                tech[t] += 1
    agg["techs"] = {k: [v, pct(v)] for k, v in tech.most_common(15)}

    # categorías de rol por regex de título
    cats: Counter = Counter()
    for r in rows:
        t = _norm(r["title"])
        matched = False
        for nombre, pat in ROL_CATEGORIAS:
            if re.search(pat, t):
                cats[nombre] += 1
                matched = True
                break
        if not matched:
            cats["Otro"] += 1
    agg["roles"] = {k: [v, pct(v)] for k, v in cats.most_common()}

    # salarios
    sal = _extraer_salarios_clp(rows, cfg.report.max_salary_samples)
    agg["salarios"] = {
        "n": len(sal),
        "valores": sal,
        "mediana": int(statistics.median(sal)) if sal else 0,
        "p25": sal[len(sal) // 4] if sal else 0,
        "p75": sal[len(sal) * 3 // 4] if sal else 0,
        "min": sal[0] if sal else 0,
        "max": sal[-1] if sal else 0,
    }

    # empresas
    emp = Counter((r["company"] or "").strip()[:40] for r in rows if (r["company"] or "").strip())
    agg["empresas_top"] = {k: v for k, v in emp.most_common(12)}
    agg["sin_empresa"] = sum(1 for r in rows if not (r["company"] or "").strip())
    agg["sin_empresa_pct"] = pct(agg["sin_empresa"])

    # actividad por día (últimos 14 días)
    hoy = datetime.now(timezone.utc).date()
    dias = Counter()
    for r in rows:
        d = (r["date_posted"] or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}", d):
            dias[d] += 1
    serie = []
    for i in range(13, -1, -1):
        dia = (hoy - timedelta(days=i)).isoformat()
        serie.append([dia[5:], dias.get(dia, 0)])
    agg["actividad"] = serie
    agg["ultimas_48h"] = sum(v for d, v in serie[-2:])

    # red/green flags (campos IA con JSON)
    def _flags(field: str, top: int = 8) -> dict:
        cnt: Counter = Counter()
        for r in rows:
            raw = (r[field] or "").strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
                items = parsed if isinstance(parsed, list) else [str(parsed)]
            except Exception:
                items = re.split(r"[;|\n]", raw)
            for it in items:
                x = it.strip().strip('"').lower()
                if len(x) > 6:
                    cnt[x[:70]] += 1
        return dict(cnt.most_common(top))

    agg["red_flags"] = _flags("ai_red_flags")
    agg["green_flags"] = _flags("ai_green_flags")
    agg["beneficios"] = _flags("ai_benefits", 6)

    # inglés (IA)
    ing = Counter(r["ai_ingles"] for r in rows if r["ai_ingles"])
    agg["ingles"] = {k: v for k, v in ing.most_common()}

    # geografía
    locs: Counter = Counter()
    for r in rows:
        l = _norm(r["location"])
        for nombre, pat in REGIONES:
            if re.search(pat, l):
                locs[nombre] += 1
                break
        else:
            locs["otro/sin dato"] += 1
    agg["geografia"] = {k: [v, pct(v)] for k, v in locs.most_common()}

    # reposteo
    agg["reposteadas"] = sum(1 for r in rows if (r["occurrences"] or 1) >= 2)
    agg["reposteadas_pct"] = pct(agg["reposteadas"])

    # meta para el PDF
    agg["ia_model"] = next(
        (r["ia_model"] for r in reversed(rows) if r["ia_model"]), "—")
    row = con.execute("SELECT score_version FROM ofertas WHERE active=1 AND score_version != '' "
                      "ORDER BY last_seen DESC LIMIT 1").fetchone()
    agg["score_version"] = row[0] if row else "—"
    return agg


# ==================================================================== FASE 2

def fase_graficos(cfg: Config, agg: dict, out_dir: Path) -> list[Path]:
    """6 PNG con matplotlib Agg. Retorna los paths generados."""
    import matplotlib
    matplotlib.use("Agg")          # antes de pyplot: headless-safe
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": "#1e1e2e", "axes.facecolor": "#1e1e2e",
        "axes.edgecolor": "#585b70", "text.color": "#cdd6f4",
        "axes.labelcolor": "#cdd6f4", "xtick.color": "#a6adc8",
        "ytick.color": "#a6adc8", "font.size": 10,
        "axes.titleweight": "bold", "figure.dpi": 150,
    })
    AZUL = "#89b4fa"; VERDE = "#a6e3a1"; ROJO = "#f38ba8"; AMAR = "#f9e2af"
    VIOLETA = "#cba6f7"; TEAL = "#94e2d5"

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    def _barh(pairs: list[tuple], fname: str, titulo: str, color: str, xlabel: str = ""):
        if not pairs:
            return _sin_datos(out_dir, fname, titulo)
        labels = [p[0] for p in pairs][::-1]
        vals = [p[1] for p in pairs][::-1]
        fig, ax = plt.subplots(figsize=(7, max(2.2, 0.45 * len(labels) + 0.8)))
        bars = ax.barh(labels, vals, color=color, edgecolor="none")
        vmax = max(vals) or 1
        for b, v in zip(bars, vals):
            ax.text(min(v + vmax * 0.02, vmax * 1.02), b.get_y() + b.get_height() / 2,
                    f" {v:,}".replace(",", "."), va="center", fontsize=9)
        ax.set_title(titulo)
        ax.set_xlabel(xlabel)
        ax.spines[["top", "right"]].set_visible(False)
        ax.margins(x=0.12)
        fig.tight_layout()
        p = out_dir / fname
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)
        return p

    def _sin_datos(out_dir: Path, fname: str, titulo: str):
        fig, ax = plt.subplots(figsize=(7, 2.2))
        ax.axis("off")
        ax.text(0.5, 0.5, "sin datos suficientes", ha="center", va="center",
                fontsize=13, style="italic", transform=ax.transAxes)
        ax.set_title(titulo)
        p = out_dir / fname
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)
        return p

    # 1. seniority
    orden = {"senior": 0, "semi": 1, "lead": 2, "junior": 3}
    sen = sorted(agg["seniority"].items(),
                 key=lambda kv: orden.get(kv[0], 9))
    labels = {"senior": "Senior", "semi": "Semi-senior", "lead": "Lead", "junior": "Junior"}
    _barh([(labels.get(k, k.title()), v[0]) for k, v in sen],
          "seniority.png", "Seniority de las ofertas", AZUL, "ofertas")

    # 2. techs
    _barh([(k, v[1]) for k, v in agg["techs"].items()],
          "techs.png", "Tecnologías más pedidas", VERDE, "% del pool")

    # 3. roles
    _barh([(k, v[0]) for k, v in agg["roles"].items() if k != "Otro"],
          "roles.png", "Demanda por tipo de rol", VIOLETA, "ofertas")

    # 4. salarios (histograma de tramos + mediana)
    sal = agg["salarios"]
    if sal["n"] >= 3:
        tramos = Counter()
        for v in sal["valores"]:
            for et, inf in reversed(TRAMOS_SALARIO):
                if v >= inf:
                    tramos[et] += 1
                    break
        orden_t = [t[0] for t in TRAMOS_SALARIO]
        pairs = [(t, tramos.get(t, 0)) for t in orden_t if t in tramos]
        _barh(pairs, "salarios.png",
              f"Rentas declaradas (mediana ${sal['mediana']:,})".replace(",", "."),
              AMAR, "ofertas")
    else:
        _sin_datos(out_dir, "salarios.png", "Rentas declaradas")

    # 5. actividad 14 días
    act = agg["actividad"]
    if act and sum(v for _, v in act) > 0:
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.plot([d for d, _ in act], [v for _, v in act],
                color=TEAL, marker="o", markersize=4, linewidth=1.8)
        ax.fill_between([d for d, _ in act], [v for _, v in act], alpha=0.15, color=TEAL)
        ax.set_title("Ofertas publicadas por día (14 días)")
        ax.spines[["top", "right"]].set_visible(False)
        ax.margins(y=0.15)
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)
        fig.tight_layout()
        p = out_dir / "actividad.png"
        fig.savefig(p, bbox_inches="tight")
        plt.close(fig)
        paths.append(p)
    else:
        _sin_datos(out_dir, "actividad.png", "Ofertas publicadas por día")

    # 6. empresas
    _barh([(k, v) for k, v in agg["empresas_top"].items()],
          "empresas.png", "Empresas con más ofertas activas", ROJO, "ofertas")

    return paths


# ==================================================================== FASE 3

REPORT_STORY_SCHEMA = ('{"relato": "6-8 párrafos en texto plano orientado a personas que buscan '
                       'empleo en el mercado tech chileno", "consejos": ["consejo práctico accionable", "..."], '
                       '"tldr": ["bullet 1", "bullet 2", "bullet 3", "bullet 4"]}')


def _ia_call(cfg: Config, prompt: str, temperature: float = 0.3) -> dict | None:
    """LLM con JSON forzado — mismo endpoint que ia_extract, temperature narrativa."""
    import requests
    try:
        req = requests.post(
            f"{cfg.ia.base_url}/chat/completions",
            json={"model": cfg.ia.model,
                  "messages": [
                      {"role": "system",
                       "content": "Eres un analista de mercado laboral tech chileno. Escribes para "
                                  "personas que buscan empleo: tono directo, segunda persona, sin jerga "
                                  "estadística, números incrustados en el texto. Respondes SOLO JSON válido."},
                      {"role": "user", "content": prompt}],
                  "temperature": temperature, "format": "json"},
            timeout=cfg.ia.timeout,
            headers={"Authorization": f"Bearer {cfg.ia.api_key}", "Content-Type": "application/json"})
        content = req.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        log.warning("IA narrativa falló: %s", e)
        return None


def _resumen_para_ia(agg: dict) -> str:
    """JSON compacto (~2KB) con lo esencial para la narrativa."""
    compacto = {
        "total_ofertas": agg["total"],
        "ultimas_48h": agg["ultimas_48h"],
        "fuentes": agg["fuentes"],
        "seniority": agg["seniority"],
        "modalidad": agg["modalidad"],
        "techs_top10": {k: v[1] for k, v in list(agg["techs"].items())[:10]},
        "roles": agg["roles"],
        "salarios": {k: v for k, v in agg["salarios"].items() if k != "valores"},
        "empresas_top": agg["empresas_top"],
        "sin_empresa_pct": agg["sin_empresa_pct"],
        "actividad_reciente": agg["actividad"][-7:],
        "red_flags_top5": dict(list(agg["red_flags"].items())[:5]),
        "green_flags_top5": dict(list(agg["green_flags"].items())[:5]),
        "ingles": agg["ingles"],
        "geografia": agg["geografia"],
        "reposteadas_pct": agg["reposteadas_pct"],
    }
    return json.dumps(compacto, ensure_ascii=False)


def _fallback_narrativa(agg: dict) -> dict:
    """Plantilla determinista si la IA no responde."""
    sal = agg["salarios"]
    top_tech = next(iter(agg["techs"]), "—")
    top_rol = next(iter(agg["roles"]), "—")
    relato = (
        f"El mercado tech chileno registra {agg['total']} ofertas activas al momento de este "
        f"análisis, con {agg['ultimas_48h']} publicadas en las últimas 48 horas: alta rotación, "
        f"conviene postular rápido.\n\n"
        f"El perfil dominante es {top_rol} y la tecnología más pedida es {top_tech}. "
        f"El mercado está estructurado para experiencia: "
        f"{agg['seniority'].get('senior', [0, 0])[0]} ofertas senior y solo "
        f"{agg['seniority'].get('junior', [0, 0])[0]} junior.\n\n"
        f"Sobre renta: mediana de ${sal['mediana']:,} líquido (n={sal['n']}). "
        f"El tramo central va de ${sal['p25']:,} a ${sal['p75']:,}.".replace(",", ".")
    )
    return {
        "relato": relato,
        "consejos": [
            "Postula dentro de las primeras 48 horas de publicada la oferta.",
            "Verifica que la oferta declare empresa, modalidad y salario antes de invertir tiempo.",
            "El inglés certificado abre el tramo de renta superior ($3M+).",
            "Si estás en regiones, prioriza ofertas remotas: la oferta local presencial senior es escasa.",
        ],
        "tldr": [
            f"{agg['total']} ofertas activas · {agg['ultimas_48h']} en las últimas 48 hrs",
            f"Mediana salarial ${sal['mediana']:,} líquido".replace(",", "."),
            f"Top tech: {top_tech} · Top rol: {top_rol}",
            "El salto de renta real está en remoto internacional con inglés.",
        ],
    }


def fase_narrativa(cfg: Config, agg: dict) -> tuple[dict, bool]:
    """Relato + consejos + tldr. Retorna (texto, ia_ok)."""
    if not cfg.report.ia_narrative or not cfg.ia.enabled or not cfg.ia.api_key:
        return _fallback_narrativa(agg), False
    resumen = _resumen_para_ia(agg)
    prompt = (f"Datos del pool de ofertas (JSON):\n{resumen}\n\n"
              f"Escribe el análisis para personas que buscan empleo tech en Chile. "
              f"Responde SOLO JSON: {REPORT_STORY_SCHEMA}")
    out = _ia_call(cfg, prompt)
    if not out or not out.get("relato"):
        return _fallback_narrativa(agg), False
    out.setdefault("consejos", [])
    out.setdefault("tldr", [])
    return out, True


# ==================================================================== FASE 4

def _fmt_clp(v: int) -> str:
    return f"${v:,}".replace(",", ".")


def fase_pdf(cfg: Config, agg: dict, narr: dict, ia_ok: bool,
             charts: list[Path], out_pdf: Path) -> Path:
    """Compone el informe con reportlab platypus."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, Image, PageBreak, Paragraph,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    styles = getSampleStyleSheet()
    st_h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=20, spaceAfter=4)
    st_sub = ParagraphStyle("sub", parent=styles["Normal"], fontSize=10,
                            textColor=colors.HexColor("#555555"))
    st_h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13,
                           spaceBefore=12, spaceAfter=4,
                           textColor=colors.HexColor("#1a3c6e"))
    st_p = ParagraphStyle("p", parent=styles["Normal"], fontSize=9.5, leading=13.5)
    st_li = ParagraphStyle("li", parent=st_p, leftIndent=14, bulletIndent=4)
    st_small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,
                              textColor=colors.HexColor("#666666"))
    st_warn = ParagraphStyle("warn", parent=st_p, textColor=colors.HexColor("#a05a00"))

    GRID = TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    def tabla(data: list[list], widths: list | None = None) -> Table:
        t = Table(data, colWidths=widths, hAlign="LEFT")
        t.setStyle(GRID)
        return t

    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        title="Análisis de mercado tech — jobhunt",
    )
    story: list = []

    # ---- portada
    ahora = datetime.now(timezone.utc)
    story += [
        Paragraph("Análisis de mercado tech en Chile", styles["Title"]),
        Paragraph(f"{agg['total']} ofertas activas · {ahora.strftime('%d/%m/%Y %H:%M UTC')} · "
                  f"modelo IA: {agg['ia_model']} · criterio: {agg['score_version']}", st_sub),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1a3c6e"), spaceAfter=8),
    ]
    if not ia_ok:
        story.append(Paragraph("⚠️ Narrativa generada sin IA (modelo no disponible) — "
                               "solo datos duros.", st_warn))

    # ---- resumen ejecutivo
    story.append(Paragraph("Resumen ejecutivo", st_h2))
    for b in narr.get("tldr", []):
        story.append(Paragraph(f"• {b}", st_li))
    if not narr.get("tldr"):
        story.append(Paragraph("Sin resumen disponible.", st_p))

    # ---- helper sección con gráfico
    charts_map = {p.stem: p for p in charts}

    def seccion(titulo: str, parrafos: list[str], filas: list[list] | None = None,
                bullets: list[str] | None = None, chart: str | None = None,
                widths: list | None = None):
        story.append(Paragraph(titulo, st_h2))
        for p in parrafos:
            if p:
                story.append(Paragraph(p, st_p))
        if filas:
            story.append(Spacer(1, 4))
            story.append(tabla(filas, widths))
        if bullets:
            for b in bullets:
                story.append(Paragraph(f"• {b}", st_li))
        if chart and chart in charts_map:
            story.append(Spacer(1, 6))
            story.append(Image(str(charts_map[chart]), width=14.5 * cm, height=8.5 * cm,
                               kind="proportional"))

    # ---- secciones (cada una: narrativa si IA + datos + gráfico)
    relato_pars = [p.strip() for p in narr.get("relato", "").split("\n\n") if p.strip()]
    story.append(Paragraph("El mercado hoy", st_h2))
    for p in relato_pars[:3]:
        story.append(Paragraph(p, st_p))

    fuente_str = ", ".join(f"{k} {v[0]} ({v[1]}%)" for k, v in agg["fuentes"].items())
    seccion("Actividad y fuentes",
            [relato_pars[3] if len(relato_pars) > 3 else "",
             f"Volumen por plataforma: {fuente_str}. "
             f"{agg['reposteadas_pct']}% de las ofertas se re-publica 2+ veces."],
            filas=[["Día", "Ofertas"] + [] ] and [["Día", "Ofertas"]] + agg["actividad"][-7:],
            chart="actividad")

    seccion("Roles más buscados",
            [relato_pars[4] if len(relato_pars) > 4 else ""],
            filas=[["Rol", "Ofertas", "%"]] + [[k, v[0], f"{v[1]}%"] for k, v in agg["roles"].items()],
            chart="roles")

    sen_lbl = {"senior": "Senior", "semi": "Semi-senior", "lead": "Lead", "junior": "Junior"}
    seccion("Seniority",
            [relato_pars[5] if len(relato_pars) > 5 else "",
             f"Clasificación IA disponible para {agg['seniority_clasificadas']} ofertas."],
            filas=[["Nivel", "Ofertas", "%"]] +
                  [[sen_lbl.get(k, k), v[0], f"{v[1]}%"] for k, v in agg["seniority"].items()],
            chart="seniority")

    seccion("Tecnologías",
            [relato_pars[6] if len(relato_pars) > 6 else ""],
            filas=[["Tech", "Ofertas", "% del pool"]] +
                  [[k, v[0], f"{v[1]}%"] for k, v in list(agg["techs"].items())[:12]],
            chart="techs")

    sal = agg["salarios"]
    seccion("Rentas declaradas",
            [relato_pars[7] if len(relato_pars) > 7 else "",
             f"Mediana {_fmt_clp(sal['mediana'])} · rango central {_fmt_clp(sal['p25'])}–"
             f"{_fmt_clp(sal['p75'])} (n={sal['n']}). Solo ofertas que declaran renta."],
            filas=[["Métrica", "CLP líquido/mes"],
                   ["Mínimo observado", _fmt_clp(sal["min"])],
                   ["P25", _fmt_clp(sal["p25"])],
                   ["Mediana", _fmt_clp(sal["mediana"])],
                   ["P75", _fmt_clp(sal["p75"])],
                   ["Máximo observado", _fmt_clp(sal["max"])]],
            chart="salarios")

    seccion("Empresas que contratan",
            [f"{agg['sin_empresa']} ofertas ({agg['sin_empresa_pct']}%) no muestran el nombre "
             f"de la empresa — desconfía si te contactan sin identificarla."],
            filas=[["Empresa", "Ofertas"]] + [[k, v] for k, v in agg["empresas_top"].items()],
            chart="empresas")

    geo = " · ".join(f"{k}: {v[1]}%" for k, v in agg["geografia"].items())
    mod = " · ".join(f"{k}: {v[1]}%" for k, v in agg["modalidad"].items()) or "sin datos"
    ing = " · ".join(f"{k}: {v}" for k, v in agg["ingles"].items()) or "sin datos"
    seccion("Geografía, modalidad e inglés",
            [f"Distribución geográfica: {geo}.",
             f"Modalidad (donde hay dato): {mod}.",
             f"Inglés según IA: {ing}."])

    if agg["red_flags"]:
        seccion("Red flags frecuentes", [],
                bullets=[f"🚩 {k} ({v}×)" for k, v in agg["red_flags"].items()])
    if agg["green_flags"]:
        seccion("Green flags", [],
                bullets=[f"✅ {k} ({v}×)" for k, v in agg["green_flags"].items()])

    if narr.get("consejos"):
        seccion("Consejos prácticos", [],
                bullets=[f"→ {c}" for c in narr["consejos"]])

    # ---- metodología
    story.append(Paragraph("Metodología y limitaciones", st_h2))
    story.append(Paragraph(
        f"Análisis generado automáticamente por jobhunt sobre {agg['total']} ofertas activas "
        f"recolectadas de LinkedIn, Computrabajo, Indeed y Glassdoor. La muestra está sesgada "
        f"por las búsquedas configuradas (perfiles de software). Solo {sal['n']} ofertas declaran "
        f"renta; los rangos reflejan lo visible, no el mercado completo. Narrativa IA: "
        f"{'generada con ' + agg['ia_model'] if ia_ok else 'plantilla sin IA'}. "
        f"No reemplaza la negociación individual.", st_small))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"generado por jobhunt /report · {ahora.strftime('%Y-%m-%d %H:%M UTC')}",
                           st_small))

    doc.build(story)
    return out_pdf


# ==================================================================== pipeline

def run_market_pipeline(cfg: Config, on_phase=None) -> tuple[Path, dict, bool]:
    """Pipeline completo. on_phase(n, total, msg) opcional. → (pdf_path, narr, ia_ok)"""
    def phase(n: int, msg: str):
        if on_phase:
            try:
                on_phase(n, msg)
            except Exception:
                pass

    total_f = 4
    phase(1, "agregando pool")
    agg = fase_agregar(cfg)
    phase(1, f"pool agregado: {agg['total']} ofertas")

    phase(2, f"generando gráficos ({agg['total']} ofertas)")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_dir = cfg.report.out_dir / f"charts_{stamp}"
    charts = fase_graficos(cfg, agg, out_dir)
    phase(2, f"{len(charts)} gráficos listos")

    phase(3, "narrativa IA")
    narr, ia_ok = fase_narrativa(cfg, agg)
    phase(3, "narrativa lista" if ia_ok else "IA no disponible — plantilla")

    phase(4, "componiendo PDF")
    out_pdf = cfg.report.out_dir / f"mercado_{stamp}.pdf"
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    fase_pdf(cfg, agg, narr, ia_ok, charts, out_pdf)
    phase(4, f"PDF listo: {out_pdf.name}")

    return out_pdf, narr, ia_ok


def highlights(agg: dict, narr: dict) -> str:
    """Caption de 200 chars para el sendDocument."""
    sal = agg["salarios"]
    top_tech = next(iter(agg["techs"]), "—")
    return (f"{agg['total']} ofertas · mediana {_fmt_clp(sal['mediana'])} · "
            f"top tech: {top_tech} · {agg['ultimas_48h']} publicadas en 48 hrs")[:200]