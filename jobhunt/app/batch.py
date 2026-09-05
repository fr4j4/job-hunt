"""Pipeline de lotes IA del barrido (extraído de cli.cmd_run — paso 6).

Regla del refactor: este paquete NO importa jobhunt.{cli,bot,channel,enrich,db,
scoring} a nivel de módulo. Las dependencias del pipeline se resuelven con
imports diferidos dentro de las funciones (cero ciclos y, además, el
monkeypatch de los tests sobre jobhunt.enrich.* sigue funcionando porque el
nombre se resuelve en cada llamada).
"""
from __future__ import annotations

import threading
import time

from ..logging_setup import get_logger

log = get_logger(__name__)


def worker_ia(cfg, work_q, out_q, lote_id: int, stop_event, mercado: str,
              profile_desc: str, extract_fn=None) -> None:
    """Worker IA — SOLO HTTP, cero SQLite (P0-3 spec v4.1 §3.1).

    Saca job de work_q, llama ia_extract_detail (nunca lanza) y SIEMPRE pone
    (job, parsed, err_kind, lote_id) en out_q (try/finally — A2: 1 resultado
    garantizado por oferta). Respeta stop_event (breaker B5: los workers extra
    salen antes de tomar un nuevo job). Sale sola cuando la cola se agota.
    extract_fn: override (spec-ia-local — ia_extract_local con fallback cloud).
    """
    from queue import Empty
    from ..enrich import ia_extract_detail
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


def _extract_local_con_fallback(cfg, job, profile_desc, mercado):
    """Wrapper spec-ia-local: 2 tareas locales; si fallan → cloud individual.
    Etiqueta parsed["_ia_model"] con el modelo REAL (IA-1: el fallback cloud
    no debe quedar como local_model en ia_model)."""
    from ..enrich import ia_extract_local_con_fallback
    parsed, err, model = ia_extract_local_con_fallback(cfg, job, profile_desc, mercado)
    if parsed is not None:
        parsed["_ia_model"] = model
    return parsed, err


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
    from ..enrich import apply_ia_result
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


class BatchRunner:
    """Loop de lotes del barrido: enrich pre-IA → reload → workers → consume →
    rescore del lote → publish incremental (spec v4.1 §2).

    worker_fn/consume_fn son inyectables: cmd_run les pasa los nombres resueltos
    en el namespace de jobhunt.cli para que el monkeypatch `cli.worker_ia` siga
    funcionando.
    """

    def __init__(self, cfg, version_id: str, *, worker_fn=None, consume_fn=None,
                 extract_fn=None, phase=None):
        self.cfg = cfg
        self.version_id = version_id
        self.worker_fn = worker_fn or worker_ia
        self.consume_fn = consume_fn or consume_lote
        self.extract_fn = extract_fn or _extract_local_con_fallback
        self.phase = phase or (lambda *a, **kw: None)

    def run(self, conn, new_jobs: list[dict], *, api=None, presupuesto_canal: int = 0,
            stop_event: threading.Event | None = None) -> dict:
        """Procesa new_jobs por lotes. stop_event es el de /stop (corte ENTRE
        lotes); NUNCA se rebindea — cada lote usa su propio lote_stop (CONC-1).
        Retorna stats acumuladas del barrido."""
        from queue import Queue
        from .. import db as database
        from ..enrich import (enrich_pending, profile_description,
                              compute_market_context)
        from ..scoring import compute_score, compute_market_score

        cfg = self.cfg
        st_run = {"lots_done": 0, "ia_failures": 0, "breaker_trips": 0,
                  "channel_posts": 0, "hechas": 0, "presupuesto_canal": presupuesto_canal}
        total_new = len(new_jobs)
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
            self.phase("IA complementaria",
                       detail=f"{st_run['hechas']}/{total_new} · lote {n_lote + 1}/{n_lotes}")

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
                    f"salary_raw, salary_status, salary_note, techs "
                    f"FROM ofertas WHERE group_id IN ({qs})", tuple(lote_ids)).fetchall()
                if rows:
                    lote = [dict(r) for r in rows]
            except Exception as e:
                log.warning("reload lote falló (usa dicts crudos): %s", e)

            # 3. workers IA paralelos (solo HTTP, módulo-level = testeable)
            work_q, out_q = Queue(), Queue()
            # evento POR LOTE: no rebindear stop_event (el de /stop) — bug CONC-1
            lote_stop = threading.Event()
            local = cfg.ia.local_enabled
            n_workers = max(1, min(cfg.ia.local_concurrency if local else cfg.ia.concurrency,
                                   len(lote)))
            # H3: precargar ANTES de arrancar threads — si un worker llegara a
            # Empty antes de los put, saldría y dejaría jobs huérfanos (deadline muerto)
            for j in lote:
                work_q.put(j)
            threads = []
            for _ in range(n_workers):
                t = threading.Thread(target=self.worker_fn,
                                     args=(cfg, work_q, out_q, n_lote,
                                           lote_stop, mercado, p_desc,
                                           self.extract_fn if local else None),
                                     daemon=True)
                t.start()
                threads.append(t)

            # 3. main: consume resultados y escribe (ÚNICO escritor)
            #    deadline anti-cuelgue: len/CONCURRENCY × peor caso × 1.5, mín 300s
            #    (spec-ia-local P1-4: en modo local el peor caso es local_timeout)
            peor_caso = cfg.ia.local_timeout if local else 242
            deadline = time.time() + max(300, (len(lote) / n_workers) * peor_caso * 1.5)
            # modelo real: apply_ia_result lee parsed["_ia_model"] (lo pone
            # _extract_local_con_fallback) — cloud de fallback NO se etiqueta local
            st = self.consume_fn(conn, cfg, out_q, lote, n_lote, deadline,
                                 stop_event=lote_stop)
            st_run["lots_done"] += 1 if st["recibidos"] else 0
            st_run["ia_failures"] += st["ia_failures"]
            st_run["breaker_trips"] += st["breaker_trips"]
            st_run["hechas"] += st["recibidos"]
            conn.commit()
            lote_stop.set()

            # 4. (enrich ya corrió PRE-IA — FIX B) rescore SOLO del lote
            self.phase("rescore lote", detail=f"{n_lote + 1}/{n_lotes}")
            database.rescore_ids(conn, list(lote_ids), self.version_id, cfg,
                                 compute_score, market_score_fn=compute_market_score)

            # 5. publish del lote (consume presupuesto del barrido)
            if cfg.channel.enabled and cfg.channel.chat_id and st_run["presupuesto_canal"] > 0:
                self.phase("canal: publicando lote", detail=f"{n_lote + 1}/{n_lotes}")
                try:
                    from ..channel import publish_channel
                    ch_stats = publish_channel(cfg, conn, api,
                                               budget=st_run["presupuesto_canal"])
                    st_run["presupuesto_canal"] -= ch_stats["posted"]
                    st_run["channel_posts"] += ch_stats["posted"]
                    log.info("canal lote %d: %s (presupuesto restante %d)",
                             n_lote + 1, ch_stats, st_run["presupuesto_canal"])
                except Exception as e:
                    log.warning("canal lote falló (continúa): %s", e)
            elif cfg.channel.enabled and cfg.channel.chat_id and st_run["presupuesto_canal"] <= 0:
                log.info("canal lote %d: presupuesto del barrido agotado — sin publish (OPS-5)",
                         n_lote + 1)
        return st_run
