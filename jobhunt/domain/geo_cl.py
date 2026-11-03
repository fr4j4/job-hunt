"""Diccionario geográfico de Chile para normalizar `ofertas.location` → (región, comuna).

Cubre las 16 regiones, las comunas del Gran Santiago y las capitales/ciudades que
aparecen en el pool real. Sin geodatos externos. Todo en minúsculas sin tildes.
"""
from __future__ import annotations

REGIONES: dict[str, str] = {   # clave normalizada → nombre de región
    "metropolitana": "Metropolitana", "arica": "Arica y Parinacota", "tarapaca": "Tarapacá",
    "antofagasta": "Antofagasta", "atacama": "Atacama", "coquimbo": "Coquimbo",
    "valparaiso": "Valparaíso", "ohiggins": "O'Higgins", "maule": "Maule", "nuble": "Ñuble",
    "biobio": "Biobío", "araucania": "La Araucanía", "los rios": "Los Ríos",
    "los lagos": "Los Lagos", "aysen": "Aysén", "magallanes": "Magallanes",
}

_RM = ("santiago", "las condes", "providencia", "vitacura", "lo barnechea", "nunoa", "la reina",
       "penalolen", "macul", "san joaquin", "la florida", "puente alto", "la cisterna", "san miguel",
       "san ramon", "la granja", "la pintana", "el bosque", "lo espejo", "pedro aguirre cerda",
       "cerrillos", "maipu", "estacion central", "quinta normal", "pudahuel", "cerro navia",
       "lo prado", "renca", "quilicura", "conchali", "huechuraba", "independencia", "recoleta",
       "san bernardo", "lampa", "colina", "calera de tango", "paine", "penaflor", "talagante",
       "melipilla", "buin", "padre hurtado", "san jose de maipo", "til til", "pirque", "el monte",
       "isla de maipo", "curacavi", "maria pinto", "alhue")

# comuna/ciudad fuera de la RM → región
_FUERA: dict[str, str] = {
    "arica": "arica", "iquique": "tarapaca", "alto hospicio": "tarapaca",
    "antofagasta": "antofagasta", "calama": "antofagasta", "mejillones": "antofagasta",
    "tocopilla": "antofagasta", "taltal": "antofagasta", "sierra gorda": "antofagasta",
    "copiapo": "atacama", "vallenar": "atacama", "caldera": "atacama",
    "la serena": "coquimbo", "coquimbo": "coquimbo", "ovalle": "coquimbo", "andacollo": "coquimbo",
    "valparaiso": "valparaiso", "vina del mar": "valparaiso", "quilpue": "valparaiso",
    "villa alemana": "valparaiso", "concon": "valparaiso", "quillota": "valparaiso",
    "san antonio": "valparaiso", "los andes": "valparaiso", "casablanca": "valparaiso",
    "san felipe": "valparaiso",
    "rancagua": "ohiggins", "san vicente": "ohiggins", "machali": "ohiggins", "rengo": "ohiggins",
    "san fernando": "ohiggins", "santa cruz": "ohiggins",
    "talca": "maule", "curico": "maule", "linares": "maule", "cauquenes": "maule",
    "chillan": "nuble", "chillan viejo": "nuble",
    "concepcion": "biobio", "talcahuano": "biobio", "hualpen": "biobio", "san pedro de la paz": "biobio",
    "coronel": "biobio", "los angeles": "biobio", "lota": "biobio", "chiguayante": "biobio",
    "temuco": "araucania", "villarrica": "araucania", "pucon": "araucania", "angol": "araucania",
    "valdivia": "los rios", "la union": "los rios",
    "puerto montt": "los lagos", "osorno": "los lagos", "puerto varas": "los lagos",
    "castro": "los lagos", "ancud": "los lagos",
    "coyhaique": "aysen", "chile chico": "aysen", "puerto aysen": "aysen",
    "punta arenas": "magallanes", "puerto natales": "magallanes",
    "yerbas buenas": "maule",
}

# ciudades con varias comunas/ruido: se prueban de la más larga a la más corta
COMUNAS: dict[str, tuple[str, str]] = {   # clave normalizada → (región clave, nombre bonito)
    **{c: ("metropolitana", c.title()) for c in _RM},
    **{c: (r, c.title()) for c, r in _FUERA.items()},
}
COMUNAS["nunoa"] = ("metropolitana", "Ñuñoa")
COMUNAS["penalolen"] = ("metropolitana", "Peñalolén")
COMUNAS["maipu"] = ("metropolitana", "Maipú")
COMUNAS["penaflor"] = ("metropolitana", "Peñaflor")
COMUNAS["vina del mar"] = ("valparaiso", "Viña del Mar")
COMUNAS["concepcion"] = ("biobio", "Concepción")
COMUNAS["copiapo"] = ("atacama", "Copiapó")
COMUNAS["valparaiso"] = ("valparaiso", "Valparaíso")
COMUNAS["machali"] = ("ohiggins", "Machalí")
COMUNAS["chillan"] = ("nuble", "Chillán")
COMUNAS["curico"] = ("maule", "Curicó")
COMUNAS["pucon"] = ("araucania", "Pucón")

# pistas de región cuando no hay comuna reconocible
PISTAS_REGION: tuple[tuple[str, str], ...] = (
    ("region metropolitana", "metropolitana"), ("metropolitan", "metropolitana"),
    ("valparaiso region", "valparaiso"), ("region de valparaiso", "valparaiso"),
    ("biobio", "biobio"), ("tarapaca", "tarapaca"), ("antofagasta", "antofagasta"),
    ("coquimbo", "coquimbo"), ("atacama", "atacama"), ("araucania", "araucania"),
    ("los lagos", "los lagos"), ("los rios", "los rios"), ("magallanes", "magallanes"),
    ("region xiv", "los rios"), ("region xv", "arica"), ("region xiii", "metropolitana"),
)

REMOTO_PISTAS = ("remoto", "remote", "latam", "latin america", "latinoamerica", "teletrabajo")
