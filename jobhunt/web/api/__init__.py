"""API JSON de la web v2. Solo lectura, salvo estado de ofertas y vistas guardadas.

Toda ruta exige sesión (401 JSON). Las escrituras exigen además same-origin, cabecera X-JH y JSON.
Los parámetros de agrupación/métrica se validan contra whitelists de analytics.semantica;
nunca se interpolan en SQL sin pasar por ellas.
"""
from __future__ import annotations

import gzip
import json
import math
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ...analytics import semantica as sem
from ...analytics.estadistica import kaplan_meier
from ...analytics.materializar import canon_tech
from ...config import Config
from . import snapshot as snap

_MAX_VISTAS = 50


class EstadoIn(BaseModel):
    estado: Literal["guardada", "postulada", "entrevista", "oferta", "descartada"]
    nota: str = Field("", max_length=2000)


class VistaIn(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=60)
    tipo: Literal["ofertas", "analisis", "explorador"]
    spec: dict


def resolver_id(conn, ref: str) -> int | None:
    """Referencia de una oferta → su id numérico. Acepta el id (preferido) o el group_id viejo (enlaces antiguos)."""
    ref = (ref or "").strip()
    if ref.isdigit():
        fila = conn.execute("SELECT id FROM ofertas WHERE id=?", (int(ref),)).fetchone()
    else:
        fila = conn.execute("SELECT id FROM ofertas WHERE group_id=?", (ref[:300],)).fetchone()
    return fila[0] if fila else None


def _json(obj, status: int = 200) -> JSONResponse:
    return JSONResponse(obj, status_code=status, headers={"Cache-Control": "no-store"})


