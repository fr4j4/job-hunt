# Spec — IA local (endpoint OpenAI-compatible) para el enrich

**Estado**: v2 — APROBADA CON CAMBIOS (auditoría 2 subagentes: 7 P1 + 9 P2 corregidos)
**Fecha**: 2026-09-04
**Rama**: `feature/canal-market-score`
**Relacionadas**: spec-enrich-lotes.md (v2, implementada) · spec-salarios-robustos.md (v2, implementada)

---

## §0 Principios

1. **Tokens gratis ≠ diseño igual**: el pipeline cloud está optimizado para minimizar
   llamadas (lote de 5, schema anidado, contexto compartido). Con local, la
   restricción desaparece → el diseño óptimo cambia a *llamadas más simples y
   robustas por oferta*, no a menos llamadas.
2. **El enrich son 2 naturalezas**: extracción (lectura determinista, schema la
   constriñe — un 7B local la hace bien) y opinión editorial (escritura con
   contexto de mercado y reglas anti-alucinación — el local la hace aceptable,
   más plana). Separarlas en 2 tareas con contexto acotado es el corazón del diseño.
3. **Cero regresión**: si `IA_LOCAL_ENABLED` no está configurado, el pipeline
   cloud actual corre EXACTO (mismo código, mismos knobs). El local es opt-in.
4. **Nunca se pierde una oferta**: fallback cloud por oferta si el local falla
   (timeout, JSON inválido, HTTP error). El breaker/retry existentes se reutilizan.
5. **El local es lento y está bien**: 10-30 min por 40 ofertas vs 6-13 min cloud.
   El usuario lo acepta explícitamente (tokens gratis). `IA_LOCAL_TIMEOUT=600` da margen.
6. **Privacidad**: con local, las descripciones de ofertas nunca salen de la máquina.
7. **Reversibilidad total**: `IA_LOCAL_ENABLED=false` restaura el pipeline cloud
   exacto (el branch local es opt-in puro; el código cloud NO se modifica). Los
   datos escritos por local quedan etiquetados con `ia_model=local_model` (P1-2)
   y son regenerables con cloud vía `/db_iaclear_confirm` + `/enrich`. El fallback
   cloud (`IA_LOCAL_FALLBACK_CLOUD=true`) mantiene el cloud operativo incluso con
   local activo.

---

## §1 Contrato de configuración (knobs nuevos en `.env`)

| Variable | Default | Descripción |
|---|---|---|
| `IA_LOCAL_ENABLED` | `false` | Activa el pipeline local. `false` → cloud como hoy (cero regresión) |
| `IA_LOCAL_BASE_URL` | `http://localhost:8080/v1` | Endpoint OpenAI-compatible local (llama.cpp / vLLM) |
| `IA_LOCAL_MODEL` | `qwen-2.5-7b-instruct` | Modelo local (GGUF servido por llama.cpp o vLLM) |
| `IA_LOCAL_TIMEOUT` | `600` | Timeout por llamada local (el local es lento; margen amplio) |
| `IA_LOCAL_RETRIES` | `1` | Reintentos por llamada local |
| `IA_LOCAL_FALLBACK_CLOUD` | `true` | Si local falla → cloud (nunca se pierde oferta). `false` → la oferta queda en cola |
| `IA_LOCAL_CONCURRENCY` | `2` | Threads IA locales paralelos (patrón worker_ia/consume_lote de cli.py) |

**Regla de activación**: `IA_LOCAL_ENABLED=true` → el pipeline local reemplaza al
cloud en TODOS los flujos de IA del enrich: `/enrich` (`run_ia_batch`), batch
nocturno, y el barrido (`cmd_run` — P1-3). El cloud queda SOLO como fallback
(si `IA_LOCAL_FALLBACK_CLOUD=true`).

**Interacción con `IA_ENABLED` (P1-6)**: `local_enabled=true` IMPLICA `enabled`
— las guardas existentes (`if not cfg.ia.enabled: return 0` en run_ia_batch,
`if not cfg.ia.enabled or not cfg.ia.api_key` en cmd_run) se ajustan a
`cfg.ia.enabled or cfg.ia.local_enabled`. El fallback cloud requiere `IA_API_KEY`
configurada; si no está, el fallback retorna `(None, "other")` y la oferta queda
en cola (comportamiento aceptable, documentado).

