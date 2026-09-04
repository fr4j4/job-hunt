# Spec — Enrich por lotes IA (optimización de rendimiento)

**Fecha:** 2026-09-04 · **Estado:** v2 — APROBADA CON CAMBIOS (auditoría 2 subagentes, 2 P0 + 8 P1 + ~13 P2 corregidos)
**Contexto:** post-spec-salarios-robustos v2 (commiteada `8c03bd8`) · modelo `deepseek-v4-flash:0731-cloud` · `IA_TIMEOUT=180` activo
**Objetivo:** reducir el tiempo del enrich de ~51 min → ~14-20 min para 204 ofertas, sin degradar calidad de opinions ni romper guardas de concurrencia.

---

## 0. Principios

1. **El lote es solo la LLAMADA IA** — la escritura sigue siendo por oferta con commit individual (idempotencia parcial: un crash a mitad de lote no pierde las ya aplicadas).
2. **La correspondencia oferta↔resultado es garantizada por la API** (schema estricto con `idx` entero), nunca por coincidencia de strings.
3. **Nunca se pierde una oferta por un lote fallido** — fallback a individual + rescate parcial.
4. **El fetch paralelo no cambia el árbitro** — cada ficha se procesa con el mismo árbitro feed-vs-texto de la spec salarios-robustos.
5. **El modo lote es opt-in por knob** — `IA_BATCH_PROMPT=1` usa llamadas individuales (persisten sleeps reducidos y pool cacheado, que aplican igual).

---

## 1. Contrato de datos

**Sin cambios de esquema DB.** No se agregan columnas. Los campos IA son los que escribe `apply_ia_result` (enrich.py:522-589) — **paridad exacta con el modo individual**:

`opinion, resumen, fit_reason, seniority_real, rol_categoria, ingles, idiomas, modalidad, salario_clp_mensual, red_flags, green_flags, benefits`

(Nota: `techs` NO lo escribe `apply_ia_result` — se excluye del schema. `idiomas` es lista de objetos `{idioma, nivel, excluyente}`; `ingles`/`fit_reason` son strings; `benefits` es lista de strings.)

**Nuevo knob en `.env` + `config.py`:**

| Knob | Default | Rango | Efecto |
|---|---|---|---|
| `IA_BATCH_PROMPT` | `5` | 1-10 | Ofertas por llamada IA. `1` = comportamiento actual (individual) |

---

## 2. Modo lote IA (núcleo)

### 2.1 Nueva función `ia_extract_lote(rows, cfg, mercado, perfil) -> list[dict] | None`

- Recibe `rows` (lista de dicts con `group_id, title, company, location, description, salary, modality, salary_status, salary_note`), el contexto de mercado y el perfil.
- Arma el prompt de lote (formato verificado en benchmark):

```
Perfil del candidato: {perfil}

Contexto de mercado (para el campo opinion): {mercado}

--- OFERTA 1 ---
Título: ...
Empresa: ...
Ubicación: ...
Sueldo declarado: ... | Modalidad: ...
Descripción: {[:2400]}
[Nota: el sueldo declarado $X es ANÓMALO (probable cifra anual ≈ $Y/mes) — señálalo en opinion.]

--- OFERTA 2 ---
...

Responde un JSON array con UN objeto por oferta (idx 1..N), cada uno con:
idx, opinion, resumen, fit_reason, seniority_real, rol_categoria, ingles, idiomas,
modalidad, salario_clp_mensual, red_flags, green_flags, benefits.
Si una oferta no declara salario, salario_clp_mensual = 0 (cero, nunca inventes un monto).
```

- **System prompt**: idéntico al actual (anti-alucinación + regla de anomalías), sin cambios.
- **Body**: `temperature=0` + `response_format={"type": "json_schema", "json_schema": {"name": "lote_ofertas", "strict": true, "schema": {...}}}` (schema en §2.2).
- **Timeout/retries**: `cfg.ia.timeout` (180s) + `cfg.ia.retries` (1), mismo patrón de backoff que `ia_extract`.

### 2.2 Schema JSON estricto

