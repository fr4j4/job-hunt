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
import threading
import time
from datetime import datetime, timezone

from .config import load_config
from .logging_setup import get_logger

log = get_logger(__name__)
from . import db as database
from .scoring import compute_score, compute_market_score
from .dedup import find_duplicate
from .notify import send_digest
import json
import urllib.error
import urllib.request


def _tg_api_for_channel(cfg):
    """Adapter: publish_channel espera tg_api(method, payload) → dict con .get('ok').

    No lanza excepciones (contrato publish_channel: los fallos del canal no tumban
    el barrido) — retorna {"ok": False, "error": ...} en fallos HTTP.
    """
    def call(method: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{cfg.telegram.bot_token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                pass
            return {"ok": False, "error": f"{exc} {detail}".strip()}
    return call


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def worker_ia(cfg, work_q, out_q, lote_id: int, stop_event, mercado: str,
              profile_desc: str, extract_fn=None) -> None:
    """Worker IA — SOLO HTTP, cero SQLite (P0-3 spec v4.1 §3.1).

    Saca job de work_q, llama ia_extract_detail (nunca lanza) y SIEMPRE pone
    (job, parsed, err_kind, lote_id) en out_q (try/finally — A2: 1 resultado
    garantizado por oferta). Respeta stop_event (breaker B5: los workers extra
    salen antes de tomar un nuevo job). Sale sola cuando la cola se agota.
    """
    from queue import Empty
    from .enrich import ia_extract_detail
    fn = extract_fn or ia_extract_detail
    while not stop_event.is_set():
        try:
            job = work_q.get(timeout=1)   # 1s: salida ≤1s tras agotarse la cola + breaker reactivo
        except Empty:
            return  # cola agotada → worker sale
        parsed, err_kind = None, "other"
        try:
            parsed, err_kind = fn(cfg, job, profile_desc, mercado)
        except Exception:
            parsed, err_kind = None, "other"
        finally:
            out_q.put((job, parsed, err_kind, lote_id))
            work_q.task_done()


def consume_lote(conn, cfg, out_q, lote, lote_id: int, deadline: float,
                 on_result=None, apply_fn=None, drain_s: int = 120,
                 get_timeout_s: int = 30, breaker_window: int = 20,
                 breaker_fails: int = 5, stop_event=None):
    """Consume resultados del lote — hilo ÚNICO escritor (spec v4.1 §3.2).

    - Terminación POR CONTEO (A2): sale cuando recibidos == len(lote).
    - Resultados con lote_id distinto → descartados (tardíos cross-lote).
    - Breaker B5: ≥5 fallos 'rate' en los últimos 20 resultados → stop_event
      (el MISMO Event que detiene a los workers — debe pasarse desde fuera).
      Timeout aislado NO cuenta; 1 éxito resetea la ventana.
    - Deadline vencido → drain acotado drain_s (120s): da chance a resultados
      ya en vuelo; lo que no llegue queda sin IA (batch nocturno, C9).
    Retorna stats: dict(recibidos, ia_failures, breaker_trips, descartados).
    """
    from queue import Empty
    from .enrich import apply_ia_result
    apply = apply_fn or apply_ia_result
    stop_event = stop_event if stop_event is not None else threading.Event()
    stats = {"recibidos": 0, "ia_failures": 0, "breaker_trips": 0, "descartados": 0}
    ventana: list[str] = []   # err_kinds de los últimos N resultados
    breaker = False
    n_lote_total = len(lote)
    while stats["recibidos"] < n_lote_total:
        restante = deadline - time.time()
        if restante <= 0:
            log.warning("lote %d: deadline vencido con %d/%d — drenando %ds",
                        lote_id + 1, stats["recibidos"], n_lote_total, drain_s)
            break
        try:
            item = out_q.get(timeout=min(get_timeout_s, max(1, int(restante))))
        except Empty:
            continue
        if len(item) == 4:
            job, parsed, err_kind, lid = item
        else:                      # compat: (job, parsed, lote_id)
            job, parsed, lid = item
            err_kind = "other" if parsed is None else ""
        if lid != lote_id:
            stats["descartados"] += 1
            continue               # resultado tardío de otro lote → descartar
        if not breaker and err_kind == "rate":
            ventana.append("f")
            ventana = ventana[-breaker_window:]
            if sum(1 for v in ventana if v == "f") >= breaker_fails:
                breaker = True
                stats["breaker_trips"] += 1
                stop_event.set()
                log.warning("breaker IA: %d fallos 429/5xx en últimos %d — cortando lote (resto → batch nocturno)",
                            breaker_fails, breaker_window)
                break   # DEV-2/OPS-3: no quemar el deadline — el resto va a cola nocturna (C9)
        elif err_kind == "":
            ventana.clear()        # 1 éxito resetea la ventana (B5)
        if on_result:
            try:
                on_result(job, parsed)
            except Exception:
                pass
        else:
            try:
                apply(conn, cfg, job, parsed)
            except Exception:
                log.warning("apply_ia_result falló (continúa): %s", job.get("group_id", "?"))
        if err_kind != "":
            stats["ia_failures"] += 1
        stats["recibidos"] += 1
    # ---- drain acotado (§3.2): resultados en vuelo del lote actual ----
    if stats["recibidos"] < n_lote_total:
        fin_drain = time.time() + drain_s
        while stats["recibidos"] < n_lote_total and time.time() < fin_drain:
            try:
                item = out_q.get(timeout=max(0.1, min(5, fin_drain - time.time())))
            except Empty:
                continue   # DEV-4: un hueco de 5s no aborta el drain — lo acota fin_drain
            if len(item) == 4:
                job, parsed, err_kind, lid = item
            else:
                job, parsed, lid = item
                err_kind = "other" if parsed is None else ""
            if lid != lote_id:
                stats["descartados"] += 1
                continue
            if on_result:
                try:
                    on_result(job, parsed)
                except Exception:
                    pass
            else:
                try:
                    apply(conn, cfg, job, parsed)
                except Exception:
                    log.warning("apply_ia_result falló (drain): %s", job.get("group_id", "?"))
            if err_kind != "":
                stats["ia_failures"] += 1
            stats["recibidos"] += 1
    return stats


def lotes_por_fit(new_jobs: list[dict], lote_size: int) -> list[list[dict]]:
    """Ordena por fit DESC (C7: orden aproximado; el gate real es market score)
    y corta en lotes de lote_size."""
    ordenados = sorted(new_jobs, key=lambda j: -j.get("score", 0))
    return [ordenados[i:i + lote_size]
            for i in range(0, len(ordenados), lote_size)] if new_jobs else []


def cmd_run(cfg, notify: bool = True, on_phase=None, stop_event: threading.Event | None = None):
    """on_phase(fuente, query, page) — callback opcional para progreso por fuente/query/página."""
    def phase(fuente: str, query: str = "", page: int = 0, detail: str = ""):
        if on_phase:
            try:
                on_phase(fuente, query, page, detail)
            except Exception:
                pass

    def qcb(fuente: str):
        """callback on_query estándar para los módulos de fuente"""
        return lambda q, pag: phase(fuente, q, pag)

    conn = database.connect(cfg)
    try:
        database.init_db(conn)
        version_id = "env-" + datetime.now(timezone.utc).strftime("%Y%m%d")
        database.register_criteria_version(conn, version_id, cfg)
        conn.commit()   # FIX: suelta el write lock YA — el fetch de fuentes tarda minutos y no escribe
        # observabilidad v4.1 (C5): stats por barrido para scan_log
        lots_done = ia_failures = breaker_trips = channel_posts = 0

        from .sources import (linkedin, computrabajo, indeed, glassdoor, laborum,
                              jooble, accenture, aira)
        from .relevance import filter_offers
        s = cfg.search
        jobs = []
        if cfg.sources.get("aira"):
            phase("aira (feeds JSON employers)")
            try:
                raw = aira.jobs(cfg.aira_feeds, "aira:")
                relevantes, stats = filter_offers(raw, cfg)
                jobs += relevantes
                log.info("aira: %d/%d ofertas pasaron el gate %s", len(relevantes), len(raw), stats)
            except Exception as e:
                log.warning("aira falló (continúa el barrido): %s", e)
        if cfg.sources.get("jooble"):
            phase("jooble")
            # jooble usa browser headless bajo xvfb (la API REST exige login de usuario)
            try:
                jobs += jooble.jobs(s.queries_jooble, "perfil:", on_query=qcb("jooble"))
            except Exception as e:
                log.warning("jooble falló (continúa el barrido): %s", e)
        if cfg.sources.get("accenture", True):
            phase("accenture")
            jobs += accenture.jobs(s.queries_accenture, "perfil:", on_query=qcb("accenture"))
        if cfg.sources.get("laborum", True):
            phase("laborum")
            jobs += laborum.jobs(s.queries_laborum, "perfil:", on_query=qcb("laborum"))
        if cfg.sources.get("linkedin"):
            phase("linkedin")
            jobs += linkedin.fetch_jobs(s.queries_linkedin, "perfil:", on_query=qcb("linkedin"))
        if cfg.sources.get("computrabajo"):
            phase("computrabajo")
            jobs += computrabajo.jobs(s.queries_computrabajo, "perfil:", on_query=qcb("computrabajo"))
        if cfg.search.mode in ("both", "sample"):
            # muestreo amplio: rotación para diversificar sin inflar requests
            phase("linkedin/computrabajo (muestreo)")
            n = max(1, int(len(s.sample_linkedin) * s.sample_rotation))
            jobs += linkedin.fetch_jobs(s.sample_linkedin[:n], "sample:")
            jobs += computrabajo.jobs(s.sample_computrabajo[:n], "sample:")
            if cfg.sources.get("indeed"):
                phase("indeed")
                jobs += indeed.jobs(s.sample_indeed[:n], "muestra:", on_query=qcb("indeed"))
            if cfg.sources.get("glassdoor") and _is_premium_tick(cfg):
                jobs += glassdoor.jobs(s.sample_glassdoor[:2], "muestra:", on_query=qcb("glassdoor"))
        if cfg.sources.get("indeed") and _is_premium_tick(cfg):
            phase("indeed")
            jobs += indeed.jobs(s.queries_indeed, "perfil:", on_query=qcb("indeed"))
        if cfg.sources.get("glassdoor") and _is_premium_tick(cfg):
            phase("glassdoor (perfil)")
            jobs += glassdoor.jobs(s.queries_glassdoor, "perfil:", on_query=qcb("glassdoor"))

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
        conn.execute("""INSERT INTO scan_log (ts, total_seen, new_count,
            lots_done, ia_failures, breaker_trips, channel_posts)
            VALUES (?,?,?,?,?,?,?)""",
                     (now, total_seen, len(new_jobs), lots_done, ia_failures,
                      breaker_trips, channel_posts))
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # PIPELINE POR LOTES con IA paralela (spec v4.1 §2):
        # lotes de IA_BATCH_SIZE, dentro de cada lote IA_CONCURRENCY workers HTTP
        # (sin SQLite — conexión única en este hilo), publish incremental por lote.
        new_sorted = sorted(new_jobs, key=lambda j: -j.get("score", 0))  # mejores primero (aprox)
        total_new = len(new_sorted)
        presupuesto_canal = cfg.channel.max_posts_per_sweep if (
            cfg.channel.enabled and cfg.channel.chat_id) else 0
        api = _tg_api_for_channel(cfg)
        hechas_acum = 0

        if new_sorted and cfg.ia.enabled and cfg.ia.api_key:
            from .enrich import (enrich_pending, profile_description,
                                 compute_market_context)
            p_desc = profile_description(cfg)
            lote_size = max(1, cfg.ia.batch_size)
            lotes = lotes_por_fit(new_jobs, lote_size)
            n_lotes = len(lotes)

            for n_lote, lote in enumerate(lotes):
                # /stop: corte limpio ENTRE lotes — lo hecho queda commiteado,
                # el resto del pool vuelve a la cola nocturna C9
                if stop_event is not None and stop_event.is_set():
                    log.warning("barrido detenido por /stop tras %d/%d lotes", n_lote, n_lotes)
                    break
                lote_ids = {j["group_id"] for j in lote}
                phase("IA complementaria",
                      detail=f"{hechas_acum}/{total_new} · lote {n_lote + 1}/{n_lotes}")

                # 1. contexto de mercado 1 vez por lote (main, conn única)
                mercado = compute_market_context(conn)

                # 2. FIX B: enrich de fichas ANTES de la IA — la IA lee el contexto
                #    completo (desc completa, sueldo, modalidad) en vez del crudo
                try:
                    enrich_pending(conn, cfg, max_n=8, groups=lote_ids, solo_fetch=True,
                                   stop_event=stop_event)
                except Exception as e:
                    conn.rollback()
                    log.warning("enrich pre-IA lote falló (continúa): %s", e)
                conn.commit()

                # 2b. recargar el lote con las fichas enriquecidas (el prompt usa estos dicts)
                try:
                    qs = ",".join("?" for _ in lote_ids)
                    rows = conn.execute(
                        f"SELECT group_id, title, company, location, description, salary, modality, "
                        f"salary_raw, salary_status, salary_note "
                        f"FROM ofertas WHERE group_id IN ({qs})", tuple(lote_ids)).fetchall()
                    if rows:
                        lote = [dict(r) for r in rows]
                except Exception as e:
                    log.warning("reload lote falló (usa dicts crudos): %s", e)

                # 3. workers IA paralelos (solo HTTP, módulo-level = testeable)
                from queue import Queue
                work_q, out_q = Queue(), Queue()
                stop_event = threading.Event()
                n_workers = max(1, min(cfg.ia.concurrency, len(lote)))
                # H3: precargar ANTES de arrancar threads — si un worker llegara a
                # Empty antes de los put, saldría y dejaría jobs huérfanos (deadline muerto)
                for j in lote:
                    work_q.put(j)
                threads = []
                for _ in range(n_workers):
                    t = threading.Thread(target=worker_ia,
                                         args=(cfg, work_q, out_q, n_lote,
                                               stop_event, mercado, p_desc),
                                         daemon=True)
                    t.start()
                    threads.append(t)

                # 3. main: consume resultados y escribe (ÚNICO escritor)
                #    deadline anti-cuelgue: len/CONCURRENCY × 242s peor caso × 1.5, mín 300s
                deadline = time.time() + max(300, (len(lote) / cfg.ia.concurrency) * 242 * 1.5)
                st = consume_lote(conn, cfg, out_q, lote, n_lote, deadline,
                                  stop_event=stop_event)
                lots_done += 1 if st["recibidos"] else 0
                ia_failures += st["ia_failures"]
                breaker_trips += st["breaker_trips"]
                hechas_acum += st["recibidos"]
                conn.commit()
                stop_event.set()

                # 4. (enrich ya corrió PRE-IA — FIX B) rescore SOLO del lote
                phase("rescore lote", detail=f"{n_lote + 1}/{n_lotes}")
                database.rescore_ids(conn, list(lote_ids), version_id, cfg,
                                     compute_score, market_score_fn=compute_market_score)

                # 5. publish del lote (consume presupuesto del barrido)
                if cfg.channel.enabled and cfg.channel.chat_id and presupuesto_canal > 0:
                    phase("canal: publicando lote", detail=f"{n_lote + 1}/{n_lotes}")
                    try:
                        from .channel import publish_channel
                        ch_stats = publish_channel(cfg, conn, api, budget=presupuesto_canal)
                        presupuesto_canal -= ch_stats["posted"]
                        channel_posts += ch_stats["posted"]
                        log.info("canal lote %d: %s (presupuesto restante %d)",
                                 n_lote + 1, ch_stats, presupuesto_canal)
                    except Exception as e:
                        log.warning("canal lote falló (continúa): %s", e)
                elif cfg.channel.enabled and cfg.channel.chat_id and presupuesto_canal <= 0:
                    log.info("canal lote %d: presupuesto del barrido agotado — sin publish (OPS-5)",
                             n_lote + 1)
            # rescore global final: cubre enrich global y pendientes de lotes con fallo
            phase("rescore pool")
            database.rescore_all(conn, compute_score, version_id, cfg,
                                  market_score_fn=compute_market_score)
        else:
            # sin IA: fit score al vuelo ya escrito; market/rescore global para el gate
            phase("rescore pool")
            database.rescore_all(conn, compute_score, version_id, cfg,
                                  market_score_fn=compute_market_score)

            # canal: publicar ofertas nuevas que pasan el gate (sin IA el gate usa fit-like)
            if cfg.channel.enabled and cfg.channel.chat_id:
                phase("canal: publicando nuevas")
                try:
                    from .channel import publish_channel
                    ch_stats = publish_channel(cfg, conn, _tg_api_for_channel(cfg),
                                               budget=presupuesto_canal)   # DEV-6: A3 también sin IA
                    channel_posts += ch_stats["posted"]
                    log.info("canal: %s", ch_stats)
                except Exception as e:
                    log.warning("canal falló (barrido continúa): %s", e)

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
        # C5 (H1/OPS-1): persistir los contadores REALES del barrido al final
        try:
            conn.execute("""UPDATE scan_log SET lots_done=?, ia_failures=?,
                breaker_trips=?, channel_posts=? WHERE id=?""",
                         (lots_done, ia_failures, breaker_trips, channel_posts, row_id))
            conn.commit()
        except Exception as e:
            log.warning("scan_log UPDATE falló (no tumba barrido): %s", e)
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
    updated = database.rescore_all(conn, compute_score, version_id, cfg,
                              market_score_fn=compute_market_score)
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
    from .scoring import compute_score, compute_market_score
    version_id = database.current_version(conn) or (
        "env-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M"))
    database.register_criteria_version(conn, version_id, cfg)
    rescored = database.rescore_all(conn, compute_score, version_id, cfg,
                              market_score_fn=compute_market_score)
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
    elif cmd == "channel":
        # python -m jobhunt channel [--dry-run] [--digest]
        from .db import connect, init_db
        from .channel import (publish_channel, publish_daily_digest,
                              publish_weekly_digests, publish_trends)
        conn = connect(cfg)
        init_db(conn)
        dry = "--dry-run" in sys.argv
        only_digest = "--digest" in sys.argv
        if not only_digest:
            stats = publish_channel(cfg, conn, _tg_api_for_channel(cfg), dry_run=dry)
            print(f"canal: {stats}")
        if "--digest" in sys.argv or dry:
            publish_daily_digest(cfg, conn, _tg_api_for_channel(cfg), dry_run=dry)
            publish_weekly_digests(cfg, conn, _tg_api_for_channel(cfg), dry_run=dry)
            publish_trends(cfg, conn, _tg_api_for_channel(cfg), dry_run=dry)
        conn.close()
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