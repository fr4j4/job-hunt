# Deuda técnica v4.1 — pendientes de la auditoría multi-agente

**Fecha:** 2026-09-04 · **Origen:** auditoría con 4 agentes (DEV, QA/OPS, TEST, ARQUITECTO)
**Reportes completos:** `.hermes/plans/audit-v4.1/reporte-{dev,qa-ops,test,arquitecto}.txt`
**Estado al documentar:** 48/48 tests · bot activo · todos los P0/P1 de código ya corregidos

---

## D1 — Test de integración del glue de `cmd_run` (TE-P1-4 + H6) · media

**Qué:** la orquestación del barrido (cli.py, bloque del pipeline por lotes ~L293-400) es la
única parte sin cobertura. Los helpers (`worker_ia`, `consume_lote`, `lotes_por_fit`,
`rescore_ids`, `publish_channel`) sí tienen tests — falta el pegamento.

**Por qué importa:** aquí se coló el P0 del INSERT scan_log (8 placeholders / 7 columnas)
con la suite entera en verde. Con un test de integración, no llega a producción.

**Cómo retomarlo (~1-2h):**
1. Refactor de inyección: los imports de fuentes/IA están dentro de `cmd_run`
   (`from .sources import linkedin...`, `from .enrich import ...`). Moverlos a
   parámetros con default o a una factory inyectable — SIN cambiar comportamiento.
2. Test: montar sqlite `:memory:` + fuentes mock que retornen 3-5 ofertas + IA mock
   (`extract_fn` inyectado o monkeypatch de `ia_extract_detail`) + `publish_channel`
   con `mock_api` (patrón ya usado en test_v41.py).
3. Aserciones: lotes en orden de fit, contadores C5 correctos en scan_log, presupuesto
   por barrido respetado, `run_ia_batch` nocturno recoge los pendientes (cierra D4).

## D2 — Limpiar complejidad muerta en `consume_lote` (H5) · baja

**Qué:** quedaron dos mecanismos sin uso en producción:
- Compat 3-tuple (cli.py ~L118-122): acepta `(job, parsed, lote_id)` además del actual
  de 4 `(job, parsed, err_kind, lote_id)`. Todos los productores mandan 4 → rama muerta.
- Triple inyección: `on_result` + `apply_fn` + default. Producción usa solo el default;
  tests usan `on_result`.

**Cómo retomarlo (~15min):** borrar el unpicking de 3-tuple y elegir UN mecanismo de
inyección (sugerencia: mantener `on_result` para tests, eliminar `apply_fn`).
Correr suite después — el test de terminación ejercita el worker real.

## D3 — Mover `canonical_date`/`age_days` a módulo neutro (H7) · baja

**Qué:** son lógica pura de fechas que viven en `channel.py` (I/O Telegram). `db.py` y
`scoring.py` hacen imports locales anti-ciclo (`from .channel import canonical_date`).
Capa de datos dependiendo de capa de presentación.

**Cómo retomarlo (~30min):**
1. Crear `jobhunt/fechas.py` (o mover a `db.py`, que ya tiene helpers) con
   `normalize_date`, `canonical_date`, `age_days` (hoy en channel.py ~L89-116).
2. `channel.py` re-exporta por compat: `from .fechas import canonical_date, age_days`.
3. Reemplazar los 3 imports locales: db.py:173, db.py:206, scoring.py:256.
4. Suite verde → listo. Cero cambio de comportamiento.

## D4 — Test del flujo completo "pendientes sin IA → batch nocturno" (TE-P1-1 parcial) · media

**Qué:** la red de seguridad C9: lo que el pipeline no procesa (deadline/breaker) queda
sin IA y el batch nocturno lo recoge con la cola relajada
(`length(description)>200 OR description_source!=''`).
Probado: el drain deja pendientes ✓ y el WHERE relajado existe ✓.
Falta: el test que une ambos extremos.

**Cómo retomarlo (~30min):**
1. sqlite `:memory:` con 2 ofertas sin IA: una desc larga, una desc corta de fuente
   (`description_source='aira-spa'`, `length<200`).
2. `monkeypatch.setattr` de `ia_extract_detail` a nivel de módulo (enrich.py) —
   que retorne `({"opinion": "x"}, "")` sin HTTP.
3. Correr `run_ia_batch(conn, cfg, ...)` y asertar que procesó AMBAS (la corta también).
4. Bonus: verificar `ia_queue_count` las cuenta (ya alineado con C9, fix OPS-6).

---

## Contexto para la retoma

- **Branch:** feature/canal-market-score (todo sin commitear al momento de este doc)
- **Suite:** `.venv/bin/python -m pytest tests/` (48 tests, ~50s; los 12 de v4.1 en test_v41.py)
- **Contratos:** spec-v4.1-final.md §3 (worker §3.1, terminación §3.2, budget §3.3,
  rescore_ids §3.4, enrich §3.5, guardas §3.6) — los fixes ya aplicados respetan esos contratos
- **No romper:** el gate anti-sqlite-en-workers (test_worker_http_puro con spy activo
  durante lote completo), el corte inmediato del breaker (DEV-2: break al trip),
  la precarga de work_q antes de los threads (H3)
- **Servicio:** `systemctl --user restart jobhunt` tras cualquier cambio;
  logs en `journalctl --user -u jobhunt -f`

## Fixes YA aplicados (referencia rápida, detalle en los reportes)

DEV-1 (.get en apply_ia_result) · DEV-2/OPS-3 (breaker corta lote) · DEV-3/OPS-1/H1
(scan_log INSERT+UPDATE final) · DEV-3b (dict garantizado) · DEV-4 (drain continue) ·
DEV-5 (commit rama expired) · DEV-6 (budget sin IA) · OPS-2 (guarda en sweep agendado) ·
OPS-4 (backoff+circuito nocturno) · OPS-5 (log presupuesto agotado) · OPS-6 (cola C9) ·
OPS-9 (marcadores digest post-éxito) · H3 (precarga work_q) · H4 (init_db al arranque) ·
worker get(timeout=1) · 3 tests reescritos (P0-3 gate real, commit-por-post 2ª conn,
scope con spy) + 3 endurecidos + fechas relativas + sleeps a 0.
Bugs de config aparte: INSERT placeholders (corregido en vivo) y
run_hours_utc strings→ints (batch nocturno nunca disparaba por schedule, desde v3).