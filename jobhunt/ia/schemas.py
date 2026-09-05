"""JSON schema estricto del modo lote (hoja)."""
from __future__ import annotations

_LOTE_SCHEMA = {
    "type": "object",
    "properties": {"ofertas": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "idx": {"type": "integer"},
            "opinion": {"type": "string"},
            "resumen": {"type": "string"},
            "fit_reason": {"type": "string"},
            "seniority_real": {"type": "string"},
            "rol_categoria": {"type": "string",
                              "enum": ["Full Stack", "Backend", "Frontend", "Data", "Mobile",
                                       "AI/ML", "Tech Lead", "DevOps/Cloud", "QA", "Software",
                                       "Seguridad", "Ingeniería no-software", "Analista/Empresa",
                                       "Profesor/Formación", "Soporte/TI", "No-tech", "Otro"]},
            "ingles": {"type": "string"},
            "idiomas": {"type": "array", "items": {"type": "object",
                "properties": {"idioma": {"type": "string"}, "nivel": {"type": "string"},
                                "excluyente": {"type": "boolean"}},
                "required": ["idioma", "nivel", "excluyente"], "additionalProperties": False}},
            "modalidad": {"type": "string"},
            "salario_clp_mensual": {"type": "integer"},
            "techs": {"type": "array", "items": {"type": "string"}},
            "red_flags": {"type": "array", "items": {"type": "string"}},
            "green_flags": {"type": "array", "items": {"type": "string"}},
            "benefits": {"type": "array", "items": {"type": "string"}}},
        "required": ["idx", "opinion", "resumen", "fit_reason", "seniority_real",
                      "rol_categoria", "ingles", "idiomas", "modalidad",
                      "salario_clp_mensual", "techs", "red_flags", "green_flags", "benefits"],
        "additionalProperties": False}},
        "required": ["ofertas"], "additionalProperties": False}}
