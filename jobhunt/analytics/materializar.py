"""Materializa la capa analítica a partir de `ofertas` (idempotente, aditiva).

Pasos: (1) columnas derivadas · (2) oferta_techs · (3) oferta_tags · (4) eventos ·
(5) mercado_diario + mercado_tech_semanal · (6) metadatos de la corrida.
Se engancha al final de cada barrido (cli.cmd_run) dentro de try/except: nunca tumba el barrido.
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import Config
from ..domain.techs import NAME_BY_ABBR
from ..logging_setup import get_logger
from . import normalizar as nz
from .estadistica import percentil

log = get_logger(__name__)

_ALIAS_EMPRESA = Path(__file__).with_name("empresa_alias.json")
_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _iso(ts: str) -> str:
    """Cualquier ISO de la DB → 'YYYY-MM-DDTHH:MM:SSZ' (orden lexicográfico consistente)."""
    ts = str(ts or "")
    return ts[:19] + "Z" if len(ts) >= 19 else ts


def _alias_empresa() -> dict[str, str]:
    try:
        return {str(k): str(v) for k, v in json.loads(_ALIAS_EMPRESA.read_text()).items()}
    except (OSError, ValueError):
        return {}


def _lista_json(raw) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def canon_tech(abbr: str) -> str:
    return NAME_BY_ABBR.get(abbr.lower(), abbr)


# ---------------- 1. columnas derivadas ----------------

def derivar_fila(r: dict, cfg: Config, alias: dict[str, str]) -> dict:
    mn = nz.norm_modalidad(r.get("modality"))
    if mn:
        msrc = "oficial"
    elif cfg.analytics.infer_modality:
        mn = nz.inferir_modalidad(r.get("title"), r.get("description"), r.get("remote_official"))
        msrc = "inferida" if mn else ""
    else:
        mn, msrc = "", ""
    smin, smax, ssrc = nz.sueldo_rango(r.get("salary"), r.get("description"))
    region, comuna = nz.norm_ubicacion(r.get("location"))
    srcs = [x for x in (r.get("sources") or "").split(",") if x]
    from ..scoring import _staffing, _years_from_description
    exp = r.get("years_official")
    if exp is None:
        exp = _years_from_description(r.get("description") or "")
    return {
        "exp_anios": exp, "staffing": int(_staffing(r)),
        "salary_clp": smin if smin == smax else (smin + smax) // 2 if smin and smax else None,
        "salary_clp_min": smin, "salary_clp_max": smax, "salary_norm_src": ssrc,
        "modality_norm": mn, "modality_source": msrc,
        "seniority_norm": nz.norm_seniority(r.get("seniority_real"), r.get("seniority_oficial")),
        "rol_familia": nz.norm_rol_familia(r.get("rol_categoria")),
        "employment_norm": nz.norm_empleo(r.get("employment_type")),
        "applicants_n": nz.norm_applicants(r.get("applicants_hint")),
        "region": region, "comuna": comuna,
        "company_canon": nz.norm_empresa(r.get("company"), alias),
        "n_fuentes": max(1, len(srcs)),
        "norm_version": nz.VERSION,
    }


def _paso_derivar(conn, cfg, desde: str, full: bool) -> list[str]:
    """→ ids re-derivados (para techs/tags)."""
    alias = _alias_empresa()
    where = "" if full else ("WHERE norm_version != ? OR norm_version IS NULL OR updated_at >= ?")
    params = () if full else (nz.VERSION, desde)
    cambiados = []
    lote = 0
    for row in conn.execute(f"SELECT * FROM ofertas {where}", params).fetchall():
        r = dict(row)
        nuevo = derivar_fila(r, cfg, alias)
        cambiados.append(r["id"])
        if all(r.get(k) == v for k, v in nuevo.items()):
            continue
        sets = ", ".join(f"{k}=?" for k in nuevo)
        conn.execute(f"UPDATE ofertas SET {sets} WHERE id=?", (*nuevo.values(), r["id"]))
        lote += 1
        if lote % 500 == 0:
            conn.commit()
    conn.commit()
    return cambiados


# ---------------- 2/3. techs y tags ----------------

def _paso_techs_tags(conn, gids: list[str]) -> None:
    for i in range(0, len(gids), 500):
        trozo = gids[i:i + 500]
        q = ",".join("?" * len(trozo))
        for row in conn.execute(
                f"SELECT id, techs, ai_benefits, ai_red_flags, ai_green_flags, ai_idiomas "
                f"FROM ofertas WHERE id IN ({q})", trozo).fetchall():
            gid = row["id"]
            techs = {canon_tech(t.strip()) for t in (row["techs"] or "").split(";") if t.strip()}
            ya = {r[0] for r in conn.execute("SELECT tech FROM oferta_techs WHERE oferta_id=?", (gid,))}
            if techs != ya:
                conn.execute("DELETE FROM oferta_techs WHERE oferta_id=?", (gid,))
                conn.executemany("INSERT OR IGNORE INTO oferta_techs VALUES (?,?)", [(gid, t) for t in sorted(techs)])
            tags: set[tuple[str, str]] = set()
            for tipo, col in (("beneficio", "ai_benefits"), ("rojo", "ai_red_flags"), ("verde", "ai_green_flags")):
                for v in _lista_json(row[col]):
                    if isinstance(v, str) and v.strip():
                        tags.add((tipo, v.strip().lower()[:120]))
            for it in _lista_json(row["ai_idiomas"]):
                if isinstance(it, dict) and it.get("idioma"):
                    idi = nz._norm(str(it.get("idioma"))).strip()
                    niv = nz._norm(str(it.get("nivel") or "")).strip()
                    tags.add(("idioma", f"{idi}:{niv}:{'excluyente' if it.get('excluyente') else ''}"[:120]))
            ya_t = {(r[0], r[1]) for r in conn.execute("SELECT tipo, valor FROM oferta_tags WHERE oferta_id=?", (gid,))}
            if tags != ya_t:
                conn.execute("DELETE FROM oferta_tags WHERE oferta_id=?", (gid,))
                conn.executemany("INSERT OR IGNORE INTO oferta_tags VALUES (?,?,?)", [(gid, a, b) for a, b in sorted(tags)])
    conn.execute("DELETE FROM oferta_techs WHERE oferta_id NOT IN (SELECT id FROM ofertas)")
    conn.execute("DELETE FROM oferta_tags WHERE oferta_id NOT IN (SELECT id FROM ofertas)")
    conn.commit()


# ---------------- 4. eventos ----------------

def _umbrales_cierre(conn, k: int) -> list[tuple[frozenset, str]]:
    """Barridos recientes (ts desc) con las fuentes que rindieron n>0."""
    out = []
    for ts, summ in conn.execute("SELECT ts, sources_summary FROM scan_log ORDER BY id DESC LIMIT 60"):
        try:
            d = json.loads(summ) if summ else {}
        except (ValueError, TypeError):
            d = {}
        sanas = frozenset(f for f, v in (d.items() if isinstance(d, dict) else []) if (v or {}).get("n", 0) > 0)
        out.append((sanas, _iso(ts)))
    return out


def _umbral(barridos, fuentes: frozenset, k: int, memo: dict) -> str | None:
    """ts del k-ésimo barrido más reciente en que alguna de sus fuentes rindió n>0."""
    if fuentes in memo:
        return memo[fuentes]
    cuenta, res = 0, None
    for sanas, ts in barridos:
        if sanas & fuentes:
            cuenta += 1
            if cuenta == k:
                res = ts
                break
    memo[fuentes] = res
    return res


def _paso_eventos(conn, cfg: Config, ahora: str, scan_id) -> dict:
    barridos = _umbrales_cierre(conn, cfg.analytics.close_after_sweeps)
    memo: dict = {}
    prev = {r["oferta_id"]: dict(r) for r in conn.execute("SELECT * FROM oferta_prev")}
    con_aparecida = {r[0] for r in conn.execute("SELECT oferta_id FROM oferta_eventos WHERE tipo='aparecida'")}
    evs, n_ev = [], Counter()

    def ev(r, ts, tipo, antes, despues):
        evs.append((ts, scan_id, r["id"], (r["title"] or "")[:120], (r["company"] or "")[:80],
                    r["rol_familia"] or "", (r["source"] or "").split(":")[0], tipo, str(antes), str(despues)))
        n_ev[tipo] += 1

    for row in conn.execute("SELECT * FROM ofertas").fetchall():
        r = dict(row)
        gid = r["id"]
        if gid not in con_aparecida:
            ev(r, _iso(r["first_seen"]), "aparecida", "", _iso(r["first_seen"]))
        fuentes = frozenset(x for x in (r.get("sources") or r["source"].split(":")[0]).split(",") if x)
        umbral = _umbral(barridos, fuentes, cfg.analytics.close_after_sweeps, memo)
        cerrada = 1 if (not r["active"]) or (umbral is not None and _iso(r["last_seen"]) < umbral) else 0
        actual = {"salary_clp": r["salary_clp"], "score": r["score"] or 0,
                  "encaje": r["ai_encaje"] or "", "modalidad": r["modality_norm"] or "", "cerrada": cerrada}
        p = prev.get(gid)
        if p is not None:
            if cerrada and not p["cerrada"]:
                ev(r, ahora, "cerrada", "abierta", "cerrada")
            elif not cerrada and p["cerrada"]:
                ev(r, ahora, "reaparecida", "cerrada", "abierta")
            if actual["salary_clp"] != p["salary_clp"] and (actual["salary_clp"] or p["salary_clp"]):
                ev(r, ahora, "sueldo", p["salary_clp"] if p["salary_clp"] is not None else "", actual["salary_clp"] if actual["salary_clp"] is not None else "")
            if abs(actual["score"] - (p["score"] or 0)) >= 10:
                ev(r, ahora, "score", p["score"], actual["score"])
            if actual["encaje"] != (p["encaje"] or "") and actual["encaje"]:
                ev(r, ahora, "encaje", p["encaje"], actual["encaje"])
            if actual["modalidad"] != (p["modalidad"] or "") and actual["modalidad"] and p["modalidad"]:
                ev(r, ahora, "modalidad", p["modalidad"], actual["modalidad"])
            if all(actual[k] == p[k] for k in actual):
                continue
        conn.execute("""INSERT INTO oferta_prev (oferta_id, salary_clp, score, encaje, modalidad, cerrada)
            VALUES (?,?,?,?,?,?) ON CONFLICT(oferta_id) DO UPDATE SET salary_clp=excluded.salary_clp,
            score=excluded.score, encaje=excluded.encaje, modalidad=excluded.modalidad, cerrada=excluded.cerrada""",
                     (gid, actual["salary_clp"], actual["score"], actual["encaje"], actual["modalidad"], cerrada))
    if evs:
        conn.executemany("""INSERT INTO oferta_eventos
            (ts, scan_id, oferta_id, title, company, rol_familia, fuente, tipo, antes, despues)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", evs)
    conn.commit()
    return dict(n_ev)