```json
{
  "type": "object",
  "properties": {
    "ofertas": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "idx": {"type": "integer"},
          "opinion": {"type": "string"},
          "resumen": {"type": "string"},
          "fit_reason": {"type": "string"},
          "seniority_real": {"type": "string"},
          "rol_categoria": {"type": "string"},
          "ingles": {"type": "string"},
          "idiomas": {"type": "array", "items": {"type": "object",
            "properties": {"idioma": {"type": "string"}, "nivel": {"type": "string"},
                            "excluyente": {"type": "boolean"}},
            "required": ["idioma", "nivel", "excluyente"], "additionalProperties": false}},
          "modalidad": {"type": "string"},
          "salario_clp_mensual": {"type": "integer"},
          "red_flags": {"type": "array", "items": {"type": "string"}},
          "green_flags": {"type": "array", "items": {"type": "string"}},
          "benefits": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["idx", "opinion", "resumen", "fit_reason", "seniority_real",
                      "rol_categoria", "ingles", "idiomas", "modalidad",
                      "salario_clp_mensual", "red_flags", "green_flags", "benefits"],
        "additionalProperties": false
      }
    }
  },
  "required": ["ofertas"],
  "additionalProperties": false
}
```

Verificado contra ollama.com/v1: HTTP 200, `strict: true` aceptado, idx 5/5 correctos.
**Pendiente de spike**: objetos anidados (`idiomas` con `{idioma, nivel, excluyente}`) no probados en el benchmark — verificar antes de implementar (si ollama.com/v1 los rechaza, fallback: `idiomas` como array de strings y reconstruir objetos en el mapeo).

### 2.3 Mapeo idx ↔ oferta

- `data["ofertas"]` es un array; para cada objeto, `idx ∈ 1..N` → `rows[idx-1]`.
- **Defensa `int(idx)`** en el mapeo (un `1.0` flotante → `rows[0.0]` → TypeError).
- `idx` fuera de rango, duplicado o ausente → la oferta se cuenta como **faltante** (rescate §2.4). Política de duplicados: **gana el primero** del array; el duplicado no se rescata (evita llamadas IA desperdiciadas).
- El mapeo es posicional puro — nunca se compara `group_id` (el benchmark demostró que el modelo trunca IDs largos: 0/5 con group_id vs 5/5 con idx).

### 2.4 Validación y rescate parcial

1. `json.loads` del contenido (el schema garantiza estructura, pero el parseo sigue siendo defensivo).
2. `faltantes = [i for i in 1..N if i not in idxs_devueltos]`.
3. Si `faltantes` → las ofertas válidas se aplican primero (commit por oferta), luego las faltantes se reprocesan **individualmente** con `ia_extract` (formato `json` actual).
4. Si el array viene vacío o el parseo falla → **fallback total**: las N ofertas se procesan individualmente.

### 2.5 Fallback total (llamada de lote fallida)

- HTTP ≥400 tras retries, timeout, o JSON inválido → log warning + **las N ofertas se procesan individualmente** (cada una con su timeout 180s).
- **Distinguir `err_kind == "rate"` (429/5xx)**: NO fragmentar en individuales (agrava el rate limit). Backoff existente (2s) + reintento del lote; si persiste, dejar el grupo en cola para el batch nocturno.
- Timeout/JSON inválido → fallback individual (cubre el peor caso real: un lote con la oferta anómala $66M podría exceder 180s → el fallback individual la resuelve, 132s < 180s).
- El fallback NO es recursivo: una sola capa, sin riesgo de loop.
- **Peor caso documentado**: lote con anomalía = 180s + retry 2s + 180s + 5×132s ≈ 11.5 min para UN lote. Aceptado (caso raro); alternativa futura: procesar ofertas `suspect/implausible` individualmente (ya llevan nota de anomalía).

### 2.6 Escritura

- `apply_ia_result` se llama **por oferta** con su resultado mapeado (misma función, mismo commit por oferta, mismo `ctx_version`).
- El lote no es atómico: cada oferta commitea individualmente → crash a mitad de lote solo pierde las no aplicadas (quedan en cola con `ia_model=''`).

---

## 3. Fetch paralelo intra-lote

**Reemplaza el pipeline 2 etapas (E1) del resumen anterior** — con lote de 5, el fetch secuencial (5×3-5s ≈ 15-25s) rivaliza con la IA del lote (~18s) y el peor caso (5×25s timeout = 125s) roza el límite.

### 3.1 En `enrich_pending`

