"""Materialización: derivadas, techs/tags, eventos, historia que sobrevive a la purga."""
import json
from datetime import datetime, timezone

import pytest

from jobhunt import db as database
from jobhunt.analytics.materializar import materializar
from jobhunt.config import load_config


@pytest.fixture
def ent(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(type(cfg), "db_path", property(lambda self: tmp_path / "a.sqlite"), raising=False)
    conn = database.connect(cfg)
    database.init_db(conn)
    yield cfg, conn
    conn.close()


def _oferta(conn, titulo, empresa="Acme", fuente="laborum", first_seen="2026-10-08T10:00:00+00:00", **campos):
    gid, _ = database.upsert(conn, {"title": titulo, "company": empresa, "url": f"https://x.cl/{titulo}",
                                    "source": f"{fuente}:q", "date": "2026-10-08"}, first_seen)
    if campos:
        sets = ", ".join(f"{k}=?" for k in campos)
        conn.execute(f"UPDATE ofertas SET {sets} WHERE group_id=?", (*campos.values(), gid))
    conn.commit()
    return gid


def _scan(conn, ts, fuentes):
    conn.execute("INSERT INTO scan_log (ts,total_seen,new_count,sources_summary) VALUES (?,?,?,?)",
                 (ts, 1, 0, json.dumps({f: {"n": n, "err": 0} for f, n in fuentes.items()})))
    conn.commit()


AHORA = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_deriva_columnas_techs_y_tags(ent):
    cfg, conn = ent
    gid = _oferta(conn, "Backend Python Sr", rol_categoria="Backend", seniority_real="senior",
                  salary="$ 2.500.000,00 (Mensual)", modality="Remoto", techs="Py;JS;AWS", location="Santiago Las Condes",
                  ai_benefits=json.dumps(["Seguro Complementario"]), ai_red_flags=json.dumps(["Horario extendido"]),
                  ai_idiomas=json.dumps([{"idioma": "Inglés", "nivel": "Avanzado", "excluyente": True}]))
    r = materializar(conn, cfg, ahora=AHORA)
    assert r["filas"] == 1
    o = dict(conn.execute("SELECT * FROM ofertas WHERE group_id=?", (gid,)).fetchone())
    assert (o["rol_familia"], o["seniority_norm"], o["salary_clp"], o["modality_norm"], o["modality_source"]) == \
        ("Desarrollo", "senior", 2500000, "remoto", "oficial")
    assert (o["region"], o["comuna"], o["company_canon"], o["norm_version"]) == ("Metropolitana", "Las Condes", "acme", "n2")
    assert {t for (t,) in conn.execute("SELECT tech FROM oferta_techs")} == {"Python", "JavaScript", "AWS"}
    tags = set(map(tuple, conn.execute("SELECT tipo, valor FROM oferta_tags")))
    assert ("beneficio", "seguro complementario") in tags and ("rojo", "horario extendido") in tags
    assert ("idioma", "ingles:avanzado:excluyente") in tags


def test_idempotente_y_no_toca_updated_at(ent):
    cfg, conn = ent
    _oferta(conn, "Dev A", techs="Py")
    materializar(conn, cfg, ahora=AHORA)
    upd = conn.execute("SELECT updated_at FROM ofertas").fetchone()[0]
    n1 = [tuple(r) for r in conn.execute("SELECT * FROM oferta_eventos")]
    r2 = materializar(conn, cfg, ahora=AHORA)
    assert r2["derivadas"] == 0 and r2["eventos"] == {}
    assert conn.execute("SELECT updated_at FROM ofertas").fetchone()[0] == upd   # derivadas no cuentan como edición
    assert [tuple(r) for r in conn.execute("SELECT * FROM oferta_eventos")] == n1
    assert conn.execute("SELECT COUNT(*) FROM oferta_techs").fetchone()[0] == 1
    r3 = materializar(conn, cfg, full=True, ahora=AHORA)
    assert r3["derivadas"] == 1 and conn.execute("SELECT COUNT(*) FROM oferta_techs").fetchone()[0] == 1


def test_inferencia_de_modalidad_y_apagado(ent):
    cfg, conn = ent
    _oferta(conn, "Dev B", description="Trabajo 100% remoto para todo Chile")
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT modality_norm, modality_source FROM ofertas").fetchone()[:] == ("remoto", "inferida")
    assert conn.execute("SELECT modality FROM ofertas").fetchone()[0] == ""            # el original no se toca
    cfg.analytics.infer_modality = False
    materializar(conn, cfg, full=True, ahora=AHORA)
    assert conn.execute("SELECT modality_norm, modality_source FROM ofertas").fetchone()[:] == ("", "")


def test_eventos_aparecida_sueldo_score_y_cierre_por_enrich(ent):
    cfg, conn = ent
    gid = _oferta(conn, "Dev C", score=50, **{"salary": ""})
    materializar(conn, cfg, ahora=AHORA)
    assert [r[0] for r in conn.execute("SELECT tipo FROM oferta_eventos")] == ["aparecida"]
    # (updated_at explícito: el reloj simulado AHORA va por delante del real y el trigger usa el real)
    conn.execute("UPDATE ofertas SET salary='CLP 2000000', score=75, updated_at='2026-10-10T13:00:00Z' WHERE group_id=?", (gid,))
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)
    ev = {t: (a, d) for t, a, d in conn.execute("SELECT tipo, antes, despues FROM oferta_eventos")}
    assert ev["sueldo"] == ("", "2000000") and ev["score"] == ("50", "75")
    conn.execute("UPDATE ofertas SET active=0 WHERE group_id=?", (gid,))
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT COUNT(*) FROM oferta_eventos WHERE tipo='cerrada'").fetchone()[0] == 1
    conn.execute("UPDATE ofertas SET active=1 WHERE group_id=?", (gid,))
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT COUNT(*) FROM oferta_eventos WHERE tipo='reaparecida'").fetchone()[0] == 1


