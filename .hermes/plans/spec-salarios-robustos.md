# Spec — Salarios con procedencia, clasificación estadística robusta y comentarios que declaran anomalías

**Fecha:** 2026-09-04 · **Estado:** DRAFT — en evaluación (2 subagentes)
**Contexto:** post-spec v4.1 (commit acf477a). Los comentarios IA alucinaban salarios por un
contexto de mercado envenenado (AVG contaminado por outlier de $66M + constantes hardcodeadas).
El fix acf477a introdujo cuarentena fija (<400k, >15M) + prompt anti-alucinación. Esta spec lo
reemplaza con clasificación estadística robusta y la filosofía acordada con el usuario:
**el salario dudoso se publica tal cual y la anomalía se NOMBRA en el comentario — nunca se
oculta ni se "corrige" a ciegas**.

---

## 0. Principios (pinned, decisión del usuario)

1. **El canal es información, no garantía** — una oferta con salario anómalo se publica con su
   valor crudo tal cual declarado por la fuente; la advertencia vive en el comentario (💬), no en
   un bloqueo del post.
2. **El dato crudo nunca se destruye** — `salary_raw` preserva lo que la fuente declaró para
   auditoría; nada de "correcciones" silenciosas de valores.
3. **No decidir sin evidencia** — el árbitro feed-vs-texto solo actúa con la ficha legible
   (ACCESO_OK); con bloqueos (captcha/CF/timeout) no se toca nada.
4. **Estadística robusta sobre constantes mágicas** — MAD + IQR consenso en vez de umbrales
   fijos; los límites físicos absolutos son red de fondo, no clasificador primario.
5. **La IA no decide el salario** — extrae/clasifica; los números de mercado provienen del
   contexto calculado determinísticamente.

## 1. Contrato de datos

### 1.1 Columnas nuevas en `ofertas` (migración ligera, patrón PRAGMA table_info de db.py)

| Columna | Tipo | Default | Propósito |
|---|---|---|---|
| `salary_raw` | TEXT | `''` | Valor crudo tal como llegó (feed o texto). **Inmutable**: solo se escribe si estaba vacío (A7) |
| `salary_source` | TEXT | `''` | `feed` (listing inicial) · `text` (extraído del HTML de la ficha) · `ia` (extraído por IA desde descripción) · `''` = sin salario |
| `salary_status` | TEXT | `''` | Clasificación vigente: `trusted` · `suspect` · `implausible` · `''` = sin evaluar |
| `salary_note` | TEXT | `''` | Motivo corto de la ÚLTIMA decisión (A15): `annual_likely` · `below_floor` · `above_ceiling` · `mad_iqr` · `text_confirms` · `text_wins` · `source_unverifiable`. Precedencia: el clasificador pisa la nota del árbitro; la decisión del árbitro queda deducible de `salary_source`+`status` |
| `ctx_version` | TEXT | `''` | Identificador del contexto de mercado usado al generar la opinión (hash corto del string de contexto). `''` = opinión de era pre-versionado → candidata a regeneración |
| `fetch_fails` | INTEGER | `0` | Contador de accesos blocked/error consecutivos (A6) |
| `last_fetch_ok` | TEXT | `''` | ISO-UTC del último ACCESO_OK (A6) |

Migración: ALTER TABLE por columna faltante (mismo patrón scan_log C5). Sin backfill obligatorio
— filas antiguas quedan `salary_status=''` y se clasifican on-demand (§2.3/A11).

### 1.2 Ingesta (sources/*, scan) — interacción con upsert (A7)

El upsert re-indexa las mismas ofertas en cada barrido (occurrences++). Regla:
- `salary_raw`: **inmutable** — el crudo nuevo solo se escribe si `salary_raw=''`
- `salary_source='feed'`: solo si estaba `''`
- `salary_status`/`salary_note`: **se conservan** entre barridos (el veredicto del árbitro y
  del clasificador sobreviven la re-ingesta; violación de §0.2 evitada)
