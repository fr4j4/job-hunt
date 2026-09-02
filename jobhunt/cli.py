"""CLI de jobhunt.

    python -m jobhunt run          # barrido completo (indexa + rescore + digest)
    python -m jobhunt rescore      # re-evalúa todo el pool con el criterio vigente
    python -m jobhunt enrich       # backfill Anillo A (JSON-LD)
    python -m jobhunt ia           # batch IA nocturno (deepseek-v4-flash)
    python -m jobhunt report       # stats de mercado del pool completo
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .config import load_config
from .logging_setup import get_logger

log = get_logger(__name__)
from . import db as database
from .scoring import compute_score
from .dedup import find_duplicate
from .notify import send_digest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cmd_run(cfg) -> None:
    conn = database.connect(cfg)
    database.init_db(conn)
    version_id = "env-" + datetime.now(timezone.utc).strftime("%Y%m%d")
    database.register_criteria_version(conn, version_id, cfg)

    from .sources import linkedin, computrabajo, indeed, glassdoor
    s = cfg.search
    jobs = []
    if cfg.sources.get("linkedin"):
        jobs += linkedin.fetch_jobs(s.queries_linkedin, "perfil:")
    if cfg.sources.get("computrabajo"):
        jobs += computrabajo.jobs(s.queries_computrabajo, "perfil:")
    if cfg.search.mode in ("both", "sample"):
        # muestreo amplio: rotación para diversificar sin inflar requests
        n = max(1, int(len(s.sample_linkedin) * s.sample_rotation))
        jobs += linkedin.fetch_jobs(s.sample_linkedin[:n], "sample:")
        jobs += computrabajo.jobs(s.sample_computrabajo[:n], "sample:")
        if cfg.sources.get("indeed"):
            jobs += indeed.jobs(s.sample_indeed[:n], "muestra:")
        if cfg.sources.get("glassdoor") and _is_premium_tick(cfg):
            jobs += glassdoor.jobs(s.sample_glassdoor[:2], "muestra:")
    if cfg.sources.get("indeed") and _is_premium_tick(cfg):
        jobs += indeed.jobs(s.queries_indeed, "perfil:")
    if cfg.sources.get("glassdoor") and _is_premium_tick(cfg):
        jobs += glassdoor.jobs(s.queries_glassdoor, "perfil:")

    log.info("barrido iniciado: %d ofertas crudas (mode=%s)", len(jobs), s.mode)

    # dedup + index + score al indexar
    seen_urls, new_jobs, total_seen = set(), [], 0
    now = _now()
    for j in jobs:
        from .db import url_key
        uk = url_key(j.get("url"))
        if uk and uk in seen_urls:
            continue
        if uk:
            seen_urls.add(uk)
        j["uid"] = ""
        gid, is_new = database.upsert(conn, j, now)
        j["uid"] = gid
        score, _ = compute_score(j, cfg)
        conn.execute("UPDATE ofertas SET score=?, score_version=? WHERE group_id=?",
                     (score, version_id, gid))
        total_seen += 1
        if is_new:
            new_jobs.append({**j, "score": score, "group_id": gid})
    conn.execute("""INSERT INTO scan_log (ts, total_seen, new_count) VALUES (?,?,?)""",
                 (now, total_seen, len(new_jobs)))
    conn.commit()

    # auto-enrich de las nuevas (Anillo A, máx 8 para no frenar)
    from .enrich import enrich_pending
    enrich_pending(conn, cfg, max_n=8)

    # re-score de las que cambiaron (descripción nueva)
    database.rescore_all(conn, compute_score, version_id, cfg)

    # digest: solo >= ALERT_MIN_SCORE
    threshold = cfg.alerts.min_score
    alerts = conn.execute(
        "SELECT * FROM ofertas WHERE active=1 AND score >= ? "
        "ORDER BY score DESC LIMIT ?", (threshold, cfg.alerts.max_per_digest)).fetchall()
    offers = [dict(r) for r in alerts]
    sent = send_digest(cfg, offers) if new_jobs else False
    if not new_jobs:
        sent = send_digest(cfg, [])
    log.info("digest enviado: %d ofertas >= %d" if sent else "digest NO enviado", len(offers), threshold)


def _is_premium_tick(cfg) -> bool:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).hour in cfg.premium_hours


def cmd_rescore(cfg) -> None:
    conn = database.connect(cfg)
    database.init_db(conn)
    version_id = "env-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    database.register_criteria_version(conn, version_id, cfg)
    updated = database.rescore_all(conn, compute_score, version_id, cfg)
    print(f"rescore completado: {updated} ofertas → versión {version_id} "
          f"({database.needs_rescore(conn, version_id)} pendientes)")


def cmd_enrich(cfg) -> None:
    conn = database.connect(cfg)
    n = database.init_db(conn) or 0
    from .enrich import enrich_pending
    done = enrich_pending(conn, cfg)
    print(f"enriquecidas: {done}")


def cmd_ia(conn_unused=None) -> None:
    cfg = load_config()
    conn = database.connect(cfg)
    database.init_db(conn)
    p = cfg.profile
    profile_desc = (f"{p.title}, {p.years_exp} años exp, stack {', '.join(p.techs[:6])}, "
                    f"inglés {p.english_level}, prefiere {'/'.join(p.modality_pref[:2])}, "
                    f"banda {p.salary_min}-{p.salary_max} CLP")
    from .enrich import run_ia_batch
    done = run_ia_batch(conn, cfg, profile_desc)
    print(f"IA enriqueció: {done} ofertas")


def cmd_report(cfg) -> None:
    conn = database.connect(cfg)
    stats = []
    n = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1").fetchone()[0]
    stats.append(f"Pool activo: {n} ofertas")
    sal = conn.execute("SELECT AVG(CAST(REPLACE(REPLACE(salary,'CLP ',''),' ','') AS REAL)) "
                       "FROM ofertas WHERE active=1 AND salary LIKE 'CLP%'").fetchone()[0]
    if sal:
        stats.append(f"Salario promedio declarado: ${sal:,.0f} CLP")
    rem = conn.execute("SELECT COUNT(*) FROM ofertas WHERE active=1 AND modality='remoto'").fetchone()[0]
    stats.append(f"Remoto: {rem}/{n} ({rem*100//max(n,1)}%)")
    print("\n".join(stats))


def main():
    import sys
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        cmd_run(cfg)
    elif cmd == "rescore":
        cmd_rescore(cfg)
    elif cmd == "enrich":
        cmd_enrich(cfg)
    elif cmd == "ia":
        conn = database.connect(cfg)
        database.init_db(conn)
        from .enrich import run_ia_batch
        p = cfg.profile
        profile_desc = (f"{p.title}, {p.years_exp} años exp, stack {', '.join(p.techs[:6])}, "
                        f"inglés {p.english_level}")
        done = run_ia_batch(conn, cfg, profile_desc)
        print(f"IA batch: {done} ofertas enriquecidas")
    elif cmd == "watch":
        from .bot import run_daemon
        run_daemon(cfg)
    elif cmd == "report":
        cmd_report(cfg)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()