def test_cierre_por_no_vista_solo_con_fuente_sana(ent):
    cfg, conn = ent
    cfg.analytics.close_after_sweeps = 3
    vieja = _oferta(conn, "Vieja", fuente="laborum", first_seen="2026-10-01T00:00:00+00:00")
    conn.execute("UPDATE ofertas SET last_seen='2026-10-01T00:00:00+00:00', sources='laborum' WHERE group_id=?", (vieja,))
    fresca = _oferta(conn, "Fresca", fuente="laborum", first_seen="2026-10-05T00:00:00+00:00")
    conn.execute("UPDATE ofertas SET last_seen='2026-10-05T04:00:00+00:00', sources='laborum' WHERE group_id=?", (fresca,))
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)     # baseline
    # laborum CAÍDA (n=0) durante 4 barridos: no debe declarar cierres
    for i in range(4):
        _scan(conn, f"2026-10-06T0{i}:00:00+00:00", {"laborum": 0, "indeed": 50})
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT COUNT(*) FROM oferta_eventos WHERE tipo='cerrada'").fetchone()[0] == 0
    # laborum sana en 3 barridos y las ofertas no aparecen → ambas "cerradas" (last_seen < 3.er barrido sano)
    for i in range(3):
        _scan(conn, f"2026-10-07T0{i}:00:00+00:00", {"laborum": 20, "indeed": 50})
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT COUNT(*) FROM oferta_eventos WHERE tipo='cerrada'").fetchone()[0] == 2
    # la fresca vuelve a verse → reaparecida
    conn.execute("UPDATE ofertas SET last_seen='2026-10-07T02:00:00+00:00' WHERE group_id=?", (fresca,))
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)
    assert [r[0] for r in conn.execute("SELECT group_id FROM oferta_eventos WHERE tipo='reaparecida'")] == [fresca]
    # y el analítico NO cambia `active`
    assert conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1").fetchone()[0] == 2


