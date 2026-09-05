# Spec v4.1 — Pipeline por lotes con IA paralela (REVISADA con 3 reviews)

**Supera a** `spec-v4-lotes-ia-paralela.md` (v4 original, NO implementable tal cual —
4 P0s confirmados por revisión cruzada). Este documento es el único contrato válido.
Bot: **DETENIDO** hasta implementación (decisión del usuario).

**Reviews consolidadas:** arquitectura (P0-1/P0-2 + P1-1..5), concurrencia
(P0-1/P0-2/P0-3 + P1-4..7, P2-8..10), producto/ops (P0-1 + P1-1..4, P2-1..4).
Hallazgos con ≥2 confirmaciones se marcan 🔁.

## 0. Decisiones de diseño pinzadas (usuario)

1. **Conexión SQLite ÚNICA en el hilo principal** — los workers IA NO tocan la DB
   (ni directa ni indirectamente: el contexto de mercado se pre-calcula y pasa
   como argumento, P0-3 de concurrencia + P0-1 de arquitectura).
2. **Paralelismo dentro del lote, lotes secuenciales.** `IA_CONCURRENCY=2`.
3. **Publish incremental por lote** (primeros posts ~5 min tras el scan).
4. Bot detenido hasta implementar.

## 1. Cambios respecto a v4 original (mapa de hallazgos → resolución)

| # | Hallazgo (fuente) | Resolución v4.1 |
|---|---|---|
| A1 🔁 | `ia_extract` abre su propia conn SQLite por llamada (P0-3 conc + P0-1 arq) | **Refactor obligatorio**: extraer contexto de mercado a `compute_market_context(conn)` (1 vez por lote, en main) → `ia_extract(cfg, job, profile_desc, mercado)` 100% HTTP-puro. Test #7 (contar sqlite3.connect) como gate |
| A2 🔁 | Terminación de lote indefinida: sin poison pill/join; resultado tardío cruza lotes (P0-2 arq + P0-1 conc) | Protocolo de terminación exacto (§3.4): workers SIEMPRE hacen `put((job, parsed or None, lote_id))` en try/finally → un resultado por oferta garantizado; main termina por **conteo** (`recibidos == len(lote)`), nunca por timeout-vacío; resultados de lote_id viejo se descartan |
| A3 🔁 | `CHANNEL_MAX_POSTS` se convierte silenciosamente en tope por lote → hasta 70 posts/barrido (P0-1 prod + P1-1 arq) | Nuevo knob `CHANNEL_MAX_POSTS_PER_SWEEP=15` (presupuesto por barrido); `publish_channel(..., budget_restante)` consume del presupuesto global del barrido; el tope por invocación sigue siendo `CHANNEL_MAX_POSTS` |
| B1 | `IA_LOT_TIMEOUT_S=600` < peor caso in-flight → abandona workers vivos y crea doble escritor (P0-1 conc) | **Timeout por oferta acumulada**: deadline_lote = `now + max(300, len(lote)/CONCURRENCY × 242 × 1.5)`. Al vencer: NO marcar pendientes sin-IA (A2); drenar resultados en vuelo con espera acotada 120s; lo que no llegue → queda para batch nocturno |
| B2 | Worker loop sin try/finally → task_done no se llama → join cuelga (P1-4 conc) | Worker: `try: ... finally: task_done()`. `ia_extract` envuelta: nunca lanza (retorna None) |
| B3 | Terminación por get(timeout)+cola-vacía es frágil | Terminación SOLO por conteo (§3.4). El timeout del lote solo corta la espera de nuevos resultados, nunca marca estado |
| B4 | `enrich_pending(máx 8)` retiene el write lock ~416s (P1-5 conc) — preexistente agravado ×7 lotes | Fix obligatorio en v4.1: commit por ficha dentro de `enrich_pending`; scope al lote con `groups=lote_ids` (patrón existente en run_ia_batch) — resuelve también P1-5 de arq |
| B5 | Breaker "reduce workers a 1" no implementable sobre threads vivos (P1-6 conc) | Flag compartido `threading.Event` (`reduce_workers`): workers N≥2 consultan antes de cada `get` y salen. Contador de fallos con lock; ventana: ≥5 fallos en últimos 20 resultados (P1-3 prod); reset por éxito; solo 429/5xx cuentan, timeout aislado no |
| B6 | `rescore_ids` hereda dual-write SIN aislamiento del fit (P1-7 conc) | try/except por fila para AMBOS scores en rescore_ids; fila fallida conserva score anterior + log |
| C1 | `_ia_sweep_maybe` no respeta `_op_busy` → batch nocturno compite con scan y envenena breaker (P1-4 arq + P1-1 prod) | `_ia_sweep_maybe` salta si `_SEARCH_STATE["running"]`; breaker distingue 429/5xx de timeout |
| C2 | `_digests_maybe` no respeta barrido en curso → digest con pool a medio procesar (P1-1 prod) | `_digests_maybe` pospone si `_SEARCH_STATE["running"]` (reintenta en el próximo tick del daemon) |
| C3 | Commit de publish_channel al final → crash entre send y commit pierde marcas (P1-2 arq) | `conn.commit()` **por post** dentro de publish_channel |
| C4 | Doble punto de publish ya no existe (wrapper) — rescore "LIVIANO" indefinido (P1-3 arq) | Se usa `rescore_ids` (nueva) por lote + `rescore_all` real al final (costo ~1s) |
| C5 | Observabilidad: sin duración por lote/fallos/trips en scan_log (P1-4 prod) | scan_log gana columnas: `lots_done`, `ia_failures`, `breaker_trips`, `channel_posts` |
| C6 | Progreso: on_phase sin lugar para "40/252 · lote 1/7" (P2-8 arq + P2-2 prod) | firma extendida: `on_phase(fuente, query="", page=0, detail="")` — detail va al mensaje vivo sin hack |
| C7 | Fit score pre-IA es predictor débil del market (P2-1 prod) | Aceptado: orden por fit es aproximado; el gate sigue siendo market score (el sesgo es cosmético). Spec deja de prometer "los mejores primero" |
| C8 | Sobrantes del tope: retraso real +4h si el lote siguiente también llena tope (P2-4 prod) | Presupuesto por barrido (A3) hace esto imposible: el publish del lote siguiente consume del presupuesto global restante |
| C9 | Fallback "batch nocturno" no toma ofertas con desc corta (P2-7 arq / P1-2 prod) | El gate de cola nocturna se relaja: `length(description)>200 OR description_source != ''` (documentado) |
| C10 | "database is locked imposible" sobre-prometido (P2-8 conc) | Reformulado: "sin contención escritor-escritor desde el pipeline; escritores externos dependen de busy_timeout" |