# ---------------- 5. mercado diario / semanal ----------------

def _sueldo_valido(r: dict, cfg: Config) -> int | None:
    v = r["salary_clp"]
    if v is None or not (cfg.analytics.band_min <= v <= cfg.analytics.band_max):
        return None
    return v if (r["salary_status"] or "") in ("trusted", "") else None


def _fila_agregada(grupo: list[dict], hoy: str, cerradas: int, cfg: Config):
    activas = [g for g in grupo if g["active"] and not g["cerrada"]]
    sueldos = [s for s in (_sueldo_valido(g, cfg) for g in activas) if s is not None]
    n_s = len(sueldos)
    return (len(activas), sum(1 for g in grupo if str(g["first_seen"])[:10] == hoy), cerradas, n_s,
            *(round(percentil(sueldos, p)) if n_s >= 5 else None for p in (25, 50, 75)))


def _paso_mercado(conn, cfg: Config, hoy: str, semana: str) -> None:
    filas = []
    for row in conn.execute("""SELECT o.id, o.active, o.first_seen, o.source, o.rol_familia, o.seniority_norm,
            o.modality_norm, o.salary_clp, o.salary_status, COALESCE(p.cerrada, 0) AS cerrada
            FROM ofertas o LEFT JOIN oferta_prev p ON p.oferta_id = o.id"""):
        filas.append(dict(row))
    cerr_hoy = defaultdict(int)
    for rf, fuente in conn.execute(
            "SELECT rol_familia, fuente FROM oferta_eventos WHERE tipo='cerrada' AND substr(ts,1,10)=?", (hoy,)):
        cerr_hoy[("rol_familia", rf)] += 1
        cerr_hoy[("fuente", fuente)] += 1
        cerr_hoy[("*", "*")] += 1

    def clave(g, dim):
        return {"rol_familia": g["rol_familia"] or "No desarrollo", "seniority": g["seniority_norm"] or "",
                "fuente": (g["source"] or "").split(":")[0], "modalidad": g["modality_norm"] or ""}[dim]

    combos: dict[tuple, list[dict]] = defaultdict(list)
    for g in filas:
        combos[("*", "*", "*", "*")].append(g)
        for dim in ("rol_familia", "seniority", "fuente", "modalidad"):
            v = clave(g, dim)
            if v:
                key = ["*"] * 4
                key[("rol_familia", "seniority", "fuente", "modalidad").index(dim)] = v
                combos[tuple(key)].append(g)
        if clave(g, "seniority"):
            combos[(clave(g, "rol_familia"), clave(g, "seniority"), "*", "*")].append(g)
    for (rf, sen, fu, mo), grupo in combos.items():
        n_cerr = cerr_hoy[("*", "*")] if (rf, sen, fu, mo) == ("*",) * 4 else (
            cerr_hoy[("rol_familia", rf)] if sen == fu == mo == "*" and rf != "*" else
            cerr_hoy[("fuente", fu)] if rf == sen == mo == "*" and fu != "*" else 0)
        conn.execute("""INSERT INTO mercado_diario VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(fecha, rol_familia, seniority, fuente, modalidad) DO UPDATE SET
            n_activas=excluded.n_activas, n_nuevas=excluded.n_nuevas, n_cerradas=excluded.n_cerradas,
            n_con_sueldo=excluded.n_con_sueldo, sueldo_p25=excluded.sueldo_p25,
            sueldo_p50=excluded.sueldo_p50, sueldo_p75=excluded.sueldo_p75""",
                     (hoy, rf, sen, fu, mo, *_fila_agregada(grupo, hoy, n_cerr, cfg)))

    # tendencias de tecnologías (semana ISO)
    techs_de = defaultdict(set)
    for gid, tech in conn.execute("SELECT oferta_id, tech FROM oferta_techs"):
        techs_de[gid].add(tech)
    semana_de = lambda fs: _semana_iso(str(fs)[:10])   # noqa: E731
    activas = [g for g in filas if g["active"] and not g["cerrada"]]
    for alcance in ["*", *sorted({g["rol_familia"] or "No desarrollo" for g in filas})]:
        sub = [g for g in activas if alcance == "*" or (g["rol_familia"] or "No desarrollo") == alcance]
        base = [g for g in sub if techs_de.get(g["id"])]
        cuenta, nuevas = Counter(), Counter()
        for g in base:
            for t in techs_de[g["id"]]:
                cuenta[t] += 1
                if semana_de(g["first_seen"]) == semana:
                    nuevas[t] += 1
        for t, n in cuenta.most_common(40):
            conn.execute("""INSERT INTO mercado_tech_semanal VALUES (?,?,?,?,?,?)
                ON CONFLICT(semana, tech, rol_familia) DO UPDATE SET n_activas=excluded.n_activas,
                n_nuevas=excluded.n_nuevas, n_base=excluded.n_base""",
                         (semana, t, alcance, n, nuevas[t], len(base)))
    conn.commit()