def test_purga_no_toca_la_historia(ent):
    cfg, conn = ent
    _oferta(conn, "Dev D", techs="Py", salary="CLP 2000000", rol_categoria="Backend")
    materializar(conn, cfg, ahora=AHORA)
    antes = (conn.execute("SELECT COUNT(*) FROM oferta_eventos").fetchone()[0],
             conn.execute("SELECT COUNT(*) FROM mercado_diario").fetchone()[0])
    assert antes[0] >= 1 and antes[1] >= 1
    conn.execute("DELETE FROM ofertas")              # /db all
    conn.commit()
    materializar(conn, cfg, ahora=AHORA)
    assert (conn.execute("SELECT COUNT(*) FROM oferta_eventos").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM mercado_diario").fetchone()[0]) == antes
    assert conn.execute("SELECT COUNT(*) FROM oferta_techs").fetchone()[0] == 0   # derivadas huérfanas se limpian


def test_mercado_diario_y_umbral_de_percentiles(ent):
    cfg, conn = ent
    for i, s in enumerate([1000000, 1200000, 1400000, 1600000, 1800000, 2000000]):
        _oferta(conn, f"Dev {i}", rol_categoria="Backend", seniority_real="senior", salary=f"CLP {s}",
                first_seen="2026-10-10T08:00:00+00:00")
    _oferta(conn, "Sin sueldo", rol_categoria="QA")
    _oferta(conn, "Fuera de banda", rol_categoria="Backend", salary="CLP 12000000000")   # absurdo
    materializar(conn, cfg, ahora=AHORA)
    tot = conn.execute("SELECT n_activas, n_nuevas, n_con_sueldo, sueldo_p50 FROM mercado_diario "
                       "WHERE fecha='2026-10-10' AND rol_familia='*' AND seniority='*' AND fuente='*' AND modalidad='*'").fetchone()
    assert tot[:3] == (8, 6, 6) and tot[3] == 1500000          # mediana de los 6 válidos; el absurdo no cuenta
    qa = conn.execute("SELECT n_activas, n_con_sueldo, sueldo_p50 FROM mercado_diario WHERE rol_familia='QA' "
                      "AND seniority='*' AND fuente='*' AND modalidad='*'").fetchone()
    assert tuple(qa) == (1, 0, None)                           # n<5 ⇒ sin percentiles
    n1 = conn.execute("SELECT COUNT(*) FROM mercado_diario").fetchone()[0]
    materializar(conn, cfg, ahora=AHORA)
    assert conn.execute("SELECT COUNT(*) FROM mercado_diario").fetchone()[0] == n1      # no duplica


def test_techs_semanales(ent):
    cfg, conn = ent
    _oferta(conn, "A", techs="Py;AWS", rol_categoria="Backend", first_seen="2026-10-10T01:00:00+00:00")
    _oferta(conn, "B", techs="Py", rol_categoria="Data", first_seen="2026-10-01T10:00:00+00:00")
    _oferta(conn, "C")                                   # sin techs: fuera de la base
    materializar(conn, cfg, ahora=AHORA)
    fila = conn.execute("SELECT n_activas, n_nuevas, n_base FROM mercado_tech_semanal "
                        "WHERE tech='Python' AND rol_familia='*'").fetchone()
    assert tuple(fila) == (2, 1, 2)                      # base = ofertas con techs conocidas, no el total
    assert conn.execute("SELECT n_base FROM mercado_tech_semanal WHERE tech='Python' AND rol_familia='Datos e IA'").fetchone()[0] == 1


def test_exp_anios_y_staffing_se_derivan_donde_la_fuente_no_los_trae(ent):
    """T0-1: years_official solo viene del JSON-LD (casi nunca) y `staffing` nunca se escribía:
    se derivan con la misma lógica que usa el score."""
    cfg, conn = ent
    a = _oferta(conn, "Dev E", description="Buscamos 5 años de experiencia en Java")
    b = _oferta(conn, "Dev F ref#ABC123", years_official=3)
    materializar(conn, cfg, ahora=AHORA)
    fila = lambda g: tuple(conn.execute("SELECT exp_anios, staffing FROM ofertas WHERE group_id=?", (g,)).fetchone())
    assert fila(a) == (5, 0) and fila(b) == (3, 1)
