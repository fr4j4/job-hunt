"""Parser de tarjetas del SERP de Jooble (layout 2026-10) y relleno de ubicación en re-avistamientos."""
import sqlite3

import pytest

from jobhunt.sources.jooble import _parse_card

_EXTRACTO = ("Desarrollo y mantención de soluciones backend en Java y Spring Boot, incluyendo APIs REST, "
             "microservicios e integración con servicios externos. Implementación de acceso a datos con SQL, "
             "Oracle, JDBC y JPA, junto con desarrollo de pruebas unitarias. Participación en revisión...\xa0")


def test_tarjeta_con_empresa_ubicacion_y_fecha_relativa():
    r = _parse_card(f"Senior Java Developer\n{_EXTRACTO}\nReportar\n\nCmg3\n\nSantiago\nHace un mes")
    assert (r["company"], r["location"], r["date"]) == ("Cmg3", "Santiago", "Hace un mes")
    assert r["snippet"].startswith("Desarrollo y mantención") and r["salary"] == ""


def test_tarjeta_sin_empresa_y_fecha_atras_se_normaliza():
    r = _parse_card(f"Desarrollador Backend\n{_EXTRACTO}\nReportar\nSantiago\n25 días atrás")
    assert (r["company"], r["location"], r["date"]) == ("", "Santiago", "hace 25 días")


def test_tarjeta_con_sueldo_suelto_bajo_el_titulo():
    r = _parse_card(f"Desarrollador Backend - JAVA\n\n$1600000\n\n{_EXTRACTO}\nReportar\n\nHaibu Solutions Spa\n\nSantiago\n14 días atrás")
    assert r["salary"] == "CLP 1600000" and r["company"] == "Haibu Solutions Spa"


def test_tarjeta_ignora_montos_fuera_de_banda_y_sin_contexto():
    r = _parse_card(f"Dev\n$500\n{_EXTRACTO}\nReportar\nSantiago\n3 días atrás")
    assert r["salary"] == ""
    r = _parse_card(f"Dev\n{_EXTRACTO} presupuesto $2.500.000 para el proyecto\nReportar\nSantiago\n3 días atrás")
    assert r["salary"] == ""


@pytest.fixture
def mem_db():
    from jobhunt.db import init_db
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()


def test_upsert_rellena_ubicacion_vacia_al_reavistar(mem_db):
    from jobhunt.db import upsert
    base = {"title": "Desarrollador Backend Java", "company": "Acme", "url": "https://x.cl/a",
            "source": "jooble:java", "found_by": "perfil:java", "location": "", "_desc": "x" * 50}
    gid, nuevo = upsert(mem_db, dict(base), "2026-10-19T00:00:00+00:00")
    assert nuevo
    gid2, nuevo2 = upsert(mem_db, {**base, "location": "Santiago"}, "2026-10-19T04:00:00+00:00")
    assert gid2 == gid and not nuevo2
    assert mem_db.execute("SELECT location FROM ofertas WHERE group_id=?", (gid,)).fetchone()[0] == "Santiago"
    upsert(mem_db, {**base, "location": "Valdivia"}, "2026-10-19T08:00:00+00:00")   # no pisa lo ya guardado
    assert mem_db.execute("SELECT location FROM ofertas WHERE group_id=?", (gid,)).fetchone()[0] == "Santiago"