1. Agrupar `pending` de a `N = IA_BATCH_PROMPT` con **`ORDER BY score DESC` explícito** (hoy la query no tiene ORDER BY — enrich.py:392-394; el orden real es rowid).
2. Por grupo: `ThreadPoolExecutor(max_workers=min(N, 5))` → cada thread ejecuta **solo HTTP** (fetch + `extract_structured`/`_extract_aira_spa`). **Los threads NO tocan la DB** (patrón P0-3 del repo: "workers NO tocan DB", cli.py:60) — evita `ProgrammingError: SQLite objects created in a thread...` (check_same_thread=True, db.py:20-25).
3. El **main** aplica secuencialmente por ficha: access → árbitro → UPDATE + commit → fetch_fails/3-strikes (el bloque actual enrich.py:400-503, sin cambios de lógica).
4. Tras el fetch: **recargar** las filas con `WHERE group_id IN (...) AND active=1` — el árbitro actualizó `salary_status`/`salary_note` y la IA debe leerlos. **Si la recarga devuelve N' < N** (expiradas → active=0, blocked → fetch_fails+1), el prompt se arma con `len(recargadas)` (idx 1..N') — nunca con N.
5. Llamada de lote IA (§2) sobre las N' filas recargadas → `apply_ia_result` por oferta.
6. `done += N'` (conteo real de procesadas).

### 3.2 Invariantes

- El árbitro y el 3-strikes se ejecutan **en el main, por oferta** — sin cambios de lógica, solo paralelismo de fetch.
- `fetch_fails`/`last_fetch_ok` se actualizan por oferta (como hoy).
- Orden de commits: por grupo, no estrictamente por score — irrelevante para el resultado (mismo criterio que el scan con 2 workers).
- `_STOP_EVENT` se chequea **entre grupos** (no dentro del fetch paralelo). **Latencia real documentada**: un lote en timeout+retry bloquea hasta ~362s (180s + 2s + 180s) antes de que el stop se evalúe — documentar en `/help`.
- **AIRA** (`_extract_aira_spa`, playwright headless=False): serializar — 5 threads = 5 chromium concurrentes. Los grupos con ofertas AIRA se procesan con max_workers=1.
- **`ctx_version`**: `enrich_pending` debe calcularlo igual que `run_ia_batch` (hash del contexto) — sin esto, las opinions del enrich quedarían sin ctx_version y el one-shot de regeneración (spec salarios-robustos §7.4) las marcaría como obsoletas.

---

## 4. Pool de salarios cacheado (E2)

- Nueva función `get_salary_pool(conn, exclude_group_id=None) -> list[int]`: SELECT de salarios activos + parseo (el mismo cálculo que hoy hace enrich.py:470-472).
- Se calcula **una vez por invocación** de `enrich_pending`/`run_ia_batch` y se pasa a `classify_salary(v, pool)`.
- **Leave-one-out preservado**: al clasificar la oferta X, `pool_sin_actual = [v for v in pool if v != valor_de_X]` (sin costo de DB — el pool ya está en memoria). La spec salarios-robustos §2.2 exige excluir el valor evaluado de su propio pool; `classify_salary` NO filtra física del pool (stats.py:88 solo filtra `v is not None`), así que un valor borderline en su propio pool desplazaría mediana/MAD/IQR y podría voltear suspect↔trusted.
- `compute_market_context` reutiliza el mismo pool (un solo cálculo, dos usos).
- **Momento del pool**: pre-fetch (no incluye los salarios arbitrados del grupo — desviación menor del comportamiento actual, aceptada).
- Efecto: 204 SELECTs idénticos → 1 por invocación.

---

## 5. Sleeps (E4)

| Ubicación | Actual | Nuevo | Razón |
|---|---|---|---|
| enrich.py:502 (post-ficha) | 2s | 0.5s | El fetch paralelo ya da margen entre ofertas |
| enrich.py:418 (post-expired) | 1s | 0.3s | Rama rara, sin presión |
| enrich.py:654 (post-IA batch nocturno) | 3s | 0.5s | La cadencia del lote ya espacia las llamadas |
| Retries (325/336/341) | 2s | **sin cambio** | Backoff de rate limit — se mantiene |

---

## 6. Progreso y /stop

- **Progreso por lote**: el callback `on_phase` reporta `"IA lote {l}/{L} · {done}/{total} ofertas"` (ej: `IA lote 3/41 · 15/204 ofertas`). Elimina el "progreso clavado" que confundió al usuario (el lote tarda ~18s en silencio).
- **Throttle del callback**: `_mk_progress_cb` (bot.py:1240) limita a 30s — ajustar a **~15s** para que el reporte por lote (~18s) no se pierda.
- **/stop**: corta **entre lotes**. Latencia real: ~18s típico, **~362s peor caso** (lote en timeout 180s + retry 2s + 180s). Documentar en `/help`.
- **`enrich_pending` recibe `stop_event`** (hoy no lo tiene — firma enrich.py:377-378): plumb desde `cmd_run`/`cmd_enrich`/`_ia_batch_async`.
- El ACK de `/enrich` mantiene el conteo de cola actual.