- Si el feed trae un crudo DISTINTO al de salary_raw con source ya en `text`: se ignora (el
  texto ya arbitró); queda en el log del scan para auditoría
- **No se filtra nada por rango en la puerta** (decisión §0.1)

### 1.3 Enrich de fichas — el árbitro (cuando ACCESO_OK)

Cuando `extract_structured` baja la ficha y detecta salario en el HTML. **Normalización previa
obligatoria (A4):** si el salario del texto/feed declara unidad anual (`/año`, `YEAR` en JSON-LD,
`/year`) → dividir por 12 ANTES de comparar/classificar y marcar note=`annual_likely`. Tolerancia
de coincidencia feed-vs-texto: **±1%** sobre el valor CLP mensual normalizado. USD: el parser
único (§2.2) ya lo convierte con la tasa existente de `_salary_to_clp_monthly`.

- Texto declara salario Y feed estaba en rango físico (§2) y coincide ±1% →
  `salary_source='text'`, `salary_status='trusted'`, note=`text_confirms`.
- Texto declara salario y CONTRADICE al feed:
  - Si el feed estaba clasificado `implausible` → **el texto gana** (única fuente verificable
    por humano): `salary` = valor del texto, `salary_source='text'`, status=`trusted`,
    note=`text_wins`, crudo anterior conservado en `salary_raw`.
  - Si ambos en rango pero distintos → texto gana igual (más fresco), note=`text_wins`.
- Texto NO declara salario y el feed era `implausible` → `salary` visible = `''`
  ("sin salario declarado"), `salary_raw` conserva el crudo, `salary_status='implausible'`,
  note=`source_unverifiable`. **El comentario IA nombra el valor crudo + la anomalía (§4).**
- **Guard de integridad (A3, violación de §0.5):** `apply_ia_result` SOLO escribe `salary`
  si `salary_source=''` — nunca pisa un salario con procedencia (feed/text) ni rellena un
  `salary=''` que el árbitro vació (status implausible + raw preservado). La IA propone;
  el árbitro dispuso.

### 1.4 Bloqueos de acceso (captcha / Cloudflare / 404)

`fetch_page` v2 retorna `(html, access)` con `access ∈ {ok, not_found, blocked, error}` (A5):
- `not_found`: HTTP 404/410, o patrón "no disponible/expirada" en el HTML, o (CB) redirect a
  listado genérico → `active=0` (generaliza el patrón CB a todas las fuentes)
- `blocked`: HTTP 403/429, o HTML con marcadores `cf-browser-verification` / `Just a moment` /
  `captcha` / `challenge-platform`, o HTML <500 chars sin JSON-LD → **no se decide nada**;
  `fetch_fails += 1`, retry con backoff 1h → 6h → 24h
- `error`: timeout/conexión → como blocked (reintentable), mismo contador
- **3-strikes (A6):** `fetch_fails >= 3` Y `last_fetch_ok` hace ≥48h → `active=0`.
  El upsert de re-ingesta **conserva** fetch_fails (no resetea strikes) y solo pone `active=1`
  si el access no fue not_found
- `ACCESO_OK` es el único estado que habilita el árbitro (§1.3) y resetea fetch_fails
- AIRA (SPA vía browser): `_extract_aira_spa` mapea sus excepciones al mismo contrato access

## 2. Clasificación estadística (nuevo módulo `jobhunt/stats.py`)

### 2.1 Funciones puras (testeables, sin DB)

```python
def classify_salary(value: int, pool: list[int]) -> tuple[str, str]:
    """Retorna (status, note). pool = salarios activos parseados (sin el valor evaluado)."""
```

**a) Límites físicos (red de fondo):** `value < 100_000` o `value > 30_000_000` →
(`implausible`, `above_ceiling`|`below_floor`).

**b) MAD (Iglewicz–Hoaglin, modified z-score):** `M = 0.6745 * (x - med) / MAD`,
con `MAD = mediana(|x_i - med|)`; outlier si `|M| > 3.5`.
**Guards degenerados (A2, P0):** pool vacío → solo decide física; `MAD == 0` (>50% de valores
iguales) → la MAD no decide (decide IQR solo; si IQR None → trusted salvo física); cualquier
excepción por oferta se captura en el llamador (try/except por oferta) — un caso degenerado
JAMÁS tumba el contexto completo del lote (degrada esa oferta a `trusted` con log warning).

