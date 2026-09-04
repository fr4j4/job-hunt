# Spec v4 — Pipeline por lotes con IA paralela (productor-consumidor, conexión única)

**Estado:** reemplaza el flujo monolítico de `cmd_run` para IA + enrich + rescore + canal.
**Base:** decisiones del usuario (03-04 sep): lotes incrementales publicando al canal, IA
paralela manejada por el programa principal con UNA única conexión SQLite escrita solo
desde el hilo principal, cero conexiones SQLite en threads workers.

## 1. Problema

El flujo actual de `cmd_run` es monolítico:

```
fuentes → indexar TODAS → IA TODAS (secuencial) → enrich → rescore TODO → publish → digest
```

Con 252 nuevas (pool vacío) tarda 50-95 min sin publicar nada: el canal queda
silencioso durante el scan, un crash a mitad pierde el trabajo parcial, y el
rescore global final re-procesa ofertas ya listas.

Medido en producción (04-sep, scan real): 252 nuevas × ~8s IA = ~55 min de fase IA
antes del primer post. Mensaje vivo estático "IA complementaria — 252 nuevas".

## 2. Solución: pipeline por lotes con workers IA paralelos

### 2.1 El flujo nuevo

```
fuentes → indexar TODAS (con fit score al vuelo) → new_jobs
    → ordenar new_jobs por fit score DESC (los mejores primero)
    → dividir en lotes de IA_BATCH_SIZE (default 40)
    → PARA CADA LOTE:
        1. llenar cola de trabajo con las ofertas del lote
        2. arrancar N workers IA (threads daemon, default 2) — SOLO HTTP, cero DB
        3. main loop: consumir resultados de la cola de resultados,
           escribir en SQLite CON LA CONEXIÓN ÚNICA del main (commit cada ~10)
        4. al agotar el lote: enrich_pending(máx 8) → rescore del lote
           (UPDATE por group_id, no global) → publish_channel() → ack de progreso
    → rescore global final LIVIANO (solo fechas/pendientes, ver §2.6)
    → digest
```

### 2.2 Patrón productor-consumidor (decisión del usuario)

**Regla de oro: la conexión SQLite vive SOLO en el hilo principal. Los threads
workers NO tocan la DB — hacen HTTP puro.**

```
HILO PRINCIPAL (dueño de la única conn SQLite)
│
├─ work_queue (Queue): ofertas del lote pendientes de IA
│
├─ WORKER IA ×N (threads daemon): saca job → ia_extract(job) → resultados.put()
│   └── ia_extract ya es HTTP puro (requests.post) — cero refactor de red
│
└─ MAIN: resultados.get(timeout=30) → UPDATE ofertas (conn única) → commit por ~10
    → cuando la cola de trabajo se vacía Y resultados procesados = tamaño lote:
        fin del lote → rescore → publish → siguiente lote
```

Por qué este patrón:
- `database is locked` es imposible por construcción: un solo escritor (el main)
- WAL feliz: escrituras pequeñas ordenadas desde un hilo, lecturas cero en workers
- Transacciones atómicas por lote — sin fragmentar estado entre threads
- `ia_extract` NO se refactoriza: ya es puro HTTP (`requests.post` + parse JSON)
- Timeout por oferta ya existe (`IA_TIMEOUT`)

### 2.3 Composición de cada lote

- **Orden:** `new_jobs` ordenado por fit score DESC antes de trocear → los lotes
  van de mejor a peor → las ofertas publicadas primero son las mejores del pool
  (mitiga el sesgo "lote 1 compite solo contra sí mismo").
- **Tamaño:** `IA_BATCH_SIZE` (default 40). El último lote puede ser menor.
- **El publish por lote** respeta `CHANNEL_MAX_POSTS` — si el lote genera 15
  candidatas, publica 10 y las 5 restantes quedan para el publish del lote
  siguiente (idempotente por `notified_channel_at`).

### 2.4 Workers IA

- `IA_CONCURRENCY` (default 2, rango 1-6): threads del pool. 2 = 2× throughput
  con riesgo de rate-limit mínimo. Configurable en .env sin código.
- Cada worker: `while True: job = cola.get() → parsed = ia_extract(cfg, job) →
  resultados.put((job, parsed or None)) → task_done()`. Si ia_extract retorna
  None (fallo tras retries), se escribe igual con parsed=None (la oferta queda
  sin IA, la toma el batch nocturno).
- **Circuit breaker:** si el main detecta ≥3 fallos consecutivos (429/timeout),
  los workers restantes se reducen a 1 para el resto del barrido (log warning).
  Se re-evalúa al siguiente barrido.
- Timeout global del lote: `IA_LOT_TIMEOUT_S = 600` — si el lote excede, los
  pendientes se marcan sin-IA y el pipeline sigue (no cuelga el daemon).

### 2.5 Escritura (solo main)

- `resultados.get(timeout=30)` — si vence sin resultados y la cola de trabajo
  está vacía y no hay workers activos → lote terminado.
- Cada resultado: UPDATE de ai_* fields por group_id (mismo código actual de
  `run_ia_batch`, extraído a función `apply_ia_result(conn, cfg, job, parsed)`)
- Commit cada 10 resultados (WAL aguantando escrituras pequeñas de un escritor).
- `ai_opinion` viene en el mismo parsed (schema ya actualizado).

### 2.6 Rescore: del lote, no global

- **Por lote:** UPDATE de fit/market/date_canonical SOLO para los group_id del
  lote (función nueva `rescore_ids(conn, ids, version_id, cfg)` — misma lógica
  de rescore_all acotada por WHERE group_id IN (...)). El publish del lote usa
  estos scores.