def crear_router(cfg: Config, conn_fn, autenticado_fn, misma_origen_fn) -> APIRouter:
    router = APIRouter(prefix="/api")

    def sesion(request: Request):
        if not autenticado_fn(request):
            raise HTTPException(status_code=401, detail="sin_sesion")

    def escritura(request: Request):
        sesion(request)
        if not misma_origen_fn(request) or request.headers.get("x-jh") != "1":
            raise HTTPException(status_code=403, detail="origen_no_valido")
        if request.method in ("PUT", "POST") and \
                not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="se_requiere_json")

    lectura = [Depends(sesion)]
    escribe = [Depends(escritura)]

    def _abrir():
        conn = conn_fn()
        snap.asegurar_materializado(conn, cfg)
        return conn

    # ---------------- snapshot / semántica / perfil ----------------

    @router.get("/semantica", dependencies=lectura)
    def semantica():
        return _json(sem.registro())

    @router.get("/snapshot", dependencies=lectura)
    def snapshot(request: Request, activas: int = 1, desde: str = ""):
        conn = _abrir()
        try:
            tag = snap.etag(conn)
            if request.headers.get("if-none-match") == tag and activas == 1 and not desde:
                return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
            cuerpo = json.dumps(snap.construir(conn, cfg, bool(activas), desde[:10]),
                                ensure_ascii=False, separators=(",", ":")).encode()
        finally:
            conn.close()
        cab = {"ETag": tag, "Cache-Control": "no-cache", "Content-Type": "application/json"}
        if "gzip" in request.headers.get("accept-encoding", ""):
            cuerpo = gzip.compress(cuerpo, 6)
            cab["Content-Encoding"] = "gzip"
        cab["Vary"] = "Accept-Encoding"
        return Response(cuerpo, headers=cab)

    @router.get("/perfil", dependencies=lectura)
    def perfil():
        p = cfg.profile
        return _json({"techs": sorted({canon_tech(t) for t in p.techs}), "roles": list(p.roles),
                      "salary_min": p.salary_min, "salary_max": p.salary_max, "years_exp": p.years_exp,
                      "min_fit": cfg.channel.min_fit_score, "titulo": p.title})

    # ---------------- detalle ----------------

    @router.get("/oferta/{gid:path}", dependencies=lectura)
    def oferta(gid: str):
        from ...domain.roles import fit_ok, is_dev
        from ...scoring import compute_market_score, compute_score
        from ..app import _etiqueta, _idiomas, _lista_json, _url_segura
        conn = _abrir()
        try:
            oid = resolver_id(conn, gid)
            fila = conn.execute("SELECT * FROM ofertas WHERE id=?", (oid,)).fetchone() if oid is not None else None
            if not fila:
                return _json({"error": "no_existe"}, 404)
            o = dict(fila)
            eventos = [dict(r) for r in conn.execute(
                "SELECT ts, tipo, antes, despues FROM oferta_eventos WHERE oferta_id=? ORDER BY id DESC LIMIT 50", (oid,))]
            est = conn.execute("SELECT estado, nota, actualizado FROM estado_oferta WHERE oferta_id=?", (oid,)).fetchone()
            techs = [r[0] for r in conn.execute("SELECT tech FROM oferta_techs WHERE oferta_id=? ORDER BY tech", (oid,))]
        finally:
            conn.close()
        score, desglose = compute_score(o, cfg)
        mscore, mdesglose = compute_market_score(o)
        fmt = lambda d: [{"clave": k, "etiqueta": _etiqueta(k), "valor": v} for k, v in d.items()]   # noqa: E731
        return _json({
            "id": o["id"], "ref": o["group_id"], "titulo": o["title"], "empresa": o["company"], "ubicacion": o["location"],
            "url": _url_segura(o["url"]), "fuente": (o["source"] or "").split(":")[0],
            "fuentes": [x for x in (o["sources"] or "").split(",") if x],
            "modalidad": o["modality_norm"], "modalidad_src": o["modality_source"],
            "rol": o["rol_categoria"], "rol_familia": o["rol_familia"], "seniority": o["seniority_norm"],
            "sueldo_texto": o["salary"], "sueldo": o["salary_clp"], "sueldo_status": o["salary_status"],
            "techs": techs, "idiomas": _idiomas(o["ai_idiomas"]), "exp_anios": o["exp_anios"],
            "first_seen": o["first_seen"], "last_seen": o["last_seen"], "actualizada": o["updated_at"],
            "active": o["active"], "score_guardado": o["score"], "score": score, "desglose": fmt(desglose),
            "market_score": mscore, "mdesglose": fmt(mdesglose), "encaje": o["ai_encaje"],
            "pasa_fit": fit_ok(o, cfg),
            "es_dev": is_dev(o.get("rol_categoria"), o.get("title") or "", cfg, o.get("description") or ""),
            "min_fit": cfg.channel.min_fit_score,
            "resumen": o["ai_resumen"], "opinion": o["ai_opinion"], "fit_reason": o["ai_fit_reason"],
            "alertas": _lista_json(o["ai_red_flags"]), "a_favor": _lista_json(o["ai_green_flags"]),
            "beneficios": _lista_json(o["ai_benefits"]), "descripcion": o["description"],
            "eventos": eventos, "estado": dict(est) if est else None})

    @router.get("/buscar", dependencies=lectura)
    def buscar(q: str = Query("", max_length=100)):
        q = q.strip().lower()
        if len(q) < 2:
            return _json({"ids": []})
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conn = conn_fn()
        try:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM ofertas WHERE active=1 AND (lower(title) LIKE ?1 ESCAPE '\\' OR "
                "lower(company) LIKE ?1 ESCAPE '\\' OR lower(description) LIKE ?1 ESCAPE '\\') LIMIT 5000", (like,))]
        finally:
            conn.close()
        return _json({"ids": ids})

    # ---------------- historia ----------------

    @router.get("/historia/diaria", dependencies=lectura)
    def historia_diaria(m: str = "n_activas", por: str = "*", desde: str = "", hasta: str = ""):
        if m not in sem.MD_METRICAS or (por != "*" and por not in sem.MD_DIMS):
            raise HTTPException(status_code=400, detail="parametro_invalido")
        conn = _abrir()
        try:
            # fijar todas las dimensiones en '*' salvo `por`, que debe ser != '*'
            cond = " AND ".join(f"{d} {'!=' if d == por else '='} '*'" for d in sem.MD_DIMS)
            filtros, params = [cond], []
            if desde:
                filtros.append("fecha >= ?")
                params.append(desde[:10])
            if hasta:
                filtros.append("fecha <= ?")
                params.append(hasta[:10])
            col = por if por != "*" else "rol_familia"
            rows = conn.execute(
                f"SELECT fecha, {col} AS clave, {m} AS valor, n_con_sueldo FROM mercado_diario "
                f"WHERE {' AND '.join(filtros)} ORDER BY fecha", params).fetchall()
            dias = conn.execute("SELECT COUNT(DISTINCT fecha) FROM mercado_diario").fetchone()[0]
        finally:
            conn.close()
        fechas = sorted({r["fecha"] for r in rows})
        clave_de = lambda r: "*" if por == "*" else r["clave"]   # noqa: E731
        series: dict[str, dict] = {}
        for r in rows:
            s = series.setdefault(clave_de(r), {"clave": clave_de(r), "valores": {}, "n": {}})
            s["valores"][r["fecha"]] = r["valor"]
            s["n"][r["fecha"]] = r["n_con_sueldo"]
        return _json({"fechas": fechas, "dias_historia": dias, "metrica": m, "por": por,
                      "series": [{"clave": s["clave"], "valores": [s["valores"].get(f) for f in fechas],
                                  "n_con_sueldo": [s["n"].get(f) for f in fechas]} for s in series.values()]})

    @router.get("/historia/techs", dependencies=lectura)
    def historia_techs(techs: str = "", rol_familia: str = "*", desde: str = ""):
        pedidas = [t.strip() for t in techs.split(",") if t.strip()][:20]
        conn = _abrir()
        try:
            params: list = [rol_familia[:40]]
            filtro = "rol_familia = ?"
            if pedidas:
                filtro += f" AND tech IN ({','.join('?' * len(pedidas))})"
                params += pedidas
            if desde:
                filtro += " AND semana >= ?"
                params.append(desde[:8])
            rows = conn.execute(f"SELECT semana, tech, n_activas, n_nuevas, n_base FROM mercado_tech_semanal "
                                f"WHERE {filtro} ORDER BY semana", params).fetchall()
            semanas_tot = conn.execute("SELECT COUNT(DISTINCT semana) FROM mercado_tech_semanal").fetchone()[0]
        finally:
            conn.close()
        semanas = sorted({r["semana"] for r in rows})
        por: dict[str, dict] = {}
        for r in rows:
            s = por.setdefault(r["tech"], {})
            s[r["semana"]] = (r["n_activas"], r["n_base"])
        return _json({"semanas": semanas, "semanas_historia": semanas_tot, "series": [{
            "tech": t, "demanda": [(v[0] / v[1] if (v := s.get(w)) and v[1] else None) for w in semanas],
            "n_activas": [(s.get(w) or (None, None))[0] for w in semanas],
            "n_base": [(s.get(w) or (None, None))[1] for w in semanas]} for t, s in por.items()]})

    @router.get("/historia/eventos", dependencies=lectura)
    def historia_eventos(tipo: str = "", desde: str = "", hasta: str = "", limite: int = Query(100, ge=1, le=500)):
        if tipo and tipo not in sem.EVENTO_TIPOS:
            raise HTTPException(status_code=400, detail="parametro_invalido")
        filtros, params = ["1=1"], []
        if tipo:
            filtros.append("tipo = ?")
            params.append(tipo)
        if desde:
            filtros.append("ts >= ?")
            params.append(desde[:10])
        if hasta:
            filtros.append("ts <= ?")
            params.append(hasta[:10] + "Z")
        conn = _abrir()
        try:
            rows = [dict(r) for r in conn.execute(
                f"SELECT ts, oferta_id, title, company, rol_familia, fuente, tipo, antes, despues "
                f"FROM oferta_eventos WHERE {' AND '.join(filtros)} ORDER BY id DESC LIMIT ?", [*params, limite])]
        finally:
            conn.close()
        return _json({"eventos": rows})

    @router.get("/historia/supervivencia", dependencies=lectura)
    def historia_supervivencia(rol_familia: str = "", fuente: str = ""):
        conn = _abrir()
        try:
            filtros, params = ["a.tipo = 'aparecida'"], []
            if rol_familia:
                filtros.append("a.rol_familia = ?")
                params.append(rol_familia[:40])
            if fuente:
                filtros.append("a.fuente = ?")
                params.append(fuente[:30])
            rows = conn.execute(f"""SELECT a.despues AS t0,
                (SELECT MAX(c.id) FROM oferta_eventos c WHERE c.oferta_id=a.oferta_id AND c.tipo='cerrada') AS cid,
                (SELECT MAX(r.id) FROM oferta_eventos r WHERE r.oferta_id=a.oferta_id AND r.tipo='reaparecida') AS rid,
                (SELECT c.ts FROM oferta_eventos c WHERE c.id = (SELECT MAX(c2.id) FROM oferta_eventos c2
                    WHERE c2.oferta_id=a.oferta_id AND c2.tipo='cerrada')) AS tc
                FROM oferta_eventos a WHERE {' AND '.join(filtros)}""", params).fetchall()
        finally:
            conn.close()
        ahora = datetime.now(timezone.utc)
        dur, obs = [], []
        for r in rows:
            try:
                t0 = datetime.fromisoformat(r["t0"].replace("Z", "+00:00"))
            except ValueError:
                continue
            cerrada = r["cid"] is not None and (r["rid"] is None or r["rid"] < r["cid"])
            fin = datetime.fromisoformat(r["tc"].replace("Z", "+00:00")) if cerrada and r["tc"] else ahora
            dur.append(max(0.0, (fin - t0).total_seconds() / 86400))
            obs.append(bool(cerrada))
        n, cierres = len(dur), sum(obs)
        minimo = sem.HISTORIA_MIN
        if n < minimo["supervivencia_n"] or cierres < minimo["supervivencia_cierres"]:
            return _json({"oculto": True, "n": n, "cierres": cierres, "minimo_n": minimo["supervivencia_n"],
                          "minimo_cierres": minimo["supervivencia_cierres"]})
        pts, med = kaplan_meier([round(d, 2) for d in dur], obs)
        return _json({"oculto": False, "n": n, "cierres": cierres, "mediana": med,
                      "t": [p[0] for p in pts], "s": [round(p[1], 4) for p in pts]})

    # ---------------- fuentes ----------------

    @router.get("/fuentes", dependencies=lectura)
    def fuentes(barridos: int = Query(30, ge=1, le=100)):
        from ... import salud
        conn = _abrir()
        try:
            hist = salud._historial(conn, barridos)
            cob = conn.execute("""SELECT substr(source,1,instr(source||':',':')-1) AS fuente, COUNT(*) AS n,
                SUM(salary_clp IS NOT NULL) AS sueldo, SUM(techs != '') AS techs,
                SUM(modality_norm != '') AS modalidad, SUM(region NOT IN ('', 'desconocida')) AS ubicacion,
                SUM(seniority_norm != '') AS seniority, SUM(length(description) > 200) AS descripcion,
                SUM(ai_ingles NOT IN ('', 'desconocido')) AS ingles, SUM(exp_anios IS NOT NULL) AS experiencia
                FROM ofertas WHERE active = 1 GROUP BY fuente ORDER BY n DESC""").fetchall()
            corrida = conn.execute("SELECT valor FROM analytics_meta WHERE clave='ultima_corrida'").fetchone()
        finally:
            conn.close()
        nombres = sorted({f for h in hist for f in h["fuentes"]})
        estado = []
        for f in nombres:
            racha = salud.racha_ceros(hist, f)
            estado.append({"fuente": f, "nombre": salud._nombre(f), "racha_ceros": racha,
                           "estado": "caida" if racha >= cfg.alerts.source_sweeps else "vigilar" if racha else "ok",
                           "serie": [h["fuentes"].get(f) for h in hist]})
        return _json({"barridos": [h["ts"] for h in hist], "fuentes": estado,
                      "cobertura": [dict(r) for r in cob],
                      "analytics": json.loads(corrida[0]) if corrida else None})

    # ---------------- estado de ofertas (escritura) ----------------

    @router.put("/estado/{gid:path}", dependencies=escribe)
    def poner_estado(gid: str, cuerpo: EstadoIn):
        conn = _abrir()
        try:
            oid = resolver_id(conn, gid)
            if oid is None:
                return _json({"error": "no_existe"}, 404)
            ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute("""INSERT INTO estado_oferta (oferta_id, estado, nota, actualizado) VALUES (?,?,?,?)
                ON CONFLICT(oferta_id) DO UPDATE SET estado=excluded.estado, nota=excluded.nota,
                actualizado=excluded.actualizado""", (oid, cuerpo.estado, cuerpo.nota, ahora))
            conn.commit()
        finally:
            conn.close()
        return _json({"id": oid, "estado": cuerpo.estado, "nota": cuerpo.nota, "actualizado": ahora})

    @router.delete("/estado/{gid:path}", dependencies=escribe)
    def quitar_estado(gid: str):
        conn = _abrir()
        try:
            oid = resolver_id(conn, gid)
            if oid is not None:
                conn.execute("DELETE FROM estado_oferta WHERE oferta_id=?", (oid,))
                conn.commit()
        finally:
            conn.close()
        return Response(status_code=204)

    @router.get("/estados", dependencies=lectura)
    def estados():
        conn = _abrir()
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM estado_oferta ORDER BY actualizado DESC")]
        finally:
            conn.close()
        return _json({"estados": rows})

    # ---------------- vistas guardadas ----------------

    @router.get("/vistas", dependencies=lectura)
    def listar_vistas():
        conn = _abrir()
        try:
            rows = [{**dict(r), "spec": json.loads(r["spec"])} for r in conn.execute(
                "SELECT id, nombre, tipo, spec, creada FROM vistas_guardadas ORDER BY id DESC")]
        finally:
            conn.close()
        return _json({"vistas": rows})

    @router.post("/vistas", dependencies=escribe)
    def crear_vista(v: VistaIn):
        spec = json.dumps(v.spec, ensure_ascii=False, separators=(",", ":"))
        if len(spec) > 4000:
            raise HTTPException(status_code=422, detail="spec_demasiado_grande")
        conn = _abrir()
        try:
            if conn.execute("SELECT COUNT(*) FROM vistas_guardadas").fetchone()[0] >= _MAX_VISTAS:
                raise HTTPException(status_code=422, detail="demasiadas_vistas")
            ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                cur = conn.execute("INSERT INTO vistas_guardadas (nombre, tipo, spec, creada) VALUES (?,?,?,?)",
                                   (v.nombre.strip(), v.tipo, spec, ahora))
            except Exception:
                raise HTTPException(status_code=409, detail="nombre_repetido")
            conn.commit()
            vid = cur.lastrowid
        finally:
            conn.close()
        return _json({"id": vid, "nombre": v.nombre.strip(), "tipo": v.tipo, "spec": v.spec, "creada": ahora}, 201)

    @router.delete("/vistas/{vid}", dependencies=escribe)
    def borrar_vista(vid: int):
        conn = _abrir()
        try:
            conn.execute("DELETE FROM vistas_guardadas WHERE id=?", (vid,))
            conn.commit()
        finally:
            conn.close()
        return Response(status_code=204)

    return router
