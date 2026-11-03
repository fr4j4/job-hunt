"""Estadística robusta, pura y sin dependencias. Gemela de frontend/src/motor/estadistica.ts:
ambas pasan los mismos vectores (tests/fixtures/estadistica.json)."""
from __future__ import annotations

import math


def percentil(valores, p: float):
    """Interpolación lineal (como numpy 'linear'). p en [0,100]. Vacío → None."""
    v = sorted(x for x in valores if x is not None and not (isinstance(x, float) and math.isnan(x)))
    if not v:
        return None
    if len(v) == 1:
        return float(v[0])
    pos = (len(v) - 1) * p / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def mediana(valores):
    return percentil(valores, 50)


class Mulberry32:
    """PRNG determinista (mismo algoritmo en TS) para que el bootstrap sea reproducible."""

    def __init__(self, seed: int):
        self.a = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def bootstrap_mediana(valores, B: int = 500, nivel: float = 0.90, semilla: int = 12345):
    """IC por percentiles de la mediana. <2 valores → None."""
    v = [x for x in valores if x is not None]
    n = len(v)
    if n < 2:
        return None
    rng = Mulberry32(semilla)
    meds = []
    for _ in range(B):
        muestra = [v[int(rng.next() * n)] for _ in range(n)]
        meds.append(mediana(muestra))
    a = (1 - nivel) / 2 * 100
    return percentil(meds, a), percentil(meds, 100 - a)


def kaplan_meier(duraciones, observado):
    """Curva de supervivencia. duraciones en días; observado[i]=True si el evento (cierre) ocurrió,
    False si está censurada (sigue abierta). → (puntos [(t, S)], mediana|None)."""
    datos = sorted(zip(duraciones, observado))
    n = len(datos)
    s = 1.0
    pts = []
    i = 0
    en_riesgo = n
    while i < n:
        t = datos[i][0]
        muertes = salen = 0
        while i < n and datos[i][0] == t:
            muertes += 1 if datos[i][1] else 0
            salen += 1
            i += 1
        if muertes:
            s *= 1 - muertes / en_riesgo
            pts.append((t, s))
        en_riesgo -= salen
    med = next((t for t, sv in pts if sv <= 0.5), None)
    return pts, med


def lift(n_ab: int, n_a: int, n_b: int, n: int):
    """P(A∧B)/(P(A)P(B)). None si falta algún marginal."""
    if not (n and n_a and n_b):
        return None
    return (n_ab / n) / ((n_a / n) * (n_b / n))


def prima_estratificada(filas, min_por_lado: int = 3):
    """Prima salarial de una tech controlando por estrato.

    filas: iterable de (estrato, sueldo, tiene_tech[bool]). En cada estrato con >= min_por_lado
    ofertas con y sin la tech: (mediana_con - mediana_sin)/mediana_sin; promedio ponderado por n_con.
    → (prima|None, n_con_total).
    """
    por: dict = {}
    for estrato, sueldo, con in filas:
        if sueldo is None:
            continue
        por.setdefault(estrato, ([], []))[0 if con else 1].append(sueldo)
    num = den = 0.0
    n_con_total = 0
    for con, sin in por.values():
        if len(con) >= min_por_lado and len(sin) >= min_por_lado:
            ms = mediana(sin)
            if ms:
                num += len(con) * (mediana(con) - ms) / ms
                den += len(con)
                n_con_total += len(con)
    return (num / den if den else None), n_con_total