**Config.py**: `IaConfig` gana 7 campos nuevos (`local_enabled`, `local_base_url`,
`local_model`, `local_timeout`, `local_retries`, `local_fallback_cloud`,
`local_concurrency`), leídos con los mismos helpers `_env_bool/_env_int/_env`.
`.env.example` documenta los 7 knobs (P2-8).

---

## §2 Pipeline local — 2 tareas por oferta

```
IA_LOCAL_ENABLED=true → por CADA oferta de la cola C9:

  TAREA 1 — EXTRACT (lectura)
    input:  descripción acotada [:2000] + instrucciones precisas
    output: JSON plano {techs[], modalidad, seniority_real, rol_categoria,
                        ingles, idiomas[], red_flags[], green_flags[],
                        benefits[], salario_clp_mensual}
    schema: plano, sin anidar → robusto en llama.cpp (gramática GBNF simple)

  TAREA 2 — OPINION (escritura)
    input:  descripción acotada [:2000] + contexto de mercado (mediana/P75/n)
            + perfil del candidato (P1-5) + datos extraídos de TAREA 1
            + nota de anomalía (si aplica)
    output: JSON plano {opinion, resumen, fit_reason}
    reglas: anti-alucinación (solo números del contexto provisto),
            anomalías se nombran con hipótesis, nunca se corrigen

  → combinar ambos dicts → apply_ia_result (con model=local_model — P1-2)
  → si TAREA 1 o 2 falla → fallback cloud individual (ia_extract_detail)
```

### §2.1 Por qué individual y no lote para local

- El lote existe para amortizar tokens — con gratis no aporta.
- El schema anidado del lote (5 ofertas en un JSON) es el punto débil de
  llama.cpp (gramática GBNF con objetos anidados falla más). Individual con
  schema plano es robusto.
- Fallback granular: si la oferta 3 falla, no se pierde el lote.

### §2.2 Por qué 2 tareas y no 3+

- Extracción y opinión son las 2 naturalezas reales. `resumen`/`fit_reason`
  van con opinión (son escritura); `salario` va con extracción (es lectura).
- Más tareas = más overhead de llamadas sin ganancia de calidad.
- 2 llamadas × ~40 ofertas = 80 llamadas locales — gratis, sin rate limits.

### §2.3 Por qué contexto acotado en cada tarea

- Un 7B se confunde con prompts largos multi-tarea (olvida reglas del final).
- Prompt corto + instrucción única = mejor cumplimiento del schema.
- Menos tokens de entrada = respuesta más rápida (aunque no importe el costo).

### §2.4 Esquema de TAREA 1 (EXTRACT) — JSON plano

```json
{
  "techs": ["Py", "Java", "AWS"],
  "modalidad": "R" | "H" | "P" | "?",
  "seniority_real": "junior" | "semi" | "senior" | "lead" | "",
  "rol_categoria": "Full Stack" | "Backend" | "Frontend" | "Data" | "Mobile" |
                   "AI/ML" | "Tech Lead" | "DevOps/Cloud" | "QA" | "Software" |
                   "Seguridad" | "Ingeniería no-software" | "Analista/Empresa" |
                   "Profesor/Formación" | "Soporte/TI" | "No-tech" | "Otro",
  "ingles": "no" | "deseable" | "requerido" | "desconocido",
  "idiomas": [{"idioma": "inglés", "nivel": "avanzado", "excluyente": true}],
  "red_flags": ["..."],
  "green_flags": ["..."],
  "benefits": ["..."],
  "salario_clp_mensual": 2500000
}
```

