"""Gráficos para el canal (digests con PNG nativos de Telegram).

Reusa el estilo dark de market.py (matplotlib Agg, headless-safe).
Cada función recibe conn y retorna el Path del PNG (o None si no hay datos).
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from .scoring import _salary_to_clp_monthly, _norm, _MARKET_TECHS_TITLE_RE

_ROL_LABELS = {
    "Full Stack": "Full Stack", "Backend": "Backend", "Frontend": "Frontend",
    "Data": "Data", "Mobile": "Mobile", "AI/ML": "AI/ML", "Tech Lead": "Tech Lead",
    "DevOps/Cloud": "DevOps/Cloud", "QA": "QA", "Software": "Software",
    "Seguridad": "Seguridad", "Otro": "Otro",
}
_SEN_LABELS = {"senior": "Senior", "semi": "Semi-senior", "lead": "Lead", "junior": "Junior"}

# Normalización de techs (columna abreviada + títulos libres → nombre canónico)
_TECH_CANON = {
    "py": "Python", "python": "Python",
    "ts": "TypeScript", "typescript": "TypeScript",
    "js": "JavaScript", "javascript": "JavaScript",
    "k8s": "Kubernetes", "kubernetes": "Kubernetes",
    "tf": "Terraform", "terraform": "Terraform",
    "golang": "Go", "go": "Go",
    "node": "Node.js", "node.js": "Node.js",
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
    "mongo": "MongoDB", "mongodb": "MongoDB",
    "react": "React", "angular": "Angular", "spring": "Spring",
    "aws": "AWS", "gcp": "GCP", "azure": "Azure",
    "docker": "Docker", "scala": "Scala", "java": "Java",
    "sql": "SQL", "nifi": "NiFi", "kafka": "Kafka",
    "fastapi": "FastAPI", "redis": "Redis", "vue": "Vue",
    ".net": ".NET", "c#": "C#", "c++": "C++", "php": "PHP",
    "ci/cd": "CI/CD", "jenkins": "Jenkins", "git": "Git",
    "linux": "Linux", "bash": "Bash", "airflow": "Airflow",
    "spark": "Spark", "hadoop": "Hadoop", "snowflake": "Snowflake",
    "databricks": "Databricks", "tableau": "Tableau", "power bi": "Power BI",
    "sap": "SAP", "abap": "ABAP", "cobol": "COBOL",
}


def _techs_de_fila(r: dict) -> set[str]:
    """Techs de una oferta: columna techs (abreviada) + títulos (libres), normalizadas."""
    out = set()
    for t in (r.get("techs") or "").split(";"):
        t = t.strip()
        if t:
            out.add(_TECH_CANON.get(_norm(t), t))
    for m in _MARKET_TECHS_TITLE_RE.findall(r.get("title") or ""):
        out.add(_TECH_CANON.get(_norm(m), m))
    return out


def _setup_plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": "#1e1e2e", "axes.facecolor": "#1e1e2e",
        "axes.edgecolor": "#585b70", "text.color": "#cdd6f4",
        "axes.labelcolor": "#cdd6f4", "xtick.color": "#a6adc8",
        "ytick.color": "#a6adc8", "font.size": 10,
        "axes.titleweight": "bold", "figure.dpi": 150,
    })
    return plt


def _barh(plt, pairs: list[tuple], fname: str, titulo: str, color: str,
          xlabel: str = "", out_dir: Path | None = None) -> Path | None:
    """Barras horizontales con etiqueta de valor. Retorna Path o None si vacío."""
    if not pairs:
        return None
    out_dir = out_dir or Path("/tmp")
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
    return p


def chart_salarios_por_rol(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """B1: mediana salarial por rol (solo roles con n>=3 declarados)."""
    rows = conn.execute(
        "SELECT rol_categoria, salary, description FROM ofertas "
        "WHERE active=1 AND salary != ''").fetchall()
    por_rol: dict[str, list[int]] = {}
    for rc, sal, desc in rows:
        v = _salary_to_clp_monthly(sal or "", desc or "")
        if v:
            por_rol.setdefault(rc or "Otro", []).append(v)
    pairs = []
    for rc, vals in por_rol.items():
        if len(vals) < 3:
            continue
        vals.sort()
        med = vals[len(vals) // 2]
        label = _ROL_LABELS.get(rc, rc.title()[:18])
        pairs.append((f"{label} (n={len(vals)})", med))
    if not pairs:
        return None
    pairs.sort(key=lambda kv: -kv[1])
    plt = _setup_plt()
    return _barh(plt, pairs, "salarios_por_rol.png",
                 "Mediana salarial por rol (CLP/mes)", "#f9e2af", "CLP", out_dir)


def chart_ofertas_por_rol(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """B2a: ofertas activas por rol (con IA — rol_categoria lleno)."""
    rows = conn.execute(
        "SELECT rol_categoria, COUNT(*) c FROM ofertas "
        "WHERE active=1 AND ia_model != '' AND rol_categoria != '' "
        "GROUP BY rol_categoria ORDER BY c DESC").fetchall()
    pairs = [(_ROL_LABELS.get(rc, rc.title()[:18]), c) for rc, c in rows if c > 0]
    if not pairs:
        return None
    plt = _setup_plt()
    return _barh(plt, pairs[:10], "ofertas_por_rol.png",
                 "Ofertas activas por rol", "#cba6f7", "ofertas", out_dir)


def chart_seniority_mix(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """B2b: mix de seniority (junior/semi/senior/lead)."""
    rows = conn.execute(
        "SELECT seniority_real, COUNT(*) c FROM ofertas "
        "WHERE active=1 AND seniority_real != '' GROUP BY seniority_real").fetchall()
    orden = {"senior": 0, "semi": 1, "lead": 2, "junior": 3}
    pairs = []
    for sen, c in rows:
        key = _norm(sen)
        label = _SEN_LABELS.get(key, sen.title()[:14])
        pairs.append((label, c))
    pairs.sort(key=lambda kv: orden.get(_norm(kv[0]), 9))
    if not pairs:
        return None
    plt = _setup_plt()
    return _barh(plt, pairs, "seniority_mix.png",
                 "Seniority de las ofertas activas", "#89b4fa", "ofertas", out_dir)


def chart_actividad(conn: sqlite3.Connection, out_dir: Path, dias: int = 14) -> Path | None:
    """B3: ofertas nuevas por día (date_canonical, últimos N días)."""
    rows = conn.execute(
        "SELECT date_canonical, COUNT(*) c FROM ofertas "
        "WHERE active=1 AND date_canonical >= date('now', ?) "
        "GROUP BY date_canonical ORDER BY date_canonical", (f"-{dias} days",)).fetchall()
    if not rows:
        return None
    from datetime import date, timedelta
    hoy = date.today()
    fechas = [(hoy - timedelta(days=i)).isoformat() for i in range(dias - 1, -1, -1)]
    vals = {f: 0 for f in fechas}
    for f, c in rows:
        if f in vals:
            vals[f] = c
    plt = _setup_plt()
    fig, ax = plt.subplots(figsize=(7, 2.6))
    xs = list(range(len(fechas)))
    ax.bar(xs, [vals[f] for f in fechas], color="#a6e3a1", edgecolor="none")
    ax.set_title(f"Ofertas nuevas por día (últimos {dias} días)")
    ax.set_xticks(xs[::2])
    ax.set_xticklabels([f[5:] for f in fechas[::2]], rotation=45, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    p = out_dir / "actividad.png"
    fig.savefig(p, bbox_inches="tight")
    plt.close(fig)
    return p


def chart_modalidad(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """B4: mix de modalidad (remoto/híbrido/presencial/sin dato)."""
    rows = conn.execute(
        "SELECT modality, COUNT(*) c FROM ofertas WHERE active=1 GROUP BY modality").fetchall()
    c = Counter()
    for mod, n in rows:
        m = _norm(mod)
        if "remot" in m:
            c["Remoto"] += n
        elif "híbrid" in m or "hibrid" in m:
            c["Híbrido"] += n
        elif "presencial" in m:
            c["Presencial"] += n
        else:
            c["Sin dato"] += n
    total = sum(c.values()) or 1
    pairs = [(k, 100 * v // total) for k, v in c.items() if v > 0]
    if not pairs:
        return None
    plt = _setup_plt()
    return _barh(plt, pairs, "modalidad.png",
                 "Modalidad de las ofertas activas (%)", "#94e2d5", "%", out_dir)


# ============ reporte de tecnologías (T1-T3) ============

def _techs_pool(conn: sqlite3.Connection, dias: int = 30) -> list[dict]:
    """Ofertas activas de los últimos N días (para el reporte de techs)."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM ofertas WHERE active=1 AND date_canonical >= date('now', ?)",
        (f"-{dias} days",)).fetchall()]