**c) IQR fences de Tukey:** outlier si `x < Q1 - 1.5*IQR` o `x > Q3 + 1.5*IQR` (cuartiles
**method='inclusive'**, A14). Con n<8, IQR se considera no-calculable (None).

**d) Consenso:**
- físico → `implausible` (note según lado)
- MAD **y** IQR lo marcan (cuando IQR calculable) → `suspect`, note=`mad_iqr`
- solo uno lo marca o ninguno → `trusted`

**e) CV global (salud de la muestra):** `CV = SD_muestral(n-1)/mediana` (A12: fijo sample)
sobre el pool *sin los implausible*. `CV < 0.6` homogéneo · `0.6-1.0` disperso · `> 1.0`
orientativa. Solo para el contexto (§3), no clasifica individuos. Evidencia B (independiente):
CV pool limpio-27 = 0.477 (sample) / 0.493 (pop) — mismo veredicto "homogéneo".

### 2.2 Parser y pool usado (A1, P0)

**Parser único obligatorio: `_salary_to_clp_monthly` (scoring.py:183)** — el parser ya testeado
que maneja ambos formatos del pool (`CLP 2000000` y `$ 2.000.000,00 (Mensual)`) y USD.
**PROHIBIDO** reutilizar la regex+`int()` de compute_market_context acf477a:
`int('2.000.000')` → ValueError (repro real; 20/51 salarios activos están en formato CB).
Tests del parser obligatorios con casos reales de la DB (§6).

Pool: salarios activos parseados con `_salary_to_clp_monthly`, excluyendo el valor evaluado de
su propio pool (leave-one-out). Nota (A9, decisión de producto): **$300k (agrónomo colado de
jooble) clasifica `trusted`** — es correcto: la anomalía de esa oferta es de RUBRO (no-dev),
no de salario; la saca el dev-gate, no la estadística. No se añade banda relativa (sería
constante mágica con n=28 que solo afectaría esa oferta).

### 2.3 Call-sites y política de re-evaluación (A11)

- `classify_salary` se llama SOLO en: (1) el árbitro del enrich tras decidir salary, (2) el
  one-shot de backfill del deploy (§7.4)
- compute_market_context es **read-only** respecto a status: calcula stats con lo que haya
  (trusted + status='' no-física, A10), NO re-clasifica el pool (evita O(n²) por lote y
  status inestables entre lotes)
- Re-evaluación de una oferta existente: solo si su `salary` cambió (árbitro) o `status=''`

## 3. Contexto de mercado (compute_market_context v2)

### 3.1 Cálculo

1. Parsear todos los salarios activos con `_salary_to_clp_monthly` (§2.2) → `vals` (A1: esto
   ahora incluye el formato CB sin crashear — 20/51 ofertas que hoy quedan fuera)
2. `implausible_pool = [v for v in vals if física(v)]` → excluidos de stats (NO del pool de ofertas)
3. Resto = muestra confiable: mediana, P75, n, % declarantes, CV (§2.1.e)
4. <10 muestras → modo insuficiente (como acf477a)
5. Read-only sobre status (§2.3): no re-clasifica nada

### 3.2 Salida (string que recibe la IA)

```
"mediana $X (P75 $Y) de N ofertas con sueldo declarado (CV Z.ZZ — homogéneo/disperso) ·
P% declara · remoto R/T activas"
```

**A8 (corrige el draft):** el contexto de lote NO lleva la lista de crudos anómalos — solo
stats+CV. La anomalía de UNA oferta viaja EXCLUSIVAMENTE por la línea individual de §4.2
(construida en el main desde salary_status/salary_note de esa oferta). Razón: anunciar
crudos anómalos ajenos incentiva a la IA a verbalizar anomalías que no son de su oferta.
La heurística "probable anual" (valor > 12× mediana) y "error de fuente" (<0.3× mediana)
se calculan determinísticamente EN la construcción de esa línea individual (no en la IA).
Si CV > 1.0 → añadir "muestra dispersa — estadísticas orientativas".