- `salario_clp_mensual`: 0 si no se declara (centinela, nunca inventar).
- `techs`: máx 8, abreviaturas canónicas REALES de `_TECH_ABBR` (enrich.py:33):
  Py, Java, TS, JS, React, Angular, Vue, Node, K8s, Docker, AWS, GCP, Azure,
  TF, Scala, Go, .NET, C#, SQL, Postgres, Mongo, Redis, Kafka, FastAPI, Django,
  Spring, NiFi, Jenkins, CI/CD (P2-9).
- `idiomas`: array de objetos `{idioma, nivel, excluyente}` (mismo formato que
  `apply_ia_result` espera — enrich.py:831-841).
- **`ingles` es OBLIGATORIO (P1-1)**: sin él, `ai_ingles` queda vacío en local →
  el reporte de mercado pierde la distribución de inglés (regresión silenciosa).

### §2.5 Esquema de TAREA 2 (OPINION) — JSON plano

```json
{
  "opinion": "max 160 chars — comentario editorial",
  "resumen": "max 120 chars",
  "fit_reason": "max 140 chars — por qué conviene o no al perfil"
}
```

- Reglas de opinion (heredadas del prompt cloud, enrich.py:313-331):
  - NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', 'no apliques', 'practica').
  - Si el sueldo está declarado, DEBE comentarlo (comparar contra la mediana del CONTEXTO provisto).
  - ANTI-ALUCINACIÓN: solo números del CONTEXTO DE MERCADO provisto. Si la muestra
    es insuficiente, describir solo la oferta.
  - Si la oferta tiene nota de anomalía: citar el valor tal cual + señalar la
    anomalía con la hipótesis + comparar contra la mediana. NUNCA corregir ni omitir.

### §2.6 Prompt de TAREA 1 (EXTRACT) — plantilla

```
Eres un extractor de datos de ofertas de empleo chilenas. Respondes SOLO JSON válido.
Dato ausente → null. No inventes.

Oferta:
Título: {title}
Empresa: {company}
Ubicación: {location}
Sueldo declarado: {salary or "(no declarado)"}
Modalidad declarada: {modality or "(no declarada)"}
Descripción: {description[:2000]}

Responde SOLO JSON con: techs, modalidad, seniority_real, rol_categoria, ingles,
idiomas, red_flags, green_flags, benefits, salario_clp_mensual.
techs: máx 8, abreviaturas canónicas (Py, Java, TS, JS, React, Angular, Vue, Node,
K8s, Docker, AWS, GCP, Azure, TF, Scala, Go, .NET, C#, SQL, Postgres, Mongo, Redis,
Kafka, FastAPI, Django, Spring, NiFi, Jenkins, CI/CD).
ingles: "no" | "deseable" | "requerido" | "desconocido".
salario_clp_mensual: 0 si no se declara (nunca inventes un monto).
```

### §2.7 Prompt de TAREA 2 (OPINION) — plantilla

```
Eres un comentarista editorial de ofertas de empleo tech chilenas. Respondes SOLO JSON válido.

Perfil del candidato: {profile_desc}

Contexto de mercado (los ÚNICOS números que puedes citar):
{mercado}

Oferta:
Título: {title}
Empresa: {company}
Sueldo declarado: {salary or "(no declarado)"}
Datos extraídos: {techs, modalidad, seniority, rol_categoria, ingles}
{nota_anomalia}

Responde SOLO JSON con: opinion (max 160 chars), resumen (max 120 chars),
fit_reason (max 140 chars).

Reglas:
- opinion = comentario editorial: contexto de mercado, señal notable, comparación
  salarial. NUNCA consejos al candidato (prohibido 'destaca', 'pregunta',
  'no apliques', 'practica').
- ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar son los del
  CONTEXTO DE MERCADO provisto. Si la muestra es insuficiente, describe solo la oferta.
- Si el sueldo está declarado, DEBE comentarlo (comparar contra la mediana del contexto).
- Si la oferta tiene nota de anomalía: cita el valor tal cual, señala la anomalía
  con la hipótesis provista, compara contra la mediana. NUNCA corrijas ni omitas.
- fit_reason: por qué conviene o no al PERFIL del candidato provisto.
```

---

## §3 Implementación — funciones nuevas

### §3.1 `ia_extract_local(cfg, job, profile_desc, mercado="") -> tuple[dict | None, str]`