def _frecuencia_techs(rows: list[dict]) -> Counter:
    c = Counter()
    for r in rows:
        c.update(_techs_de_fila(r))
    return c


def chart_techs_top(conn: sqlite3.Connection, out_dir: Path, dias: int = 30,
                    top: int = 12) -> Path | None:
    """T1: top tecnologías más pedidas (columna + títulos, normalizadas)."""
    rows = _techs_pool(conn, dias)
    c = _frecuencia_techs(rows)
    pairs = [(t, n) for t, n in c.most_common(top) if n > 0]
    if not pairs:
        return None
    plt = _setup_plt()
    return _barh(plt, pairs, "techs_top.png",
                 f"Tecnologías más pedidas (últimos {dias} días)", "#a6e3a1",
                 "ofertas", out_dir)


def chart_techs_emergentes(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """T2: techs emergentes — frecuencia últimos 7d vs 8-30d (crecimiento)."""
    from datetime import date, timedelta
    rows = _techs_pool(conn, 30)
    hoy = date.today()
    recientes, anteriores = Counter(), Counter()
    for r in rows:
        try:
            d = date.fromisoformat((r.get("date_canonical") or "")[:10])
        except Exception:
            continue
        techs = _techs_de_fila(r)
        if d >= hoy - timedelta(days=7):
            recientes.update(techs)
        elif d >= hoy - timedelta(days=30):
            anteriores.update(techs)
    pairs = []
    for t in recientes:
        n7, n30 = recientes[t], anteriores.get(t, 0)
        if n7 >= 2 and n7 > n30:
            pairs.append((f"{t} (x{n7 / max(1, n30):.1f})", n7))
    if not pairs:
        return None
    pairs.sort(key=lambda kv: -kv[1])
    plt = _setup_plt()
    return _barh(plt, pairs[:10], "techs_emergentes.png",
                 "Techs emergentes — 7d vs mes anterior (crecimiento)",
                 "#f9e2af", "ofertas 7d", out_dir)


def chart_techs_salario(conn: sqlite3.Connection, out_dir: Path) -> Path | None:
    """T3: mediana salarial por tech (solo techs con n>=3 declarados)."""
    rows = _techs_pool(conn, 30)
    por_tech: dict[str, list[int]] = {}
    for r in rows:
        v = _salary_to_clp_monthly(r.get("salary") or "", r.get("description") or "")
        if not v:
            continue
        for t in _techs_de_fila(r):
            por_tech.setdefault(t, []).append(v)
    pairs = []
    for t, vals in por_tech.items():
        if len(vals) < 3:
            continue
        vals.sort()
        pairs.append((f"{t} (n={len(vals)})", vals[len(vals) // 2]))
    if not pairs:
        return None
    pairs.sort(key=lambda kv: -kv[1])
    plt = _setup_plt()
    return _barh(plt, pairs[:10], "techs_salario.png",
                 "Mediana salarial por tecnología (CLP/mes)", "#cba6f7",
                 "CLP", out_dir)
