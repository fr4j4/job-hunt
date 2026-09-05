"""Estadística robusta para salarios — spec salarios-robustos v2 (§2).

Funciones PURAS (sin DB, testeables). Filosofía:
- límites físicos = red de fondo (dominio, no estadística)
- MAD (Iglewicz-Hoaglin) + IQR (Tukey, method='inclusive') por CONSENSO
- guards degenerados: un caso raro jamás tumba el llamador (A2)
- CV (sample, n-1) = salud global de la muestra, no clasifica individuos (A12)

Nota parser: usamos parse_salary_clp PROPIO (wraper de la lógica de
_salary_to_clp_monthly pero SIN su banda 300k-20M — aquí la banda la pone
la física de stats.py: 100k-30M). El parser de scoring retorna None fuera
de SU banda, lo que impediría clasificar implausible por debajo (15000 →
None → no clasificable). Aquí necesitamos el VALOR crudo para clasificarlo.
"""
from __future__ import annotations

import re
import statistics as st

# límites físicos absolutos (red de fondo §2.1.a)
FLOOR = 100_000
CEILING = 30_000_000

# umbrales del consenso
MAD_Z = 3.5          # Iglewicz-Hoaglin recomendado
IQR_K = 1.5          # fences de Tukey
IQR_MIN_N = 8        # <8: IQR no calculable (§2.1.c)
CV_MIN_N = 10        # <10 muestras: modo insuficiente (contexto §3.1)

# banda de conversión USD (igual a scoring.py — única fuente de verdad del tipo de cambio)
USD_CLP = 950


def parse_salary_clp(raw: str, description: str = "") -> int:
    """Parser único (§2.2/A1): cualquier formato del pool → CLP mensual int.
    Maneja ambos formatos reales de la DB ('CLP 2000000' y '$ 2.400.000,00
    (Mensual)') + USD. NO aplica banda de plausibilidad (eso es trabajo del
    clasificador): 'CLP 15000' → 15000, 'CLP 66496000' → 66496000.
    Retorna 0 si no parsea."""
    if not raw:
        return 0
    raw = str(raw)
    lower = raw.lower()
    m = re.search(r"([\d.,]+)", raw)
    if not m:
        # monto CLP embebido en la descripción ($ N.NNN.NNN)
        md = re.search(r"\$\s?([\d.]{7,})", description or "")
        if md:
            try:
                return int(md.group(1).replace(".", "").replace(",", ""))
            except ValueError:
                return 0
        return 0
    num_s = m.group(1)
    if "." in num_s and "," in num_s:
        num_s = num_s.replace(".", "").replace(",", ".")   # 2.400.000,00 → 2400000.00
    elif num_s.count(",") == 1 and re.fullmatch(r"\d{1,3},\d{3}", num_s):
        num_s = num_s.replace(",", "")                     # 4,000 US → 4000
    elif "." in num_s:
        num_s = num_s.replace(".", "")                     # 2.400.000 → 2400000
    try:
        num = float(num_s)
    except ValueError:
        return 0
    if num < 1:
        return 0
    is_usd = "usd" in lower or (re.fullmatch(r"[\d,]{3,4}", num_s) and "clp" not in lower)
    if is_usd:
        return int(num * USD_CLP)
    return int(num)


def _median(vals: list) -> float:
    return st.median(vals)


def classify_salary(value: int, pool: list[int]) -> tuple[str, str]:
    """Clasifica value contra pool (leave-one-out: pool SIN el valor evaluado).
    Retorna (status, note) ∈ {trusted, suspect, implausible} × {below_floor,
    above_ceiling, mad_iqr, ''}. JAMÁS lanza (guards A2)."""
    try:
        # a) límites físicos — red de fondo
        if value < FLOOR:
            return "implausible", "below_floor"
        if value > CEILING:
            return "implausible", "above_ceiling"

        clean = [v for v in pool if v is not None]
        if not clean:                      # pool vacío → solo física decide (A2)
            return "trusted", ""

        # b) MAD modified z-score (MAD=0 → no decide, A2)
        med = _median(clean)
        mad = _median([abs(v - med) for v in clean])
        mad_flag = False
        if mad > 0:
            mad_flag = abs(0.6745 * (value - med) / mad) > MAD_Z

        # c) IQR fences de Tukey (inclusive; n<8 → no calculable)
        iqr_flag = False
        if len(clean) >= IQR_MIN_N:
            q = st.quantiles(clean, n=4, method="inclusive")
            q1, q3 = q[0], q[2]
            iqr = q3 - q1
            if iqr > 0:
                iqr_flag = value < (q1 - IQR_K * iqr) or value > (q3 + IQR_K * iqr)

        # d) consenso: ambos → suspect; solo uno o ninguno → trusted
        if mad_flag and iqr_flag:
            return "suspect", "mad_iqr"
        return "trusted", ""
    except Exception:
        # un caso degenerado JAMÁS tumba al llamador — degrada a trusted
        return "trusted", ""


def cv_health(pool: list[int]) -> tuple[float, str]:
    """CV muestral (n-1, A12) sobre el pool SIN implausible. Retorna (cv, etiqueta)."""
    clean = sorted(v for v in pool if FLOOR <= v <= CEILING)
    if len(clean) < 2:
        return 0.0, "sin muestra"
    cv = st.stdev(clean) / _median(clean)
    if cv < 0.6:
        label = "homogéneo"
    elif cv <= 1.0:
        label = "disperso"
    else:
        label = "orientativa"
    return round(cv, 3), label


def annual_likely(value: int, median: int) -> bool:
    """Heurística determinística (§3.2): valor > 12× mediana → probable cifra anual."""
    return median > 0 and value > 12 * median