## 2. Pipeline (flujo final)

```
fuentes → indexar (fit al vuelo) → new_jobs ordenados por fit DESC
→ presupuesto_canal = CHANNEL_MAX_POSTS_PER_SWEEP (15)
→ PARA CADA LOTE (chunk de IA_BATCH_SIZE=40):
    1. mercado = compute_market_context(conn)      # main, 1 vez
    2. work_queue con las 40 · workers = IA_CONCURRENCY (daemon, solo HTTP)
    3. main: while recibidos < len(lote):
           job, parsed, lote_id = resultados.get(timeout=30)
           if lote_id != lote_actual: descartar      # resultado tardío cross-lote
           apply_ia_result(conn, job, parsed)        # conn única, main
           commit cada 10
       (deadline_lote corta la espera; drena 120s; lo que no llegó → batch nocturno)
    4. enrich_pending(conn, cfg, max_n=8, groups=lote_ids)   # commit por ficha
    5. rescore_ids(conn, lote_ids, version_id, cfg)          # aislamiento por fila, AMBOS scores
    6. publish_channel(cfg, conn, api, budget=presupuesto_restante)   # commit por post
       presupuesto_restante -= publicadas
    7. on_phase("IA", detail=f"{hechas}/{total} · lote {n}/{L} · publicadas {acum}")
→ rescore_all final (cubre enrich global y pendientes)
→ digest
```

## 3. Contratos exactos

### 3.1 Worker IA (único lugar con threads)
```python
def worker():
    while True:
        job = work_queue.get()
        try:
            parsed = ia_extract(cfg, job, profile_desc, mercado)  # HTTP puro, nunca lanza
        except Exception:
            parsed = None
        finally:
            resultados.put((job, parsed, lote_id))
            work_queue.task_done()
```
- `ia_extract` refactorizada: firma `(cfg, job, profile_desc, mercado="")`; cero sqlite3.
- `reduce_workers` (Event): workers índice ≥1 la consultan antes de cada get; si
  set → exit silencioso. Los fallos que la activan: solo 429/5xx, ≥5 en últimos 20.