- **Global final:** al terminar todos los lotes, UNA pasada de `rescore_all`
  LIVIANA — pero como los lotes ya escribieron scores frescos, esta pasada solo
  cubre ofertas cambiadas por enrich_pending (fichas) y pendientes de lotes con
  fallo. Costo ~1s en pool actual.

### 2.7 Publish por lote

- Al final de cada lote: `publish_channel(cfg, conn, api)` — idempotente por
  `notified_channel_at`, respeta `CHANNEL_MAX_POSTS`. Los posts salen con
  market score fresco del rescore del lote.
- El ACK del mensaje vivo se actualiza por lote: `▸ IA 40/252 · lote 1/7 listo —
  publicadas 10`.
- `/channel_publish` manual sigue funcionando igual (publica lo que esté en cola).

### 2.8 Progreso en mensaje vivo

El `on_phase` del scan se extiende: `on_phase("IA complementaria", progreso=f"{hechas}/{total}")`
→ mensaje vivo muestra `▸ IA complementaria — 40/252 · lote 1/7 · publicadas 10`
(actualiza cada lote, no cada oferta — el throttle 15s existente se mantiene).

## 3. Config (.env)

```
IA_BATCH_SIZE=40          # ya existe — ahora es también el tamaño de lote
IA_CONCURRENCY=2          # threads IA (1 = secuencial actual; 2-3 recomendado)
IA_LOT_TIMEOUT_S=600      # circuito anti-cuelgue por lote
```

## 4. Cambios de código

### 4.1 `jobhunt/enrich.py`
- `run_ia_batch(conn, cfg, profile_desc, max_n=None, groups=None, progress=None)`
  SE MANTIENE intacto (lo usan `/enrich` manual y el batch nocturno — flujo
  secuencial simple, no lo tocamos).
- NUEVO `run_ia_lote_parallel(cfg, jobs: list[dict], on_result=None) -> int`:
  - cola de trabajo (queue.Queue) + N workers (threading, daemon)
  - workers llaman `ia_extract(cfg, job)` — SIN conn
  - retorna vía callback/queue; el main escribe (§4.3)
  - circuit breaker interno (≥3 fallos consecutivos → workers=1)

### 4.2 `jobhunt/db.py`
- NUEVA `rescore_ids(conn, group_ids, version_id, cfg, market_score_fn)` —
  rescore acotado a una lista de group_id (mismo dual-write + aislamiento por
  fila que rescore_all). `rescore_all` sigue igual (lo usan /rescore, cron, bot).

### 4.3 `jobhunt/cli.py` (`cmd_run`)
- Reemplaza el bloque monolítico IA→enrich→rescore→publish por el loop de lotes (§2.1).
- `publish_channel` se mueve DENTRO del loop (una llamada por lote).
- `enrich_pending` sigue en cada lote (máx 8) — Anillo A incremental.
- El digest al grupo queda al final (una sola vez, como hoy).

### 4.4 `jobhunt/bot.py`
- `on_phase` extendido para progreso fino (§2.7). Sin cambios en handlers.

### 4.5 `jobhunt/channel.py`
- SIN CAMBIOS (publish_channel ya es idempotente y se llama N veces por barrido
  sin problema).

## 5. Tests (extensión de tests/test_canal.py)

1. `test_lotes_orden_score`: 100 nuevas con scores mixtos → los lotes se arman
   mejor-primero (el lote 1 contiene los 40 mejores).
2. `test_workers_ia_no_tocan_db`: mock de ia_extract que falla si recibe conn —
   garantiza el contrato HTTP-puro.
3. `test_circuit_breaker`: ia_extract que falla 3 veces seguidas → workers
   reducidos a 1, el barrido continúa con las restantes sin IA.
4. `test_lot_timeout`: ia_extract que cuelga → IA_LOT_TIMEOUT_S corta el lote,
   pendientes quedan sin IA (van a cola nocturna), barrido continúa.
5. `test_rescore_ids`: solo los group_id del lote cambian score.
6. `test_publish_por_lote`: 3 lotes × 40 → publicaciones incrementales, suma
   ≤ CHANNEL_MAX_POSTS por invocación, idempotencia total al final.
7. `test_write_solo_main`: instrumentar sqlite3.connect para contar conexiones
   creadas durante un lote — exactamente 1 (la del main).

## 6. Despliegue

1. commit en feature/canal-market-score → push → restart service
2. `/search` de prueba → observar lotes en el mensaje vivo + posts incrementales
3. `IA_CONCURRENCY=2` default; si hay 429s en logs → bajar a 1 (secuencial) o
   subir a 3 según comportamiento; medible via `/enrich status` + journalctl

## 7. Edge cases

- new_jobs ≤ BATCH_SIZE → un solo lote, flujo idéntico al actual
- IA disabled → los lotes saltan la IA, publican con fit score al vuelo
  (comportamiento ya definido en spec v2)
- Ofertas con ia_extract=None → sin IA, no bloquean el lote (van a cola nocturna)
- Crash del daemon a mitad de lote → ofertas con IA hecha pero sin rescore
  quedan para el rescore global del siguiente barrido (idempotente por group_id)
- `/enrich` manual durante un scan → protegido por _op_busy (como hoy)
- Batch nocturno 03:00 → sin cambios (secuencial, tope IA_BATCH_SIZE)

## 8. Fuera de alcance

- Paralelismo de los lotes completos (solo la IA dentro del lote es paralela)
- Concurrencia > 3 (riesgo rate-limit sin beneficio medible)
- Multiprocessing (SQLite + forks complica; threads bastan para I/O-bound)
- Enrich (Anillo A) paralelo — Playwright/xvfb no es thread-safe; queda
  secuencial max 8/lote