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


def cmd_run(cfg, notify: bool = True) -> None:
    conn = database.connect(cfg)
    try:
        database.init_db(conn)
        version_id = "env-" + datetime.now(timezone.utc).strftime("%Y%m%d")
        database.register_criteria_version(conn, version_id, cfg)

        from .sources import (linkedin, computrabajo, indeed, glassdoor, laborum,
                              jooble, accenture)
        s = cfg.search
        jobs = []
        if cfg.sources.get("jooble"):
            # jooble usa browser headless bajo xvfb (la API REST exige login de usuario)
            try:
                jobs += jooble.jobs(s.queries_jooble, "perfil:")
            except Exception as e:
                log.warning("jooble falló (continúa el barrido): %s", e)
        if cfg.sources.get("accenture", True):
            jobs += accenture.jobs(s.queries_accenture, "perfil:")
        if cfg.sources.get("laborum", True):
            jobs += laborum.jobs(s.queries_laborum, "perfil:")
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

        # dedup + index + score al indexar (commit cada 25 filas: el lock de
        # SQLite se retiene <1s por vez — batches IA / comandos no quedan fuera)
        seen_urls, new_jobs, total_seen = set(), [], 0
        now = _now()
        for n, j in enumerate(jobs, 1):
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
            if n % 25 == 0:
                conn.commit()
        conn.execute("""INSERT INTO scan_log (ts, total_seen, new_count) VALUES (?,?,?)""",
                     (now, total_seen, len(new_jobs)))
        conn.commit()

        # IA complementaria para las NUEVAS (si IA_ENABLED) — llena modality/salary/
        # seniority/flags al indexarse, no espera al batch nocturno
        if new_jobs and cfg.ia.enabled and cfg.ia.api_key:
            try:
                from .enrich import run_ia_batch, profile_description
                n_ia = run_ia_batch(conn, cfg, profile_description(cfg),
                                    max_n=12, groups={j["group_id"] for j in new_jobs})
                log.info("IA complementaria: %d/%d ofertas nuevas enriquecidas",
                         n_ia, len(new_jobs))
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                log.warning("IA complementaria falló (barrido continúa): %s", e)

        # auto-enrich de las nuevas (Anillo A, máx 8 para no frenar)
        from .enrich import enrich_pending
        try:
            enrich_pending(conn, cfg, max_n=8)
        except Exception as e:
            conn.rollback()
            log.warning("enrich falló (barrido continúa): %s", e)

        # re-score de las que cambiaron (descripción nueva)
        database.rescore_all(conn, compute_score, version_id, cfg)

        # digest: solo >= ALERT_MIN_SCORE
        threshold = cfg.alerts.min_score
        alerts = conn.execute(
            "SELECT * FROM ofertas WHERE active=1 AND score >= ? "
            "ORDER BY score DESC LIMIT ?", (threshold, cfg.alerts.max_per_digest)).fetchall()
        offers = [dict(r) for r in alerts]
        sent = False
        if notify:
            sent = send_digest(cfg, offers) if new_jobs else send_digest(cfg, [])
        if sent:
            log.info("digest enviado: %d ofertas >= %d", len(offers), threshold)
        else:
            log.info("digest NO enviado: %d ofertas >= %d (nuevas=%d, notify=%s)",
                     len(offers), threshold, len(new_jobs), notify)
    except Exception:
        try:
            conn.rollback()      # no dejar transacción abierta → DB locked para otros
        except Exception:
            pass
        raise
    finally:
        conn.close()


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


def cmd_market(cfg=None) -> None:
    """Análisis de mercado completo sin Telegram: imprime progreso por fase."""
    cfg = cfg or load_config()
    from .market import run_market_pipeline
    t0 = __import__("time").time()

    def on_phase(n: int, msg: str):
        print(f"[fase {n}/4] {msg}")

    pdf_path, narr, ia_ok = run_market_pipeline(cfg, on_phase=on_phase)
    dur = int(__import__("time").time() - t0)
    print(f"\nPDF: {pdf_path} ({pdf_path.stat().st_size // 1024} KB)")
    print(f"IA narrativa: {'sí' if ia_ok else 'no (plantilla)'} · {dur}s")
    for b in narr.get("tldr", []):
        print(f"  • {b}")


def cmd_ia(conn_unused=None) -> None:
    cfg = load_config()
    conn = database.connect(cfg)
    database.init_db(conn)
    from .enrich import run_ia_batch, profile_description
    done = run_ia_batch(conn, cfg, profile_description(cfg))
    print(f"IA enriqueció: {done} ofertas")
    # re-score con los datos nuevos de IA (salary/modality/seniority cambian el score)
    from .scoring import compute_score
    version_id = database.current_version(conn) or (
        "env-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    database.register_criteria_version(conn, version_id, cfg)
    rescored = database.rescore_all(conn, compute_score, version_id, cfg)
    print(f"rescore: {rescored} ofertas → versión {version_id}")


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
    elif cmd == "market":
        cmd_market(cfg)
    elif cmd == "ia":
        conn = database.connect(cfg)
        database.init_db(conn)
        from .enrich import run_ia_batch, profile_description
        done = run_ia_batch(conn, cfg, profile_description(cfg))
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