## 4. Comportamiento de la IA (prompt + opinion)

### 4.1 System prompt (añadido a anti-alucinación de acf477a)

> "Si el contexto de mercado marca el sueldo de ESTA oferta como anómalo, la opinion DEBE:
> (1) citar el valor declarado tal cual, (2) señalar la anomalía con la hipótesis provista
> (probable anual/error de fuente), (3) comparar contra la mediana provista. NUNCA corrijas
> el valor, nunca lo omitas. Si la muestra es insuficiente o dispersa: sin comparaciones, solo descripción.
> Prohibido comentar anomalías de salarios de OTRAS ofertas — solo la de la oferta procesada."

### 4.2 Línea individual por oferta (única vía de anomalías, A8)

En `cmd_run`/`run_ia_batch` (MAIN, no worker): si la oferta a procesar tiene
`salary_status in (suspect, implausible)`, se añade al prompt de ESA oferta:
`Nota: el sueldo declarado de esta oferta ($crudo) fue clasificado anómalo (motivo:
salary_note; hipótesis: probable anual ≈ $X/mes | error de fuente) — coméntalo en
opinion según las reglas`. El SELECT de ambos caminos se amplía con
`salary_status, salary_note, salary_raw` para construir esta línea.

## 5. Consumidores

| Consumidor | Cambio |
|---|---|
| 💰 display (canal + DM) | **Sin cambios** — muestra `salary` visible siempre (crudo fiel) |
| 💬 opinion | Debe nombrar anomalías (§4) — verificación en §6 |
| Mediana/P75 contexto | Salarios `trusted` **O (`status=''` AND no-física)** (A10: evita digest salarial vacío mientras el backfill on-demand avanza; evoluciona la cuarentena fija a MAD+IQR consenso) |
| market_score | Sin cambio en esta iteración (bono por declarar se mantiene; el "-2 por implausible" queda como knob futuro documentado) |
| Digests salariales | Ranking excluye `implausible` (evita que $66M capere el top); incluye `trusted` y `status=''` no-física (A10) |
| Backfill del deploy (A10/A11) | El one-shot de §7.4 clasifica TAMBIÉN todas las ofertas con salary y `status=''` (única vez; después on-demand por §2.3) |

## 6. Tests (nuevos, tests/test_stats.py + ampliaciones)

0. `test_parser_formatos_db` (A1) — parser `_salary_to_clp_monthly` sobre casos reales:
   'CLP 2000000' → 2000000; '$ 2.400.000,00 (Mensual)' → 2400000; '$ 791.960,00 (Mensual)'
   → 791960; USD → conversión; 'CLP 15000' → 15000 (pasa, la física lo clasifica)
1. `test_classify_fisico` — 66M → implausible/above_ceiling; 15k → implausible/below_floor
2. `test_classify_mad_iqr_consensus` — **pool sintético congelado** (A13: no pool vivo);
   caso donde ambos marcan → suspect; caso solo-MAD → trusted
3. `test_classify_pool_chico` — n<8: IQR None → consenso = MAD solo; 4.9M trusted
4. `test_classify_degenerados` (A2) — pool [2M,2M,1.5M] LOO eval 1.5M → sin crash, trusted;
   pool vacío → solo física; MAD=0 con IQR None → trusted
5. `test_cv_salud` — CV sample (n-1); pool limpio <0.6; pool con 30% outliers → CV>1 → "orientativa"
6. `test_contexto_sin_anomalos_ajenos` (A8) — string de lote NO contiene crudos anómalos;
   <10 muestras → modo insuficiente
7. `test_linea_individual_anomalia` (A8) — oferta 66M recibe línea con hipótesis anual;
   oferta normal NO la recibe
