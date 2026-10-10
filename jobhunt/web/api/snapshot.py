"""Snapshot columnar con diccionarios (v=2): todo lo que el navegador necesita para filtrar y agregar
sin viajes al servidor. Sin descripción ni opinión IA (van en el detalle)."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from ...config import Config
from ...domain.fechas import age_days

VERSION = 2
LIMITE = 20000

_INGLES = {"": "", "desconocido": "", "no": "no", "deseable": "deseable", "requerido": "requerido",
           "excluyente": "requerido"}


class _Dic:
    def __init__(self):
        self.items: list[str] = []
        self.idx: dict[str, int] = {}

    def __call__(self, v: str) -> int:
        i = self.idx.get(v)
        if i is None:
            i = self.idx[v] = len(self.items)
            self.items.append(v)
        return i


def asegurar_materializado(conn, cfg: Config) -> None:
    """Si nunca se materializó (web levantada antes del primer barrido), lo hace una vez."""
    from ...analytics.materializar import materializar
    from ...analytics.esquema import asegurar_esquema
    asegurar_esquema(conn)
    if not conn.execute("SELECT 1 FROM analytics_meta WHERE clave='ultima_corrida'").fetchone():
        materializar(conn, cfg)


def etag(conn) -> str:
    from ...analytics.normalizar import VERSION as NV
    n, mx = conn.execute("SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM ofertas").fetchone()
    corrida = conn.execute("SELECT valor FROM analytics_meta WHERE clave='ultimo_inicio'").fetchone()
    est = conn.execute("SELECT COUNT(*), COALESCE(MAX(actualizado),'') FROM estado_oferta").fetchone()
    base = f"{VERSION}|{NV}|{n}|{mx}|{corrida[0] if corrida else ''}|{est[0]}|{est[1]}"
    return '"' + hashlib.sha1(base.encode()).hexdigest()[:20] + '"'


def sueldo_valido(salary_clp, status, cfg: Config) -> bool:
    return (salary_clp is not None and cfg.analytics.band_min <= salary_clp <= cfg.analytics.band_max
            and (status or "") in ("trusted", ""))


def construir(conn, cfg: Config, activas: bool = True, desde: str = "") -> dict:
    where, params = [], []
    if activas:
        where.append("o.active = 1")
    if desde:
        where.append("o.first_seen >= ?")
        params.append(desde)
    filas = conn.execute(f"""SELECT o.*, COALESCE(p.cerrada, 0) AS posible_cerrada, COALESCE(e.estado, '') AS estado
        FROM ofertas o LEFT JOIN oferta_prev p ON p.group_id = o.group_id
        LEFT JOIN estado_oferta e ON e.group_id = o.group_id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY o.first_seen DESC LIMIT ?""", [*params, LIMITE + 1]).fetchall()
    truncado = len(filas) > LIMITE
    filas = filas[:LIMITE]
    ids = {r["group_id"] for r in filas}

    techs, tags = defaultdict(list), defaultdict(lambda: defaultdict(list))
    for gid, t in conn.execute("SELECT group_id, tech FROM oferta_techs"):
        if gid in ids:
            techs[gid].append(t)
    for gid, tipo, v in conn.execute("SELECT group_id, tipo, valor FROM oferta_tags"):
        if gid in ids:
            tags[gid][tipo].append(v)

    d = {k: _Dic() for k in ("empresa", "fuente", "rol", "tech", "tag", "region", "comuna")}
    c: dict[str, list] = defaultdict(list)
    for r in filas:
        r = dict(r)
        gid = r["group_id"]
        c["id"].append(gid)
        c["titulo"].append(r["title"] or "")
        c["url"].append(r["url"] or "")
        c["empresa"].append(d["empresa"](r["company"] or ""))
        c["empresa_canon"].append(r["company_canon"] or "")
        fuentes = [x for x in (r["sources"] or "").split(",") if x] or [(r["source"] or "").split(":")[0]]
        c["fuente"].append(d["fuente"]((r["source"] or "").split(":")[0]))
        c["fuentes"].append([d["fuente"](f) for f in fuentes])
        c["rol"].append(d["rol"](r["rol_categoria"] or ""))
        c["rol_familia"].append(r["rol_familia"] or "")
        c["seniority"].append(r["seniority_norm"] or "")
        c["modalidad"].append(r["modality_norm"] or "")
        c["modalidad_src"].append(r["modality_source"] or "")
        c["empleo"].append(r["employment_norm"] or "")
        c["region"].append(d["region"](r["region"] or ""))
        c["comuna"].append(d["comuna"](r["comuna"] or ""))
        c["sueldo"].append(r["salary_clp"])
        c["sueldo_min"].append(r["salary_clp_min"])
        c["sueldo_max"].append(r["salary_clp_max"])
        c["sueldo_valido"].append(1 if sueldo_valido(r["salary_clp"], r["salary_status"], cfg) else 0)
        c["techs"].append([d["tech"](t) for t in sorted(techs[gid])]
                          if (r["techs"] or "").strip() else None)
        for col, tipo in (("beneficios", "beneficio"), ("alertas", "rojo"), ("a_favor", "verde")):
            c[col].append([d["tag"](v) for v in sorted(tags[gid][tipo])])
        c["score"].append(r["score"] or 0)
        c["market_score"].append(r["market_score"] or 0)
        c["encaje"].append(r["ai_encaje"] or "")
        c["ingles"].append(_INGLES.get((r["ai_ingles"] or "").strip().lower(), ""))
        c["first_seen"].append((r["first_seen"] or "")[:10])
        c["fecha_pub"].append(r["date_canonical"] or "")
        c["antiguedad"].append(age_days(r))
        c["applicants"].append(r["applicants_n"])
        c["exp_anios"].append(r["exp_anios"])
        c["staffing"].append(r["staffing"] or 0)
        c["n_fuentes"].append(r["n_fuentes"] or 1)
        c["active"].append(r["active"])
        c["posible_cerrada"].append(r["posible_cerrada"])
        c["resumen"].append((r["ai_resumen"] or "")[:240])
        c["estado"].append(r["estado"])
    return {"v": VERSION, "n": len(filas), "truncado": truncado,
            "dicts": {k: v.items for k, v in d.items()}, "cols": dict(c)}