### 3.2 Terminación del lote (la única condición correcta)
- Main sale del loop cuando `recibidos == len(lote)` (conteo, no timeout-vacío).
- `deadline_lote` SOLO corta la espera de nuevos resultados; a la vencida: drain
  acotado 120s, lo no recibido queda SIN IA (batch nocturno, cola relajada C9).
- El main NUNCA arranca el lote siguiente mientras queden resultados sin drenar
  del lote actual (los resultados llevan lote_id y se descartan si viejos).

### 3.3 publish_channel(cfg, conn, api, budget=None)
- `budget`: máx posts de ESTA invocación (presupuesto restante del barrido).
- `max_posts` por invocación se mantiene como tope duro adicional.
- commit **por post** (C3). Retorna stats con publicadas para descontar budget.

### 3.4 rescore_ids(conn, group_ids, version_id, cfg, market_score_fn)
- Mismo dual-write que rescore_all pero: try/except por fila para AMBOS scores
  (B6), UPDATE acotado por `WHERE group_id IN (...)`, refresca date_canonical.

### 3.5 enrich_pending(conn, cfg, max_n=None, groups=None)
- commit por ficha (B4). `groups` acota al lote (patrón run_ia_batch).

### 3.6 Guardas de daemon
- `_ia_sweep_maybe`: saltar si `_SEARCH_STATE["running"]` (C1).
- `_digests_maybe`: posponer si `_SEARCH_STATE["running"]` (C2); el digest corre
  en el próximo tick (el daemon itera cada ~1s, no se pierde).

## 4. Config (.env añadidos)

```
IA_CONCURRENCY=2                    # 1 = secuencial; 2-3 recomendado; máx 3
CHANNEL_MAX_POSTS_PER_SWEEP=15      # presupuesto por barrido (anti-flood real)
# IA_LOT_TIMEOUT_S eliminado — reemplazado por deadline calculado (§2)
```

## 5. Tests (extensión; los 36 existentes intactos)

1. `test_lote_orden_score` — lote 1 contiene los mejores por fit.
2. `test_worker_http_puro` — monkeypatch sqlite3.connect para contar: 0 llamadas
   desde workers durante un lote completo (gate real del P0-3).
3. `test_resultado_tardio_cross_lote` — worker lento del lote 1 → resultado con
   lote_id viejo → descartado, no escrito.
4. `test_terminacion_por_conteo` — worker que muere (excepción) → el resultado
   None garantizado llega → el lote termina (sin colgar).
5. `test_breaker_ventana` — 5 fallos 429 en 20 resultados → reduce_workers set.
   1 éxito → contador reset. Timeout aislado → no cuenta.
6. `test_lot_deadline_drain` — ia_extract cuelga >deadline → drain 120s →
   pendientes sin-IA → batch nocturno los recoge (con desc corta, C9).
7. `test_rescore_ids_aislamiento_ambos` — fit Y market lanzando → fila conserva
   scores anteriores, lote continúa.
8. `test_publish_commit_por_post` — crash simulado entre send 3 y 4 → 3 marcas
   persistidas (sin duplicar).
9. `test_enrich_pending_commit_por_ficha` — crash a mitad → fichas previas
   persistidas; lock no retenido >5s.
10. `test_presupuesto_por_sweep` — 2 lotes con 12 candidatas c/u y budget 15 →
    10 (lote 1, tope max_posts) + 5 (lote 2, presupuesto restante) = 15 total.
11. `test_digests_pospuestos_en_scan` — _digests_maybe durante scan → no corre.

## 6. Despliegue

1. Bot detenido (ya está). Implementar en feature/canal-market-score.
2. `pytest tests/` verde → `/search` manual de prueba → verificar: lotes en
   mensaje vivo, posts incrementales, presupuesto respetado.
3. Si 429 en logs → `IA_CONCURRENCY=1` (degrada a secuencial sin tocar código).
4. Revisar `/channel` para volumen real; ajustar CHANNEL_MAX_POSTS_PER_SWEEP.

## 7. Riesgos aceptados (documentados, no bugs)

- Orden por fit pre-IA es aproximado (C7) — el gate final es market score.
- Posts ya borrados a mano cuentan como skipped en wipe (Telegram limit).
- Ollama-cloud rate limits desconocidos: IA_CONCURRENCY es el knob, el breaker
  es la red de seguridad, la cola nocturna es la red de seguridad de la red.

## 8. Fuera de alcance

- Paralelizar lotes completos (solo IA intra-lote)
- Concurrencia > 3 · multiprocessing · Anillo A paralelo (Playwright no thread-safe)
- Backfill histórico · fit score y grupo interactivo: INTACTOS