- **Firma CON `profile_desc` (P1-5)**: compatible con el hook `extract_fn` de
  `worker_ia` (cli.py:77 llama `fn(cfg, job, profile_desc, mercado)`).
- HTTP puro (sin SQLite) — mismo patrón que `ia_extract_detail` (enrich.py:336-365).
- POST `{cfg.ia.local_base_url}/chat/completions` con `Authorization: Bearer {api_key}`
  (llama.cpp/vLLM ignoran la key; si no hay key, enviar "local" — P2-6).
- `temperature: 0`, `response_format: {"type": "json_object"}` (P2-1: aceptado por
  llama.cpp Y vLLM — `format: "json"` es solo de llama.cpp).
- Retry loop `cfg.ia.local_retries`, `time.sleep(2)` entre intentos.
- **Connection refused → fallback inmediato** (P2-3): detectar `ConnectionError`
  y retornar `(None, "other")` sin esperar el timeout ni reintentar.
- Retorna `(dict, "")` si OK, `(None, "timeout"|"rate"|"other")` en fallo
  (mismos err_kind que `ia_extract_detail` — B5: timeout aislado no alimenta breaker).

### §3.2 `ia_extract_local_2tareas(cfg, job, profile_desc, mercado="") -> tuple[dict | None, str]`

- Orquesta TAREA 1 + TAREA 2:
  1. `ia_extract_local` con prompt EXTRACT → si falla → `(None, err_kind)`
  2. `ia_extract_local` con prompt OPINION (input: datos de TAREA 1) → si falla →
     `(None, err_kind)`
  3. Combina ambos dicts en uno solo (campos de TAREA 1 + TAREA 2) → `(combinado, "")`
- **Coerción de tipos (P2-6)**: un 7B con gramática json puede emitir
  `salario_clp_mensual` como string ("2.5M") o `techs`/`idiomas` como string.
  Normalizar: `salario_clp_mensual` → int/0, listas → listas, `idiomas` → dicts
  (espejo de la normalización del lote, enrich.py:499-504).
- El dict combinado tiene la MISMA forma que el de `ia_extract_detail` (incluye
  `ingles` — P1-1) → `apply_ia_result` no cambia su lógica.

### §3.3 `apply_ia_result` — parámetro `model` (P1-2)

- Mínimo cambio: `apply_ia_result(conn, cfg, r, parsed, ctx_version="", model=None)`
  — `model` default `cfg.ia.model` (cloud, comportamiento actual).
- En modo local, el dispatch pasa `model=cfg.ia.local_model` → `ia_model` en DB
  dice la verdad (trazabilidad + regeneración selectiva).

### §3.4 `enrich_pending` / `run_ia_batch` — dispatch local

- Si `cfg.ia.local_enabled`:
  - `N = 1` para la IA (individual, sin lote — §2.1)
  - **Fetch paralelo MANTENIDO (P1-7)**: el fetch agrupado (enrich.py:698) no
    depende de N — se agrupa por lote de fetch (5) y la IA corre individual
    después. No serializar el fetch.
  - Por oferta: `ia_extract_local_2tareas` → `apply_ia_result(model=local_model)`
  - Si falla y `cfg.ia.local_fallback_cloud` → `ia_extract_detail` (cloud) → `apply_ia_result`
  - Si falla y NO fallback → oferta queda en cola (se reintenta en el próximo pase)
  - `ctx_version` se pasa tal cual (P2-3) — trazabilidad spec salarios-robustos §7.4
- Si NO `local_enabled` → pipeline cloud actual EXACTO (cero regresión).

### §3.5 `run_ia_batch` — concurrencia local (P1-4)

- En modo local, `run_ia_batch` usa el patrón `worker_ia`/`consume_lote` de
  cli.py (workers HTTP + main escribe — respeta P0-3 single-writer) con
  `ThreadPoolExecutor(max_workers=cfg.ia.local_concurrency)`.
- `IA_LOCAL_CONCURRENCY=2` es REAL (no knob muerto): 2 ofertas en paralelo.
- El progreso por lote se mantiene (callback `progress(done, total, título)`).