---

## 7. Evidencia (benchmarks 2026-09-04, modelo :0731-cloud, 5 ofertas reales)

| Métrica | 5 individuales | 1 lote `format=json` | 1 lote `json_schema` |
|---|---|---|---|
| Tiempo total | 40.8s | 16.0s | 17.6s |
| Tokens entrada | 4,231 | 3,268 | 3,268 |
| Tokens salida | 9,195 | 4,357 | 5,142 |
| Correspondencia | — | 5/5 (idx) | 5/5 (idx) |
| Overhead schema | — | — | +1.6s/lote |

- **-55% tiempo, -42% tokens salida, -21% tokens entrada** (lote vs individual; precisos: in -22.8%, out -44.1%, coste combinado -37.4%).
- Opinions con schema citan el P75 real ($2,578,680) — calidad superior verificada.
- `json_schema` simple: 7.8s vs `format=json` 1.2s (overhead ~6× en caso trivial; ~1.6s en lote real — aceptable).
- **Estimación total**: 41 lotes × ~18s ≈ 12.3 min base + fetch paralelo (~4s/grupo) + sleeps (~2.5s/grupo) ≈ **14-20 min típico** (vs 51 min actuales). Peor caso con timeouts de fetch (25s): ~30 min. Objetivo < 20 min alcanzable en el caso típico. 1-2 lotes con anomalía añaden ~5-10 min (362s de timeout desperdiciado + 132s×2 individuales).

---

## 8. Tests (10 nuevos en `tests/test_stats.py` o `tests/test_lotes.py`)

1. `test_lote_prompt_bloques_idx` — el prompt de lote contiene N bloques `--- OFERTA {i} ---` con idx 1..N.
2. `test_lote_mapeo_idx_correcto` — mapeo `rows[i-1] ↔ data[idx]` posicional.
3. `test_lote_array_incompleto_rescate` — 4 de 5 devueltos → 4 aplicadas + 1 reprocesada individual.
4. `test_lote_fallback_individual` — llamada de lote falla (mock HTTP 500) → N individuales.
5. `test_lote_idx_fuera_rango` — idx=99 → oferta contada como faltante.
6. `test_fetch_paralelo_grupo` — N fichas procesadas en paralelo (mock `extract_structured`), todas commiteadas.
7. `test_pool_cacheado_una_vez` — `get_salary_pool` llamado 1 vez por invocación (mock conn con contador).
8. `test_schema_estructura` — el schema JSON tiene `idx` entero + todos los campos requeridos + `additionalProperties: false`.
9. `test_progreso_por_lote` — callback recibe `"IA lote {l}/{L}"`.
10. `test_knob_batch_prompt_1` — `IA_BATCH_PROMPT=1` → `ia_extract` individual (comportamiento actual).
11. `test_lote_paridad_campos` — **paridad de campos lote vs individual** (ingles/fit_reason/benefits/idiomas-array — habría cazado P0-1/P0-2): el schema contiene TODOS los campos que escribe `apply_ia_result`.
12. `test_lote_salario_null_no_escribe` — `salario_clp_mensual=0` → no escribe salary (P1-6).
13. `test_lote_nota_anomalia_por_oferta` — la nota de anomalía de UNA oferta aparece SOLO en su bloque del prompt (funcionalidad salarios-robustos §4.2).
14. `test_pool_loo` — pool cacheado excluye el valor evaluado (leave-one-out, P1-3).
15. `test_lote_idx_duplicado` — idx duplicado → gana el primero, sin rescate (P2-2).
16. `test_lote_array_vacio_fallback` — array vacío → fallback total individual (§2.4.4).
17. `test_recarga_n_menor` — recarga devuelve N'<N (expiradas) → prompt con idx 1..N' (P1-4).
18. `test_stop_entre_grupos` — stop_event seteado → no procesa el siguiente grupo (§6).
19. `test_ctx_version_enrich` — `enrich_pending` escribe ctx_version (P2-ctx).
20. `test_fetch_paralelo_threads_no_db` — los threads del fetch NO tocan la DB (solo HTTP; el main escribe) (P0-3).

