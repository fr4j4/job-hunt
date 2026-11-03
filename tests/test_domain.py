"""Tests del paquete jobhunt/domain (refactor de estructura, PASO 1).

1 test por módulo + 1 test de arquitectura (cero ciclos, tablas viejas vivas).
"""
from __future__ import annotations

import pathlib
import re

import jobhunt.channel as channel
import jobhunt.charts as charts
import jobhunt.db as db
import jobhunt.enrich as enrich
import jobhunt.scoring as scoring
from jobhunt.domain import fechas, roles, techs, texto


def test_fechas_normalize_date():
    assert fechas.normalize_date("2026-01-15") == "2026-01-15"
    assert fechas.normalize_date("31-12-2025") == "2025-12-31"
    assert fechas.normalize_date(None) == ""


def test_roles_is_dev_categoria_conocida():
    class _Cfg:
        class ia:
            enabled = True
    assert roles.is_dev("Backend", "cualquier título", _Cfg()) is True
    assert roles.is_dev("Analista/Empresa", "x", _Cfg()) is False


def test_texto_norm_quita_acentos_y_baja_case():
    assert texto._norm("Ingeniería") == "ingenieria"


def test_techs_tabla_unica_consistente():
    assert techs.ABBR_BY_ALIAS["python"] == "Py"
    assert techs.NAME_BY_ABBR["k8s"] == "Kubernetes"
    assert "Py" in techs.MARKET_ABBRS
    assert techs.TITLE_RE.search("Senior Python Developer")


def test_arquitectura_domain_no_importa_de_modulos_viejos():
    prohibido = re.compile(
        r"from\s+\.\.(enrich|channel|bot|cli|scoring|db)\s+import"
        r"|from\s+jobhunt\.(enrich|channel|bot|cli|scoring|db)\b"
    )
    domain_dir = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "domain"
    ofensores = []
    for f in domain_dir.glob("*.py"):
        texto_archivo = f.read_text(encoding="utf-8")
        if prohibido.search(texto_archivo):
            ofensores.append(f.name)
    assert not ofensores, f"import prohibido en domain/: {ofensores}"


def test_tablas_viejas_siguen_accesibles_por_nombre_viejo():
    # channel.py
    assert channel.normalize_date("2026-01-15") == "2026-01-15"
    assert channel.canonical_date({"date_posted": "2026-01-15", "first_seen": "2026-01-16"})
    assert channel.age_days({"date_posted": "2020-01-01", "first_seen": "2020-01-01"}) > 0
    assert "Backend" in channel._DEV_CATEGORIES
    assert "No-tech" in channel._NONDEV_CATEGORIES
    assert callable(channel._categorias_dev)
    assert callable(channel.is_dev)
    # scoring.py
    assert scoring._norm("Á") == "a"
    assert "Py" in scoring.MARKET_TECHS_ABBR
    assert scoring._MARKET_TECHS_TITLE_RE.search("Python Dev")
    # charts.py
    assert charts._TECH_CANON["k8s"] == "Kubernetes"
    # enrich.py
    assert enrich._TECH_ABBR["python"] == "Py"
    assert "No-tech" in enrich._NONDEV_CATEGORIES
    # db.py
    assert db._norm_text("Ingeniería") == "ingenieria"


# ---------- fecha de publicación: relativa → exacta ----------
import pytest as _pytest
from datetime import datetime as _dt, timezone as _tz
from jobhunt.domain.fechas import resolver_fecha

_NOW = _dt(2026, 10, 9, 15, 0, tzinfo=_tz.utc)


@_pytest.mark.parametrize("txt,iso,prec", [
    ("hace 3 días", "2026-10-06", "dia"), ("Hace un día", "2026-10-08", "dia"),
    ("hace una semana", "2026-10-02", "aprox"), ("2 weeks ago", "2026-09-25", "aprox"),
    ("hace 1 mes", "2026-09-09", "aprox"), ("hace más de 30 días", "2026-09-09", "aprox"),
    ("hoy", "2026-10-09", "dia"), ("Ayer", "2026-10-08", "dia"),
    ("hace 20 horas", "2026-10-08", "dia"), ("06-10-2026", "2026-10-06", "exact"),
    ("Publicado el 21 de Jul, 2026", "2026-07-21", "exact"),
    ("2026-10-06T10:00:00Z", "2026-10-06", "exact"),
])
def test_resolver_fecha(txt, iso, prec):
    assert resolver_fecha(txt, _NOW) == (iso, prec)


def test_resolver_fecha_basura():
    assert resolver_fecha("sin fecha", _NOW) == ("", "")
    assert resolver_fecha("", _NOW) == ("", "")


def test_upsert_resuelve_fecha_relativa_contra_captura(tmp_path):
    import sqlite3
    from jobhunt import db
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    db.init_db(conn)
    gid, _ = db.upsert(conn, {"title": "Dev", "company": "X", "url": "http://x/1",
                              "date": "Hace 2 semanas", "source": "computrabajo:q"},
                       "2026-10-09T15:00:00Z")
    r = conn.execute("SELECT date_posted, date_posted_raw, date_precision, date_canonical "
                     "FROM ofertas WHERE group_id=?", (gid,)).fetchone()
    assert tuple(r) == ("2026-09-25", "Hace 2 semanas", "aprox", "2026-09-25")