### §3.6 `cmd_run` (barrido) — cableado local (P1-3)

- `worker_ia` ya acepta `extract_fn` (cli.py:69) y `consume_lote` acepta
  `apply_fn` (cli.py:102) — hooks listos.
- En modo local: `worker_ia(..., extract_fn=ia_extract_local_2tareas)` y
  `consume_lote(..., apply_fn=lambda conn, cfg, r, parsed, ctx: apply_ia_result(conn, cfg, r, parsed, ctx, model=cfg.ia.local_model))`.
- **Deadline parametrizado (P1-4)**: `deadline = time.time() + max(300, (len(lote) / concurrency) * (cfg.ia.local_timeout if local_enabled else 242) * 1.5)`.
- El barrido es el mayor consumidor de IA (6×/día) — sin este cableado, el
  ahorro de tokens sería parcial (contradice §0.1).

---

## §4 Velocidad estimada (RTX 3060 12GB, Qwen-2.5-7B Q4_K_M)

```
TAREA 1 (EXTRACT):  ~10-20s por oferta (prefill 2K + decode ~200 tokens)
TAREA 2 (OPINION):  ~15-25s por oferta (prefill 3K + decode ~300 tokens)
→ 40 ofertas ≈ 20-30 min secuencial
→ con IA_LOCAL_CONCURRENCY=2 (P1-4, real) ≈ 10-15 min
```

- El usuario acepta la lentitud explícitamente (tokens gratis).
- `IA_LOCAL_TIMEOUT=600` da margen para el peor caso (desc larga + decode largo).
- El fetch paralelo se mantiene (P1-7) — no suma ~2-4 min extra.

---

## §5 Modelo recomendado

| Modelo | Tamaño (Q4_K_M) | VRAM | Calidad | Veredicto |
|---|---|---|---|---|
| **Qwen-2.5-7B-Instruct** | ~4.7GB | 12GB (3060) | Bueno en español + output estructurado | **Recomendado** |
| Qwen-2.5-14B-Instruct | ~9GB | 12GB (justo) | Mejor opinions, ~2× lento | Alternativa si se quiere más calidad |
| Llama-3.1-8B-Instruct | ~5GB | 12GB | Bueno pero peor en español | No recomendado |

- Servir con llama.cpp (`llama-server -hf Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M`)
  o vLLM (`vllm serve Qwen/Qwen2.5-7B-Instruct --quantization awq`).
- Si el schema plano falla en llama.cpp (gramática GBNF), mitigación: vLLM con
  xgrammar (soporta `response_format: json_schema` estricto).

---

## §6 Fallback y robustez

| Fallo | Comportamiento |
|---|---|
| TAREA 1 timeout/JSON inválido | Reintento local (retries) → fallback cloud individual → si falla, oferta en cola |
| TAREA 2 timeout/JSON inválido | Ídem |
| Local caído (connection refused) | Fallback cloud SIN esperar timeout (P2-3: detectar ConnectionError) |
| `IA_LOCAL_FALLBACK_CLOUD=false` | Oferta queda en cola (se reintenta en el próximo pase) |
| Cloud también caído | Oferta en cola (comportamiento actual) |
| Local responde 429/5xx | `err_kind="rate"` → breaker `rate_racha` (OPS-4) — el err_kind FINAL post-fallback es el que alimenta el breaker (P2-7e) |

- **Orden breaker (P2-7e)**: local falla → fallback cloud → si el cloud también
  falla, el err_kind del CLOUD es el que alimenta `rate_racha` (un 429 local no
  debe tripular el circuito del cloud).

---

## §7 Tests (nuevos, ~16 → suite 111)

