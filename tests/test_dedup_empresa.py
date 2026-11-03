"""Dedup con empresa desconocida: el comodín solo vale si ambas son desconocidas (fuentes sin dato)."""
import sqlite3

from jobhunt import dedup
from jobhunt.db import upsert, init_db

NOW = "2026-10-10T00:00:00+00:00"


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    from jobhunt.config import load_config
    import jobhunt.db as database
    init_db(c)
    return c


def _ins(c, title, company, src="jooble:q"):
    gid, nuevo = upsert(c, {"title": title, "company": company, "url": f"https://x/{abs(hash((title, company, src)))}",
                            "source": src, "date": "2026-10-09"}, NOW)
    c.commit()
    return gid, nuevo


def test_fuzzy_con_empresa_real_contra_vacia_no_fusiona():
    c = _conn()
    a, _ = _ins(c, "Data Scientist - GCP", "")
    b, nuevo = _ins(c, "Data Scientist Senior", "NeuralWorks", "getonboard:q")
    assert nuevo and a != b


def test_titulo_exacto_con_empresa_vacia_fusiona_y_completa_empresa():
    c = _conn()
    a, _ = _ins(c, "Data Engineer Semi Senior", "")
    b, nuevo = _ins(c, "Data Engineer Semi Senior", "Factor IT", "getonboard:q")
    assert not nuevo and a == b
    assert c.execute("SELECT company FROM ofertas WHERE group_id=?", (a,)).fetchone()[0] == "Factor IT"
    # ya con empresa conocida, otra empresa con el mismo título NO fusiona
    _, nuevo2 = _ins(c, "Data Engineer Semi Senior", "DRIMO", "getonboard:q")
    assert nuevo2


def test_fuzzy_ambas_vacias_sigue_fusionando():
    c = _conn()
    a, _ = _ins(c, "Desarrollador Full Stack Senior", "")
    b, nuevo = _ins(c, "Desarrollador Fullstack Senior", "", "computrabajo:q")
    assert not nuevo and a == b