8. `test_arbitro_text_wins` — feed implausible + texto declara 500k → salary=texto, status trusted, raw preservado
9. `test_arbitro_texto_sin_salario` — feed implausible + texto sin sueldo → salary visible '', raw preservado, status implausible; y apply_ia_result NO lo rellena (A3)
10. `test_arbitro_anual_normaliza` (A4) — texto 'CLP 24000000/año' → 2M mensual, note=annual_likely, compara ±1% contra feed
11. `test_acceso_bloqueado` — blocked: no se toca salary, fetch_fails+=1, sin desactivar
12. `test_404_desactiva` — not_found → active=0 (fuente no-CB)
13. `test_3_strikes` — 3 blocked en 48h → active=0; 2 blocked + ok → activa con ficha; upsert conserva strikes (A6)
14. `test_upsert_preserva_raw_status` (A7) — re-indexar no pisa salary_raw ni status
15. `test_iaclear_no_toca_salary` — iaclear sigue solo desmarcando IA (sin borrar salary/raw/status)
16. `test_digests_ranking_sin_implausible` — ranking salarial excluye implausible, incluye status='' (A10)
17. `test_ctx_version_se_guarda` — opinion regenerada lleva ctx_version = hash del contexto usado
18. `test_one_shot_ctx_viejo` — ofertas con ia_model!='' y ctx_version='' son detectadas por la query de regeneración

## 7. Rollout

1. Migración (7 columnas: salary_raw, salary_source, salary_status, salary_note, ctx_version,
   fetch_fails, last_fetch_ok) + módulo stats.py + tests → suite verde
2. compute_market_context v2 (parser único) + prompt → suite verde
3. Árbitro en enrich + guard apply_ia_result + fetch_page access + 3-strikes → suite verde
4. **One-shot de regeneración + backfill** (ctx_version + clasificación inicial):
   - `apply_ia_result` guarda `ctx_version = hash8(contexto)` al escribir opinion
   - Query de regeneración: `WHERE ia_model!='' AND ctx_version=''` (opinions pre-versionado,
     incluye las generadas 04:30-13:55 UTC con el AVG envenenado, ej. "mediana $5.97M")
     → vaciarles `ia_model` (solo la marca; la opinion fósil la limpia la sanitización
     de apply_ia_result al reprocesar) → `/enrich` regenera con contexto v2 (~30 ofertas)
   - Backfill de clasificación (A10): una vez, `classify_salary` sobre TODAS las ofertas
     activas con salary y `status=''` → el ranking de digests queda sano desde el día 1
   - Anómalas esperadas tras el backfill: 66M → implausible; 15k×2 → implausible;
     220k (CB, formato $) → implausible (entra al pool por el parser único, A1);
     **300k → trusted** (decisión A9: anomalía de rubro, no de salario)
5. Restart fuera de barrido (regla de casa) → validación en vivo: `/enrich` y revisar
   opinions de 66M (debe nombrar anomalía + hipótesis anual ≈ $5.5M/mes) y 220k
6. Commit + push

## 8. Fuera de alcance (explícito)

- Filtro de no-dev colados de jooble (agrónomo/mecánico/ayudantes) — problema de rubro en
  ingesta, otro epic (evidencia: 4 de 5 outliers de la banda baja son no-dev)
- Knob "-2 market_score por implausible" — documentado, no implementado
- Re-clasificación masiva continua — solo el one-shot del deploy; después on-demand (§2.3)
- Conversión automática anual→mensual en el VALOR mostrado — la normalización anual es para
  comparar/classificar (§1.3); el display conserva el crudo y la opinion verbaliza la hipótesis

## 9. Historial de revisión

- v1 (draft): propuesta original tras 3 iteraciones de diseño con el usuario
- v1+aediciones: hallazgos propios (ctx_version, one-shot, tests 13-14)
- **v2 (esta): ediciones por auditoría A (15 hallazgos, repros contra DB real) + validación
  numérica independiente B. Veredicto A era "rechazado"; los 2 P0 (parser, guards) y 9 P1
  incorporados como requisitos normativos. 300k decidido como trusted (A9: rubro ≠ salario).**