1. `test_local_enabled_dispatch` — `local_enabled=true` → se llama `ia_extract_local_2tareas`, no `ia_extract_lote`
2. `test_local_disabled_cloud` — `local_enabled=false` → pipeline cloud exacto (cero regresión)
3. `test_local_2tareas_combina` — TAREA 1 + TAREA 2 → dict combinado con TODOS los campos (incluye `ingles` — P1-1)
4. `test_local_paridad_campos` — paridad local↔apply_ia_result (espejo de `test_lote_paridad_campos`; detecta P1-1)
5. `test_local_tarea1_falla_fallback_cloud` — TAREA 1 timeout → fallback `ia_extract_detail`
6. `test_local_tarea2_falla_fallback_cloud` — TAREA 2 JSON inválido → fallback cloud
7. `test_local_fallback_disabled_queda_cola` — `local_fallback_cloud=false` → oferta en cola
8. `test_local_schema_plano` — el prompt EXTRACT pide schema plano (sin anidar)
9. `test_local_opinion_anti_alucinacion` — el prompt OPINION contiene las reglas anti-alucinación
10. `test_local_concurrency_knob` — `IA_LOCAL_CONCURRENCY` se lee y clamp (1-6)
11. `test_local_timeout_knob` — `IA_LOCAL_TIMEOUT` se lee (default 600)
12. `test_local_ia_model_etiqueta` — `apply_ia_result(model=local_model)` escribe `ia_model=local_model` (P1-2)
13. `test_local_ctx_version` — `ctx_version` escrito en modo local (P2-3)
14. `test_local_connection_refused_fallback` — connection refused → fallback sin esperar timeout (P2-3)
15. `test_local_breaker_cloud_no_tripula` — 429 local + fallback cloud OK → breaker NO se tripula (P2-7e)
16. `test_local_cmd_run_dispatch` — `cmd_run` con local_enabled usa `extract_fn=ia_extract_local_2tareas` (P1-3)

Suite total: 95 + 16 = **111 tests**.

---

## §8 Rollout

1. **Config**: knobs en `config.py` + `.env` + `.env.example` (P2-8) — defaults apagados → cero regresión
2. **Funciones**: `ia_extract_local` + `ia_extract_local_2tareas` en enrich.py
3. **`apply_ia_result`**: parámetro `model` (P1-2)
4. **Dispatch**: `enrich_pending` + `run_ia_batch` (concurrencia local) + `cmd_run` (extract_fn + deadline)
5. **Tests**: 16 nuevos → suite 111/111
6. **Setup local** (fuera del repo): `llama-server` con Qwen-2.5-7B Q4_K_M en la 3060
7. **Smoke**: `IA_LOCAL_ENABLED=true` + 3 ofertas → verificar extracción + opinión + fallback + etiqueta ia_model
8. **Producción**: activar con `IA_LOCAL_ENABLED=true` cuando el smoke pase
9. **Reversión**: `IA_LOCAL_ENABLED=false` + restart → cloud exacto (datos locales etiquetados, regenerables)

---

## §9 Fuera de alcance

- **Partición local-extracción + cloud-opinion**: se puede agregar después como
  knob (`IA_LOCAL_OPINION_CLOUD=true`) — la spec ya separa las tareas, el knob
  solo decide el destino de TAREA 2.
- **vLLM como servidor**: documentado en §5 como mitigación, no como requisito.
- **Modelos >14B**: no caben en 12GB con Q4 — fuera de alcance.
- **Embeddings/otros usos de la IA local**: solo el enrich en esta spec.
- **Cambios en la lógica de `apply_ia_result`**: ninguno (solo el parámetro `model`).

---

## §10 Historial

- v1 (2026-09-04): propuesta inicial — 2 tareas por oferta, schema plano,
  fallback cloud, knobs opt-in, cero regresión.
- v2 (2026-09-04): APROBADA CON CAMBIOS (auditoría 2 subagentes) —
  P1-1 `ingles` obligatorio · P1-2 `ia_model` etiqueta local · P1-3 `cmd_run`
  cableado · P1-4 deadline parametrizado + concurrency real · P1-5 firma con
  `profile_desc` · P1-6 `local_enabled` implica `enabled` · P1-7 fetch paralelo
  mantenido · P2 absorbidos (json_object, ctx_version, nota_anomalia helper,
  coerción, .env.example, breaker cloud, tests 16) · §0.7 reversibilidad.