**Suite completa**: 70 existentes + 20 nuevos = 90 tests, todos verdes antes del commit.
**Adaptación de tests existentes**: 6 tests llaman `enrich_pending(mem_db, None, max_n=1)` (test_stats.py:135,153,172,182,193,201) — el refactor debe guardar `if cfg and cfg.ia.batch_prompt > 1` (modo lote solo con cfg real) o actualizar los 6 tests.

---

## 9. Rollout

1. `config.py` + `.env`: knob `IA_BATCH_PROMPT=5`.
2. `enrich.py`: `ia_extract_lote` + schema + mapeo + rescate + fallback (§2). **Spike previo**: verificar objetos anidados del schema contra ollama.com/v1 (§2.2).
3. `enrich_pending`: fetch paralelo (threads solo HTTP) + recarga N' + pool cacheado + sleeps + `stop_event` + `ctx_version` (§3-5).
4. **`cmd_run` (scan)**: flag `solo_fetch=True` en su llamada a `enrich_pending` (cli.py:330) — el pipeline de workers IA de `cmd_run` se mantiene individual (fase 2). Sin esto, cada barrido haría doble IA (P1-3).
5. `run_ia_batch` (batch nocturno): mismo motor de lote (§2) — sin fetch (las ofertas ya tienen ficha).
6. Progreso por lote + throttle 15s + /stop entre lotes (§6).
7. Tests (20 nuevos) + adaptación de los 6 existentes + suite completa verde.
8. Commit + push (rama `feature/canal-market-score`).
9. Restart + **validación en vivo**: `/enrich` → medir tiempo real (objetivo < 20 min para la cola), verificar 2-3 opinions con P75/mediana correctos, verificar progreso por lote en el journal.

---

## 10. Fuera de alcance

- **E3 (concurrencia 2→3-4)**: se evalúa post-medición — el lote ya reduce llamadas 5× y el riesgo de 429 baja solo.
- **Scan (cli.py `worker_ia`/`consume_lote`)**: fase 2 opcional — el deadline B1 y el drain están calibrados para individual; se adapta solo si el benchmark del enrich lo justifica.
- **Cache de fichas HTML**: innecesario (cada oferta se visita una vez).
- **`max_tokens`**: no acelera (alarga la generación).
- **Cambios de esquema DB**: ninguno.

---

## 11. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Lote con oferta anómala excede timeout 180s | Fallback individual (§2.5) — 132s < 180s verificado; peor caso 11.5 min documentado |
| Contaminación cruzada entre ofertas del lote | idx + schema estricto + bloques separados en prompt; verificado en benchmark (opinions independientes) |
| Rate limit 429 | Backoff existente + 5× menos llamadas + fallback NO fragmenta en rate (§2.5) |
| Crash a mitad de lote | Escritura por oferta con commit individual (§2.6) — solo se pierden las no aplicadas |
| Progreso "clavado" percibido | Reporte por lote + throttle 15s (§6) |
| Doble IA en el scan | Flag `solo_fetch` en cmd_run (§9.4) |
| Threads tocando la DB | Threads solo HTTP; el main escribe (§3.1.2) |
| Schema con objetos anidados rechazado | Spike previo; fallback: idiomas como array de strings (§2.2) |

---

## 12. Métricas de éxito

- Tiempo enrich 204 ofertas: **< 20 min** (vs 51 min actuales; rango típico 14-20 min).
- Correspondencia idx: **100%** (garantizada por schema).
- Opinions con P75/mediana correctos (sin alucinación) — verificar 2-3 en vivo.
- Suite 90/90 verde.
- Costo API: **-37% tokens** esperado (medido: in -22.8%, out -44.1%).

---

## 13. Historial

- **v1 (2026-09-04)**: consolidación de la conversación — modo lote + json_schema estricto (verificado), fetch paralelo intra-lote (reemplaza E1), pool cacheado (E2), sleeps (E4), E3 pospuesto, progreso por lote, /stop entre lotes. Benchmarks reales en §7.
- **v2 (2026-09-04)**: auditoría con 2 subagentes (deleg_c0406bac) — APROBADA CON CAMBIOS. Corregidos: schema completo (idiomas array de objetos, fit_reason/ingles/benefits, sin techs, centinela 0 para salario), threads solo HTTP (P0-3), flag solo_fetch en cmd_run (P1-3), LOO en pool cacheado (P1-2), fallback distingue rate (P1-5), recarga N' (P1-4), stop_event en enrich_pending + latencia 362s, AIRA serializado, ctx_version en enrich, throttle 15s, int(idx) defensivo, política de duplicados, estimación 14-20 min, 20 tests + adaptación de 6 existentes.
