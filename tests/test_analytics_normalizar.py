"""Normalizadores y estadística de la capa analítica (casos reales vistos en la DB)."""
import pytest

from jobhunt.analytics import normalizar as nz
from jobhunt.analytics.estadistica import (bootstrap_mediana, kaplan_meier, lift, mediana, percentil,
                                           prima_estratificada)


@pytest.mark.parametrize("real,oficial,esperado", [
    ("senior", "", "senior"), ("junior", "", "junior"), ("semi", "", "semi"), ("lead", "", "lead"),
    ("noviciado", "", "trainee"), ("mid-level", "", "semi"), ("intermedio", "", "semi"),
    ("mid senior", "", "senior"),
    ("deseable", "", ""), ("no", "", ""), ("", "", ""), (None, None, ""),
    ("", "Mid-Senior level", "senior"), ("", "Entry level", "junior"), ("", "Associate", "semi"),
    ("", "Director", "lead"), ("deseable", "Entry level", "junior"),      # basura → cae al oficial
    ("senior", "Entry level", "senior"),                                    # real manda
])
def test_seniority(real, oficial, esperado):
    assert nz.norm_seniority(real, oficial) == esperado


def test_rol_familia_cubre_los_17_roles_reales():
    roles = {"Analista/Empresa": "No desarrollo", "Full Stack": "Desarrollo",
             "Ingeniería no-software": "No desarrollo", "QA": "QA", "Otro": "No desarrollo",
             "Tech Lead": "Desarrollo", "Backend": "Desarrollo", "Data": "Datos e IA",
             "Software": "Desarrollo", "No-tech": "No desarrollo", "DevOps/Cloud": "Infra y seguridad",
             "Seguridad": "Infra y seguridad", "AI/ML": "Datos e IA", "Frontend": "Desarrollo",
             "Mobile": "Desarrollo", "Soporte/TI": "Infra y seguridad", "Profesor/Formación": "No desarrollo",
             "": "No desarrollo", None: "No desarrollo"}
    for rol, fam in roles.items():
        assert nz.norm_rol_familia(rol) == fam, rol
    assert set(roles.values()) <= set(nz.FAMILIAS)


@pytest.mark.parametrize("texto,esperado", [
    ("remoto", "remoto"), ("híbrido", "hibrido"), ("Hybrid", "hibrido"), ("presencial", "presencial"),
    ("", ""), ("otra cosa", ""),
])
def test_modalidad(texto, esperado):
    assert nz.norm_modalidad(texto) == esperado


def test_inferir_modalidad():
    assert nz.inferir_modalidad("Dev", "Trabajo 100% remoto desde casa") == "remoto"
    assert nz.inferir_modalidad("Dev", "Modalidad híbrida, 3 días en la oficina") == "hibrido"
    assert nz.inferir_modalidad("Dev", "Trabajo presencial en Las Condes") == "presencial"
    assert nz.inferir_modalidad("Dev", "Trabajo remoto o presencial según el equipo") == ""     # contradictorio
    assert nz.inferir_modalidad("Dev", "nada que ver") == ""
    assert nz.inferir_modalidad("Dev", "", remote_official=1) == "remoto"


@pytest.mark.parametrize("texto,esperado", [
    ("Full-time", "completa"), ("Full-time OPEN_ENDED", "completa"), ("PART_TIME", "parcial"),
    ("OTHER", "otro"), ("", ""), ("Contract", "contrato"), ("INTERN", "practica"),
])
def test_empleo(texto, esperado):
    assert nz.norm_empleo(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [("first 25", 25), ("over 200 applicants", 200), ("", None), (None, None)])
def test_applicants(texto, esperado):
    assert nz.norm_applicants(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("Santiago Las Condes", ("Metropolitana", "Las Condes")),
    ("Las Condes, Región Metropolitana", ("Metropolitana", "Las Condes")),
    ("Santiago, Santiago Metropolitan Region, Chile", ("Metropolitana", "Santiago")),
    ("Santiago de Chile, Región Metropolitana", ("Metropolitana", "Santiago")),
    ("Santiago Centro", ("Metropolitana", "Santiago")),
    ("Santiago Metropolitan Area", ("Metropolitana", "Santiago")),
    ("las_condes", ("Metropolitana", "Las Condes")),
    ("Las Condes 7550000", ("Metropolitana", "Las Condes")),
    ("Santiago Nunoa", ("Metropolitana", "Ñuñoa")),
    ("Ñuñoa, Región Metropolitana", ("Metropolitana", "Ñuñoa")),
    ("Antofagasta", ("Antofagasta", "Antofagasta")),
    ("Viña del Mar, Valparaiso Region, Chile", ("Valparaíso", "Viña del Mar")),
    ("Concepción, Biobío", ("Biobío", "Concepción")),
    ("Iquique, Tarapacá", ("Tarapacá", "Iquique")),
    ("Valparaiso Region, Chile", ("Valparaíso", "")),
    ("Santiago Metropolitan Region, Chile", ("Metropolitana", "Santiago")),
    ("Remoto LatAm", ("remoto", "")),
    ("Chile", ("desconocida", "")), ("", ("desconocida", "")), (None, ("desconocida", "")),
    ("Cerca de la luna", ("desconocida", "")),
])
def test_ubicacion(texto, esperado):
    assert nz.norm_ubicacion(texto) == esperado


