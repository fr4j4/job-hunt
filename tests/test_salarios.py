"""Tests del paquete jobhunt/salarios (refactor de estructura, PASO 2)."""
from __future__ import annotations

import pathlib
import re

from jobhunt.salarios.arbiter import SalaryArbitrator


def _row(**kw):
    base = {"group_id": "g1", "title": "Dev", "salary": "", "ai_opinion": "", "ia_model": "",
            "db_salary": "", "db_source": "", "db_status": ""}
    base.update(kw)
    return base


def test_decide_ficha_mensual_trusted():
    d = SalaryArbitrator(None).decide(_row(), {"salary": "CLP 2000000"},
                                      [2_000_000, 2_100_000, 2_200_000])
    assert d.salary == "CLP 2000000"
    assert (d.salary_source, d.salary_status, d.salary_note) == ("text", "trusted", "text_confirms")
    assert d.extra["value"] == 2_000_000
    assert 2_000_000 not in d.extra["pool_loo"]      # leave-one-out
    assert d.unmark_ia is False


def test_decide_anual_se_divide_por_12():
    d = SalaryArbitrator(None).decide(_row(), {"salary": "CLP 18000000/año"}, [])
    assert d.salary_note == "annual_likely"
    assert d.salary_status == "trusted"
    assert d.salary == "CLP 18000000/año"            # texto crudo intacto


def test_decide_sin_ficha_y_pool_vacio_no_decide():
    d = SalaryArbitrator(None).decide(_row(), {"salary": ""}, [])
    assert (d.salary, d.salary_source, d.salary_status, d.salary_note) == (None, None, None, None)
    assert d.extra == {}


def test_arquitectura_salarios_no_importa_de_modulos_viejos():
    prohibido = re.compile(
        r"from\s+\.\.(enrich|channel|bot|cli|scoring|db|stats)\s+import"
        r"|from\s+jobhunt\.(enrich|channel|bot|cli|scoring|db|stats)\b")
    d = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "salarios"
    ofensores = [f.name for f in d.glob("*.py") if prohibido.search(f.read_text(encoding="utf-8"))]
    assert not ofensores, f"import prohibido en salarios/: {ofensores}"


def test_stats_viejo_sigue_reexportando():
    from jobhunt import stats as _st
    assert _st.parse_salary_clp("CLP 2000000") == 2_000_000
    assert _st.classify_salary(2_000_000, [])[0] == "trusted"
    assert (_st.FLOOR, _st.CEILING) == (100_000, 30_000_000)
    assert _st._median([1, 3, 5]) == 3        # privado usado por channel.py
    assert _st.cv_health([]) == (0.0, "sin muestra")
    assert _st.annual_likely(19_000_000, 1_500_000)


# ---------- sueldo IA debe estar respaldado por el texto (anti-invención) ----------
import pytest
from jobhunt.salarios.texto import extraer_montos, sueldo_respaldado


@pytest.mark.parametrize("txt,esperado", [
    ("Sueldo $2.500.000 líquidos", 2500000), ("renta 2,5 millones", 2500000),
    ("1.8M mensual", 1800000), ("800 mil", 800000), ("USD 3,000", 2850000),
])
def test_extraer_montos(txt, esperado):
    assert esperado in extraer_montos(txt)


def test_sueldo_respaldado():
    assert sueldo_respaldado(2500000, "Ofrecemos $2.500.000")
    assert not sueldo_respaldado(3000000, "Ofrecemos $2.500.000")   # inventado
    assert not sueldo_respaldado(2500000, "Sin sueldo declarado, 5 años de experiencia")
    assert not sueldo_respaldado(50000, "pago $50.000")             # bajo el piso físico
