"""Tabla única de tecnologías: abreviatura de columna, nombre canónico, alias.

Consolida 3 tablas divergentes que vivían en enrich.py (_TECH_ABBR), charts.py
(_TECH_CANON) y scoring.py (MARKET_TECHS_ABBR + _MARKET_TECHS_TITLE_RE).
Verificado antes de mover: ABBR_BY_ALIAS/NAME_BY_ABBR/MARKET_ABBRS derivados
de TECHS son idénticos byte a byte a las 3 tablas viejas (ver reporte del
refactor). Los 3 módulos viejos importan estos nombres por compat.

Comparación cubierta (antes → después):
  - _TECH_ABBR (enrich, 36 alias → 29 abreviaturas)     == ABBR_BY_ALIAS
  - _TECH_CANON (charts, 49 alias → 44 nombres)          == NAME_BY_ABBR
  - MARKET_TECHS_ABBR (scoring, 27 abreviaturas)         == MARKET_ABBRS
  - _MARKET_TECHS_TITLE_RE (scoring)                     == TITLE_RE
  15 alias de charts sin abreviatura propia (c++, php, git, linux, bash,
  airflow, spark, hadoop, snowflake, databricks, tableau, power bi, sap,
  abap, cobol) quedan como entradas (abbr=None) que solo alimentan
  NAME_BY_ABBR, igual que antes. 'django' no tenía entrada en _TECH_CANON
  (name=None aquí) — se preserva: no aparece en NAME_BY_ABBR, igual que antes.
  'Terraform' en MARKET_TECHS_ABBR es un duplicado histórico de 'TF' (no es
  una abreviatura de columna real) — se preserva explícito, no derivado.
"""
from __future__ import annotations

import re

# (abbr | None, nombre_canonico | None, [alias...], en_market_abbrs)
TECHS: list[tuple[str | None, str | None, list[str], bool]] = [
    ("Py", "Python", ["python", "py"], True),
    ("Java", "Java", ["java"], True),
    ("TS", "TypeScript", ["ts", "typescript"], True),
    ("JS", "JavaScript", ["js", "javascript"], True),
    ("React", "React", ["react"], True),
    ("Angular", "Angular", ["angular"], True),
    ("Vue", "Vue", ["vue"], False),
    ("Node", "Node.js", ["node", "node.js"], True),
    ("K8s", "Kubernetes", ["kubernetes", "k8s"], True),
    ("Docker", "Docker", ["docker"], True),
    ("AWS", "AWS", ["aws"], True),
    ("GCP", "GCP", ["gcp"], True),
    ("Azure", "Azure", ["azure"], True),
    ("TF", "Terraform", ["terraform", "tf"], True),
    ("Scala", "Scala", ["scala"], True),
    ("Go", "Go", ["golang", "go"], True),
    (".NET", ".NET", [".net"], True),
    ("C#", "C#", ["c#"], False),
    ("SQL", "SQL", ["sql"], True),
    ("Postgres", "PostgreSQL", ["postgres", "postgresql"], True),
    ("Mongo", "MongoDB", ["mongo", "mongodb"], True),
    ("Redis", "Redis", ["redis"], True),
    ("Kafka", "Kafka", ["kafka"], True),
    ("FastAPI", "FastAPI", ["fastapi"], True),
    ("Django", None, ["django"], True),
    ("Spring", "Spring", ["spring"], True),
    ("NiFi", "NiFi", ["nifi"], True),
    ("Jenkins", "Jenkins", ["jenkins"], False),
    ("CI/CD", "CI/CD", ["ci/cd"], True),
    (None, "C++", ["c++"], False),
    (None, "PHP", ["php"], False),
    (None, "Git", ["git"], False),
    (None, "Linux", ["linux"], False),
    (None, "Bash", ["bash"], False),
    (None, "Airflow", ["airflow"], False),
    (None, "Spark", ["spark"], False),
    (None, "Hadoop", ["hadoop"], False),
    (None, "Snowflake", ["snowflake"], False),
    (None, "Databricks", ["databricks"], False),
    (None, "Tableau", ["tableau"], False),
    (None, "Power BI", ["power bi"], False),
    (None, "SAP", ["sap"], False),
    (None, "ABAP", ["abap"], False),
    (None, "COBOL", ["cobol"], False),
]

# alias (lowercase) -> abreviatura de columna 'techs'. == enrich._TECH_ABBR
ABBR_BY_ALIAS: dict[str, str] = {
    alias: abbr for abbr, _name, aliases, _mkt in TECHS if abbr for alias in aliases
}

# alias (lowercase) -> nombre canónico para mostrar. == charts._TECH_CANON
NAME_BY_ABBR: dict[str, str] = {
    alias: name for _abbr, name, aliases, _mkt in TECHS if name for alias in aliases
}

# abreviaturas que cuentan en el market score de techs. == scoring.MARKET_TECHS_ABBR
# ('Terraform' es un duplicado histórico de 'TF', preservado tal cual)
MARKET_ABBRS: set[str] = {
    abbr for abbr, _name, _aliases, mkt in TECHS if abbr and mkt
} | {"Terraform"}

# regex de techs mencionadas en el título. == scoring._MARKET_TECHS_TITLE_RE
# ojo boundaries: 'go' con lookahead (evita falsos positivos de "go" suelto)
# y '.net' con \b.\bnet\b (el punto no es \w, ver escape original).
TITLE_RE = re.compile(
    r"\bpython\b|\bjava\b|\bscala\b|\btypescript\b|\bjavascript\b|\bkubernetes\b|\bk8s\b|"
    r"\bdocker\b|\bnifi\b|\breact\b|\bangular\b|\bspring\b|\b\.net\b|\baws\b|\bgcp\b|"
    r"\bazure\b|\bgolang\b|\bgo\b(?=\s*(?:lang|developer|dev\b|engineer))|\bnode\b|"
    r"\bpostgres\b|\bgraphql\b", re.I)