@pytest.mark.parametrize("texto,esperado", [
    ("BairesDev", "bairesdev"), ("KIBERNUM S.A.", "kibernum"), ("Abenis - Santiago", "abenis"),
    ("XinerLink - Santiago", "xinerlink"), ("Perceptual Consultores Ltda. - Santiago", "perceptual consultores"),
    ("Confidencial", ""), ("", ""), (None, ""), ("BC Tecnología", "bc tecnologia"),
    ("ECRGROUP®️ - Santiago", "ecrgroup"),
])
def test_empresa(texto, esperado):
    assert nz.norm_empresa(texto) == esperado


def test_empresa_alias():
    assert nz.norm_empresa("Falabella.com", {"falabella com": "falabella"}) == "falabella"


@pytest.mark.parametrize("sal,desc,mn,mx", [
    ("$ 1.400.000,00 (Mensual)", "", 1400000, 1400000),
    ("CLP 1531444", "", 1531444, 1531444),
    ("CLP 15000", "", None, None),
    ("", "", None, None),
    ("CLP 1.500.000 - 2.000.000", "", 1500000, 2000000),
    ("", "Sueldo $1.800.000 líquidos", 1800000, 1800000),
])
def test_sueldo_rango(sal, desc, mn, mx):
    a, b, _ = nz.sueldo_rango(sal, desc)
    assert (a, b) == (mn, mx)


# ---------------- estadística ----------------

def test_percentil_y_mediana():
    assert percentil([1, 2, 3, 4], 50) == 2.5
    assert percentil([1, 2, 3, 4, 5], 25) == 2
    assert percentil([7], 90) == 7
    assert percentil([], 50) is None
    assert mediana([5, None, 1, 3]) == 3
    assert percentil([10, 20], 0) == 10 and percentil([10, 20], 100) == 20


def test_bootstrap_determinista_y_acotado():
    v = [800, 900, 1000, 1100, 1200, 1500, 2000]
    a = bootstrap_mediana(v, B=200)
    assert a == bootstrap_mediana(v, B=200)           # misma semilla, mismo resultado
    assert min(v) <= a[0] <= mediana(v) <= a[1] <= max(v)
    assert bootstrap_mediana([5]) is None


def test_kaplan_meier_con_censura():
    # 4 cierres a los días 2,2,4,6 y 2 abiertas (censuradas) a los 5 y 9
    pts, med = kaplan_meier([2, 2, 4, 5, 6, 9], [True, True, True, False, True, False])
    assert pts[0] == (2, pytest.approx(4 / 6))
    assert pts[1] == (4, pytest.approx(4 / 6 * 3 / 4))
    assert med == 4                                    # S(4)=0.5 → primera vez ≤ 0.5
    assert kaplan_meier([3, 4], [False, False]) == ([], None)   # nadie cerró


def test_lift():
    assert lift(10, 20, 20, 100) == pytest.approx(2.5)
    assert lift(0, 0, 5, 100) is None


def test_prima_estratificada_controla_por_estrato():
    # Dentro de cada estrato la tech NO paga más; solo se concentra en el estrato senior (que paga más)
    filas = [("junior", 1000, False)] * 3 + [("junior", 1000, True)] * 3 \
            + [("senior", 2000, True)] * 6 + [("senior", 2000, False)] * 3
    prima, n = prima_estratificada(filas)
    assert prima == pytest.approx(0.0) and n == 9
    # la comparación ingenua (con vs sin, sin estratificar) diría que sí paga más
    con = [s for _, s, c in filas if c]
    sin = [s for _, s, c in filas if not c]
    assert mediana(con) > mediana(sin)
    assert prima_estratificada([("a", 1, True)])[0] is None


def test_vectores_compartidos_con_typescript():
    """El motor del navegador (frontend/src/motor/estadistica.ts) pasa estos MISMOS vectores."""
    import json
    import pathlib
    from jobhunt.analytics.estadistica import Mulberry32
    f = json.loads((pathlib.Path(__file__).parent / "fixtures" / "estadistica.json").read_text())
    for c in f["percentil"]:
        assert percentil(c["v"], c["p"]) == pytest.approx(c["e"])
    rng = Mulberry32(f["mulberry32"]["seed"])
    assert [rng.next() for _ in f["mulberry32"]["secuencia"]] == pytest.approx(f["mulberry32"]["secuencia"])
    b = f["bootstrap"]
    assert list(bootstrap_mediana(b["v"], b["B"], b["nivel"], b["semilla"])) == pytest.approx(b["e"])
    k = f["kaplan_meier"]
    pts, med = kaplan_meier(k["dur"], k["obs"])
    assert [x for p in pts for x in p] == pytest.approx([x for p in k["e_pts"] for x in p]) and med == k["e_mediana"]
    assert lift(f["lift"]["n_ab"], f["lift"]["n_a"], f["lift"]["n_b"], f["lift"]["n"]) == pytest.approx(f["lift"]["e"])
    prima, n = prima_estratificada([tuple(x) for x in f["prima"]["filas"]])
    assert (prima, n) == pytest.approx(tuple(f["prima"]["e"]))
