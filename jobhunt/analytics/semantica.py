"""Registro semántico: dimensiones y métricas definidas UNA vez (spec-web-v2 §2).

Lo consumen la API (whitelists, /api/semantica), el motor del navegador y el Explorador.
Ningún gráfico define su propia métrica.
"""
from __future__ import annotations

from . import normalizar as nz

# tipo: nominal | ordinal | temporal | cuantitativa · multivalor: una oferta cuenta una vez por valor
DIMENSIONES: dict[str, dict] = {
    "rol_familia": {"etiqueta": "Familia de rol", "tipo": "nominal", "orden": list(nz.FAMILIAS)},
    "rol": {"etiqueta": "Rol", "tipo": "nominal"},
    "seniority": {"etiqueta": "Seniority", "tipo": "ordinal", "orden": list(nz.SENIORITIES)},
    "modalidad": {"etiqueta": "Modalidad", "tipo": "nominal", "orden": ["remoto", "hibrido", "presencial"]},
    "fuente": {"etiqueta": "Fuente", "tipo": "nominal"},
    "encaje": {"etiqueta": "Encaje IA", "tipo": "ordinal", "orden": ["ninguno", "bajo", "medio", "alto"]},
    "ingles": {"etiqueta": "Inglés", "tipo": "ordinal", "orden": ["no", "deseable", "requerido"]},
    "empleo": {"etiqueta": "Jornada", "tipo": "nominal"},
    "region": {"etiqueta": "Región", "tipo": "nominal", "drill": "comuna"},
    "comuna": {"etiqueta": "Comuna", "tipo": "nominal"},
    "empresa": {"etiqueta": "Empresa", "tipo": "nominal", "top": 12},
    "tech": {"etiqueta": "Tecnología", "tipo": "nominal", "multivalor": True, "top": 25},
    "beneficio": {"etiqueta": "Beneficio", "tipo": "nominal", "multivalor": True, "top": 15},
    "alerta": {"etiqueta": "Alerta IA", "tipo": "nominal", "multivalor": True, "top": 15},
    "a_favor": {"etiqueta": "A favor (IA)", "tipo": "nominal", "multivalor": True, "top": 15},
    "dia": {"etiqueta": "Día", "tipo": "temporal"},
    "semana": {"etiqueta": "Semana", "tipo": "temporal"},
    "mes": {"etiqueta": "Mes", "tipo": "temporal"},
    "antiguedad": {"etiqueta": "Antigüedad (días)", "tipo": "cuantitativa"},
    "sueldo": {"etiqueta": "Sueldo mensual CLP", "tipo": "cuantitativa"},
    "score": {"etiqueta": "Puntaje de encaje", "tipo": "cuantitativa"},
    "market_score": {"etiqueta": "Puntaje de mercado", "tipo": "cuantitativa"},
    "estado": {"etiqueta": "Mi estado", "tipo": "ordinal",
               "orden": ["guardada", "postulada", "entrevista", "oferta", "descartada"]},
}

DRILL = {"rol_familia": "rol", "region": "comuna", "tech": None, "empresa": None}

# min_n: tamaño de muestra mínimo del denominador. Debajo → se muestran datos individuales.
METRICAS: dict[str, dict] = {
    "ofertas": {"etiqueta": "Ofertas", "unidad": "n", "min_n": 1, "denominador": "—"},
    "activas": {"etiqueta": "Activas", "unidad": "n", "min_n": 1, "denominador": "—"},
    "nuevas": {"etiqueta": "Nuevas", "unidad": "n", "min_n": 1, "denominador": "—"},
    "cerradas": {"etiqueta": "Cerradas (posibles)", "unidad": "n", "min_n": 1, "denominador": "—"},
    "pct_con_sueldo": {"etiqueta": "Transparencia salarial", "unidad": "%", "min_n": 10,
                       "denominador": "ofertas del grupo"},
    "sueldo_p50": {"etiqueta": "Sueldo (mediana)", "unidad": "CLP", "min_n": 10, "min_n_aviso": 5,
                   "denominador": "ofertas con sueldo válido"},
    "sueldo_p25": {"etiqueta": "Sueldo (p25)", "unidad": "CLP", "min_n": 10, "min_n_aviso": 5,
                   "denominador": "ofertas con sueldo válido"},
    "sueldo_p75": {"etiqueta": "Sueldo (p75)", "unidad": "CLP", "min_n": 10, "min_n_aviso": 5,
                   "denominador": "ofertas con sueldo válido"},
    "demanda_tech": {"etiqueta": "Demanda de la tecnología", "unidad": "%", "min_n": 20,
                     "denominador": "ofertas con tecnologías conocidas"},
    "lift": {"etiqueta": "Lift (co-ocurrencia)", "unidad": "razón", "min_n": 5,
             "denominador": "ofertas con tecnologías conocidas"},
    "prima_tech": {"etiqueta": "Prima salarial (indicativa)", "unidad": "%", "min_n": 10,
                   "denominador": "ofertas con sueldo válido, estratificado por rol y seniority"},
    "score_medio": {"etiqueta": "Puntaje medio", "unidad": "0-100", "min_n": 5, "denominador": "ofertas"},
    "pct_encaje_alto": {"etiqueta": "Encaje alto", "unidad": "%", "min_n": 10, "denominador": "ofertas con encaje evaluado"},
    "antiguedad_mediana": {"etiqueta": "Antigüedad mediana", "unidad": "días", "min_n": 5, "denominador": "activas"},
    "vida_mediana": {"etiqueta": "Duración mediana (Kaplan-Meier)", "unidad": "días", "min_n": 20,
                     "denominador": "ofertas con evento 'aparecida'; requiere ≥5 cierres"},
    "pct_multifuente": {"etiqueta": "En 2+ fuentes", "unidad": "%", "min_n": 10, "denominador": "ofertas"},
    "concentracion_top10": {"etiqueta": "Concentración top-10 empresas", "unidad": "%", "min_n": 30,
                            "denominador": "ofertas con empresa"},
}

# umbrales mínimos de historia (días) para mostrar series temporales
HISTORIA_MIN = {"diaria": 7, "semanal": 28, "tendencia_tech": 42, "supervivencia_n": 20, "supervivencia_cierres": 5}

# columnas de mercado_diario y su etiqueta (whitelist de /api/historia/diaria)
MD_METRICAS = ("n_activas", "n_nuevas", "n_cerradas", "n_con_sueldo", "sueldo_p25", "sueldo_p50", "sueldo_p75")
MD_DIMS = ("rol_familia", "seniority", "fuente", "modalidad")
EVENTO_TIPOS = ("aparecida", "reaparecida", "cerrada", "sueldo", "score", "encaje", "modalidad")
ESTADOS = ("guardada", "postulada", "entrevista", "oferta", "descartada")
VISTA_TIPOS = ("ofertas", "analisis", "explorador")


def registro() -> dict:
    return {"v": 1, "dimensiones": DIMENSIONES, "metricas": METRICAS, "drill": DRILL,
            "historia_min": HISTORIA_MIN, "estados": list(ESTADOS)}
