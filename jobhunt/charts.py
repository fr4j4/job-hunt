"""Gráficos para el canal (digests con PNG nativos de Telegram).

Reusa el estilo dark de market.py (matplotlib Agg, headless-safe).
Cada función recibe conn y retorna el Path del PNG (o None si no hay datos).
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from .scoring import _salary_to_clp_monthly, _norm

_ROL_LABELS = {
    "Full Stack": "Full Stack", "Backend": "Backend", "Frontend": "Frontend",
    "Data": "Data", "Mobile": "Mobile", "AI/ML": "AI/ML", "Tech Lead": "Tech Lead",
    "DevOps/Cloud": "DevOps/Cloud", "QA": "QA", "Software": "Software",
    "Seguridad": "Seguridad", "Otro": "Otro",
}
_SEN_LABELS = {"senior": "Senior", "semi": "Semi-senior", "lead": "Lead", "junior": "Junior"}


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