def _semana_iso(d: str) -> str:
    try:
        y, w, _ = date.fromisoformat(d).isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return ""


# ---------------- orquestador ----------------

def materializar(conn, cfg: Config, *, scan_id: int | None = None, full: bool = False,
                 ahora: datetime | None = None) -> dict:
    t0 = time.time()
    ahora_dt = ahora or datetime.now(timezone.utc)
    ahora_s = ahora_dt.strftime(_FMT)
    hoy = ahora_dt.strftime("%Y-%m-%d")
    from .esquema import asegurar_esquema
    asegurar_esquema(conn)
    ultimo = conn.execute("SELECT valor FROM analytics_meta WHERE clave='ultimo_inicio'").fetchone()
    desde = ultimo[0] if ultimo else ""
    gids = _paso_derivar(conn, cfg, desde, full or not ultimo)
    _paso_techs_tags(conn, gids)
    eventos = _paso_eventos(conn, cfg, ahora_s, scan_id)
    _paso_mercado(conn, cfg, hoy, _semana_iso(hoy))
    ms = int((time.time() - t0) * 1000)
    total = conn.execute("SELECT COUNT(*) FROM ofertas").fetchone()[0]
    for k, v in (("ultimo_inicio", ahora_s),
                 ("ultima_corrida", json.dumps({"ts": ahora_s, "filas": total, "derivadas": len(gids),
                                                "ms": ms, "norm": nz.VERSION, "eventos": eventos}))):
        conn.execute("INSERT INTO analytics_meta VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (k, v))
    conn.commit()
    log.info("analytics: %d filas, %d derivadas, eventos=%s, %d ms", total, len(gids), eventos, ms)
    return {"filas": total, "derivadas": len(gids), "eventos": eventos, "ms": ms}
