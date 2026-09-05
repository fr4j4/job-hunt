# Diseño: antigüedad multiplataforma + anti-repetición (por group_id) + catálogo de formatos de canal (v3)

Diseño producto/técnico para el modo canal. Complementa a `spec-canal-market-score.md` (v2)
y responde a la review `review-canal-market-score.md`. **Incorpora la DECISIÓN DE PRODUCTO
2026-09-03 del usuario (§D)** sobre republicaciones, que invalida el dedup difuso del canal.
Verificado contra el código real (db.py, dedup.py, cli.py, bot.py, enrich.py, sources/*.py)
y la DB de producción (data/ofertas.sqlite, 2026-09-03).

## D. Decisión de producto: la republicación ES un evento nuevo

Regla del usuario, pinzada:

1. **Una republicación (mismo puesto que vuelve a publicarse, incluso cross-plataforma)
   SÍ es candidata a publicarse en el canal.** La empresa reabrió la búsqueda → información
   fresca para los suscriptores. NO se bloquea por similitud título+empresa.
2. **Lo único que se evita: el MISMO registro (mismo group_id) notificándose dos veces
   sin causa.** El dedup del canal es SOLO por group_id vía `notified_channel_at` — cada
   fila nueva del pool puede postear.
3. **Interacción con el dedup del pool** (evaluado y recomendado en §2.1): si el reposte
   FUSIONA con un group_id existente → NO re-notifica; si dedup crea fila nueva → SÍ publica.
4. **La ventana de 2 semanas sigue aplicando** a republicaciones: la fecha que gana es la
   de la FILA (ver §1.2/§2.3): plataforma re-datou fresco → entra; fecha canónica vieja
   → queda fuera. Criterio: para el feed lo que importa es "¿es relevante AHORA?".

Consecuencias de la decisión:

- **H9 de la review queda resuelto por decisión de producto**, no con dedup difuso: el
  reposteo cross-source con nuevo group_id se publica. El spam potencial se controla con
  los gates que ya existen (market score ≥ umbral, edad ≤14d) y se MONITOREA (informativo,
  no bloqueante) en `/channel` (§4.2).
- **`dedup.py` NO se modifica.** Se verificó su comportamiento actual (§2.1): los merges
  conservan `first_seen`/`date_posted` y `upsert()` no re-notifica nada — la semántica
  pedida ya emerge de la mecánica existente.
- **Trade-off aceptado:** el mismo puesto puede aparecer en el canal dos veces (CB hoy,
  LinkedIn mañana, filas distintas). Es el costo deliberado de no bloquear republicaciones;
  `/channel` reporta el conteo de "posibles republicaciones" para poder revertir la
  decisión con datos si el ruido supera lo tolerable.

## 0. Hechos medidos (fundamento del diseño)

- `date_posted` en la DB YA está normalizado a `YYYY-MM-DD` en todas las fuentes (los
  scrapers convierten sus formatos nativos al insertar; 607/607 filas cumplen el patrón).
  La normalización robusta (`normalize_date`) igual se implementa como defensa: LinkedIn
  trae `datetime="..."` ISO, Laborum `fechaPublicacion` DD-MM-YYYY, Jooble "Publicado el
  21 de Jul, 2026", Computrabajo "Hace X horas/días" (relativo, **resetea en cada
  reposteo**), Indeed `datePublished` GraphQL, AIRA `publication_days`, Accenture
  `postedDateText`.
- Cobertura real: 502/578 activas con fecha, **76 sin fecha** (69 Indeed — `datePublished`
  viene vacío — y 7 Computrabajo). Distribución de edad (activas): hoy 206 · 1-2d 163 ·
  3-6d 49 · 7-13d 19 · **14+d 65** · sin 76.
- `upsert()` NO toca `date_posted` ni `first_seen` al fusionar (solo last_seen,
  occurrences, campos vacíos como salary/modality) → **un merge conserva la fecha
  original aunque el reposte traiga fecha más fresca**. Medido hoy: 0 filas con
  date_posted > first_seen.
- Comportamiento verificado de `dedup.py find_duplicate` (detalle y evaluación en §2.1):
  L1 por URL escanea TODAS las filas (incluye expiradas); L2/L3 por título+empresa
  escanean solo `active=1`.
- `cli.py cmd_run` ya corre IA complementaria de TODAS las nuevas dentro del mismo
  barrido (mitiga H1) y luego Anillo A (`enrich_pending(max_n=8)`) y `rescore_all`.
  El publish va al FINAL de ese pipeline (§4).
- IA narrativa reutilizable: `market._ia_call(cfg, prompt, temperature)`.
- `market_score` y `notified_channel_at` NO existen aún (migración pendiente).

## 1. Esquema de antigüedad robusto

### 1.1 Columna canónica: `date_canonical`

Nueva columna en `ofertas` (migración patrón `ai_idiomas`):

```sql
ALTER TABLE ofertas ADD COLUMN date_canonical TEXT DEFAULT '';
UPDATE ofertas SET date_canonical = COALESCE(NULLIF(date_posted,''), substr(first_seen,1,10));
```

Mantenimiento (una sola función, `channel.canonical_date(row, now=None) -> str`, pura):

```
d  = normalize_date(date_posted)          # YYYY-MM-DD o ''
fs = first_seen[:10]
if not d:        return fs                # sin fecha del source → first_seen
if d > fs:       return fs                # clamp defensivo: nunca más fresca que first_seen
return d                                  # date_posted (es ≤ first_seen: la honesta)
```

Regla = `min(date_posted, first_seen)`. **Nota v3:** se ELIMINÓ el clamp "zombi" (>90d →
first_seen) de la versión anterior: bajo la decisión de producto, una fuente que mantiene
una fecha vieja está diciendo que el posting es viejo → debe quedarse fuera de la ventana,
no rejuvenecerse artificialmente. Se escribe en: `upsert()` al INSERT (nueva fila),
`enrich_pending` cuando Anillo A completa `date_posted`, y una pasada de `rescore_all`
(UPDATE barato, 1 campo). Motivo de persistirla: los digests filtran por SQL
(`date_canonical >= date('now','-14 days')`) y el market score la usa como entrada.

### 1.2 Jerarquía de confianza por fuente

| Fuente | date_posted proviene de | Confianza | Tratamiento |
|---|---|---|---|
| Laborum | `fechaPublicacion` oficial (DD-MM-YYYY→ISO) | ALTA | confiar |
| Accenture | `postedDateText` oficial | ALTA | confiar |
| Jooble | "Publicado el X de Mes" parseado; **si falta → `now.date()`** (código actual) | MEDIA-ALTA | confiar; el fallback-a-hoy se mitiga con el clamp §1.1 |
| JSON-LD (Anillo A) | `datePosted` oficial de la ficha | ALTA | confiar (solo llena si estaba vacío) |
| AIRA | `publication_days` (días desde publicación, ATS del empleador) | MEDIA-ALTA | confiar |
| Glassdoor | `age` (días) → now−delta | MEDIA | min con first_seen |
| Computrabajo | regex "Hace X horas/días" → now−delta; **resetea en cada reposteo** | BAJA | ver §2.3: solo influye si crea fila NUEVA |
| Indeed | `datePublished` (76 activas lo traen vacío) | N/A | **fallback a first_seen**; el filtro GraphQL `date: 168h` garantiza ≤7d al primer scrape → first_seen es proxy honesto |

Regla de oro: **cuando hay duda gana la fecha MÁS VIEJA verificable** (`min` con
first_seen). El clamp mata el "+7 pts de frescura" inflado por repost en fila nueva (H12)
solo cuando la fecha reclamada supera a first_seen; el caso normal (CB "hace 1 hora" en
fila nueva el mismo día) queda tal cual — es exactamente el "re-datou fresco" que la
decisión de producto quiere que ENTRE.

### 1.3 `date_posted` vacío (76 hoy)

- **Usar `first_seen`** (no excluir). Justificación: para Indeed, first_seen ≤ fecha real
  de publicación (filtro 168h) → cota superior honesta de staleness; excluir 76 ofertas
  —toda la fuente Indeed— sacrifica 13% del pool por ~0 riesgo.
- El origen se deduce sin columna extra: `date_posted == '' and date_canonical ==
  first_seen[:10]` → edad por first_seen.

### 1.4 `valid_through`

NO participa en la antigüedad. Rol exclusivo: expiración (`active=0` vía `valid_through`
pasado o `_cb_expired` de Anillo A, ya implementado). Una oferta con `valid_through`
vencido está fuera del canal por `active=1` en el gate; sin reglas nuevas.

### 1.5 Ventana configurable

- `CHANNEL_MAX_AGE_DAYS=14` (default). Gate: `date_canonical >= date('now','-14 days')`
  evaluado **al momento del publish**, no al indexar.
- Anti-backfill por deriva del score (H3): una oferta vieja que cruza el umbral días
  después (IA completa salario) se bloquea porque su `date_canonical` ya supera 14 días.
  Imposible por construcción publicar algo de >14d.
- Knob opcional estricto (default OFF): `CHANNEL_MAX_FIRST_SEEN_HOURS=0` → si se setea
  (ej. 72), exige además `first_seen` reciente: canal estrictamente "primeras 72h de vida
  en el pool". Documentado, no activado: perdería ofertas que completan IA al 2º-3º día
  y siguen frescas.

### 1.6 ¿Qué fecha gana en republicaciones? (decisión usuario #4)

Criterio del feed: **"¿es relevante AHORA para un dev que ve el canal?"** → lo que cuenta
es cómo la plataforma presenta el posting HOY, no lo que sabíamos hace semanas.

| Caso | Fecha que gana | Por qué |
|---|---|---|
| Reposte → **fila NUEVA** (nuevo group_id) | El `date_posted` de ESA fila (la re-datación de la plataforma), clampeado por su `first_seen` (mismo día en la práctica) | Es la única fecha asociable a ese registro y codifica "la plataforma lo muestra como publicado el día X". Si CB dice "Hace 1 hora" → entra (búsqueda activa). Si Jooble muestra "Publicado el 21 de Jul" sin re-datación → fecha vieja → queda fuera. Coherente: la plataforma decide qué tan fresco lo presenta. |
| Reposte **FUSIONADO** a fila activa | La original (upsert no toca date_posted) | Irrelevante para el canal: el gid ya tiene `notified_channel_at` → no re-notifica (§2). Mantener la fecha estable evita inflar frescura del pool con reposts de CB (ofertas zombi que se ven eternamente frescas en el ancla del grupo). |
| Reposte fusionado a fila **expirada** por MISMO url | La original (merge L1 de dedup escanea todas las filas) | El merge reviva la fila vieja; no re-notifica (ya estaba notificada). Edge case documentado: si "el puesto reabrió con el mismo URL" se vuelve frecuente y molesto, v4 puede añadir "revive tras ≥7d inactive → reset notified_channel_at" como knob separado. Hoy NO (simple, predecible). |
| Fila nueva con date_posted vacío (Indeed) | first_seen | §1.3 |

## 2. Anti-repetición: SOLO por group_id

### 2.1 Verificación y evaluación del dedup del pool (decisión usuario #3)

Comportamiento verificado de `dedup.py find_duplicate(job) -> group_id | None`:

- **L1 (URL):** `url_key(url)` → busca en `ofertas` **sin filtro active** → un reposte con
  el MISMO url fusiona incluso con una fila expirada (revive el gid).
- **L2 (exacto):** `norm_title(title)` igual + `companies_match` strong o weak (acepta
  genéricas tipo "Confidencial") → merge, pero **solo sobre `active=1`**.
- **L3 (difuso):** `similar()` (Jaccard ≥ .55 o secuencia ≥ .86) + empresa no-distinta →
  merge, solo `active=1`.

Evaluación para un feed público — recomendación (adoptada):

1. **Fusionado a fila activa YA notificada → NO re-notificar.** El suscriptor ya vio ese
   puesto; republicar el registro idéntico sería spam. La fila sigue visible en el ancla
   del grupo. (Implementación: nada que hacer — `notified_channel_at` permanece seteada.)
2. **Fusionado a fila activa NUNCA notificada que ahora cruza el gate → SÍ notificar.**
   Ejemplo real: fila sin datos IA quedó bajo 70; un reposte trae `salary` (upsert llena
   campos vacíos) o la IA nocturna la completa → cruza 70 → primera notificación. Hay
   CAUSA (el registro ahora cumple los criterios por primera vez). El gate
   `notified_channel_at = ''` ya produce exactamente esto.
3. **Fusionado a fila expirada por mismo URL → NO re-notificar** (merge L1 conserva gid y
   `notified_channel_at`). Edge case aceptado (§1.6).
4. **Dedup NO fusiona (título/empresa variantes cross-plataforma, o renacida tras expirar
   con URL distinto) → fila NUEVA → SÍ notificar**, si pasa edad + score. Es la
   republicanación como evento nuevo que la decisión de producto exige.

Conclusión: **`dedup.py` queda intacto.** La semántica deseada emerge de la mecánica
existente + el gate por `notified_channel_at`. La extensión "escanear expiradas en L2/L3"
de la versión anterior del diseño se DESCARTA: forzaría merges de renacimientos y
suprimiría republicaciones que ahora sí queremos publicar.

### 2.2 Gate de no-repetición del canal (lo único que bloquea)

- `notified_channel_at = ''` en la query de candidatas (spec v2, se mantiene).
- Se setea **solo si Telegram aceptó el mensaje** (`ok=true`) → crash a mitad de corrida
  reintenta sin duplicar.
- Doble publicación del mismo barrido es imposible por diseño: `publish_channel(cfg, conn)`
  se invoca en UN solo punto (final de `cmd_run`, H5/H6).

### 2.3 Memoria del canal: tabla `channel_posts` (slim)

Ya no guarda claves de título/empresa (no bloquean nada). Su rol: idempotencia de digests,
log de observabilidad (H10) y `message_id` para edición futura (H16):

```sql
CREATE TABLE IF NOT EXISTS channel_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER,             -- para editar/borrar a futuro (H16)
  group_id TEXT DEFAULT '',       -- kind='offer': gid publicado
  kind TEXT NOT NULL,             -- 'offer' | 'daily' | 'weekly-remoto' | 'weekly-salario' | 'tendencias' | 'test'
  bucket TEXT DEFAULT '',         -- '2026-W36' / '2026-09-03' / '2026-09' (idempotencia digests)
  body_hash TEXT DEFAULT '',      -- sha1 del texto (guard anti-digest-idéntico)
  posted_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cp_kind_bucket ON channel_posts(kind, bucket);
```

### 2.4 Guard anti-digest-idéntico

- Idempotencia por corrida: `INSERT OR IGNORE` sobre `(kind, bucket)` único → dos corridas
  el mismo día/semana/mes no duplican el digest.
- `body_hash = sha1(texto)`: si coincide con el último post del mismo kind (aunque cambie
  el bucket) → skip con log. Cubre "pool quieto → digest igual al de ayer".
- MONITOREO informativo de republicaciones (no bloquea, decisión §D): `/channel` muestra
  "posibles republicaciones (14d): N" = pares de posts `kind='offer'` de los últimos 14d
  cuyo `norm_title|norm_company` coincide. Solo lectura, para recalibrar la decisión si
  hiciera falta.

## 3. Catálogo de formatos de post del canal

Volumen esperado: 0-5 ofertas individuales/barrido (medido: 18/578 pasarían 70 hoy; crecerá
con cobertura IA). Los digests agregan valor sin inflar volumen.

| # | Formato | Trigger | Datos | Anti-repetición |
|---|---|---|---|---|
| A | **Oferta individual** | cada barrido (post-IA), al final de `cmd_run` (una sola vía, H5/H6) | SQL sobre `ofertas` | `notified_channel_at` + edad ≤14d |
| B | **Digest diario "top del día"** | 1×/día, `CHANNEL_DIGEST_DAILY_HOUR_UTC=21` (post-barrido 20:00, cron interno del daemon) | SQL | bucket diario + `body_hash` |
| C | **Digest semanal: mejor remoto por seniority** | 1×/semana, `CHANNEL_DIGEST_WEEKLY_DAY_UTC=6` (domingo) `HOUR=13` | SQL | bucket semanal + variedad (§3-C) |
| D | **Digest semanal: ranking salarial** | mismo tick que C (posts consecutivos) | SQL + parser salarial en Python | bucket semanal |
| E | **Mini-análisis de tendencias (mensual)** | 1×/mes, día 1 `13:00 UTC` | SQL agregado (30d) | bucket mensual; IA opcional |

### A — Oferta individual (formato v2 + H11)

```
🎯 [86] Backend Semi-Senior Java/Spring Boot
🏢 Consultora DT · 📍 híbrido Stgo
💰 $2.1M líquido
🧰 Java · Spring Boot
🗣 EN! · 📅 hace 2 días · 🌐 Laborum
🔗 https://www.laborum.cl/empleos/1118378959
```

La edad mostrada sale de `date_canonical` (§1) — una republicación re-datada muestra su
edad fresca, coherente con lo que la plataforma presenta.

Fuente de datos (query del gate):

```sql
SELECT * FROM ofertas
WHERE active=1 AND market_score >= :min_score
  AND notified_channel_at = ''
  AND date_canonical >= date('now', '-' || :max_age || ' days')
  AND (:require_dev = 0 OR rol_categoria IN ('Full Stack','Backend','Frontend','Data',
        'Mobile','AI/ML','Tech Lead','DevOps/Cloud','QA','Software','Seguridad'))
ORDER BY market_score DESC LIMIT :max_posts;
```

(H4: gate dev con `rol_categoria`; fallback regex `nontech_titles` cuando está vacío —
`enrich.py` IA_SCHEMA ya produce esas categorías.)

### B — Digest diario "top del día" (determinístico)

- Query: mismas condiciones de gate A **pero** umbral relajado
  `CHANNEL_DIGEST_MIN_SCORE=60` y excluyendo lo publicado como oferta individual en las
  últimas 24h (`notified_channel_at < now-24h OR = ''`); top `CHANNEL_DIGEST_MAX=8` por
  `market_score DESC`.
- Formato: tabla compacta existente (`notify.table_block`) + encabezado
  `📊 Top del día · 8 ofertas · 03 Sep`; 1 mensaje.
- Anti-repetición: bucket `YYYY-MM-DD` + `body_hash` (§2.4). Sin candidatas nuevas →
  **no se envía nada** (silencio honesto).

### C — Digest semanal remoto por seniority (determinístico)

- Query: `modality LIKE '%remot%' OR remote_official=1`, edad ≤14d, `seniority_real != ''`,
  orden `market_score DESC`, **top 3 por seniority** (junior/semi/senior/lead). Regla de
  variedad: excluir `group_id` ya destacado en digests de las últimas 4 semanas
  (`channel_posts.kind IN ('daily','weekly-remoto','weekly-salario')`) — es selección de
  contenido del digest, NO bloqueo de republicaciones individuales.
- Formato: una sección por seniority con títulos + 💵 + 🔗; secciones vacías omitidas;
  digest sin secciones → no se envía.

### D — Digest semanal ranking salarial (determinístico)

- Fuente: candidatas edad ≤14d con `salary != ''`; ranking en Python con
  `_salary_to_clp_monthly` (parser único, H13) sobre top 40 por market_score; top 5 DESC.
- Formato: `💰 Top salarios de la semana` + 5 líneas `1. $3.1M — Título (Empresa) 🔗`.
- Una oferta puede aparecer en C y D (distinto ángulo, aceptado).

### E — Mini-análisis de tendencias (mensual; única pieza con IA opcional)

- Fuente (SQL puro, 30 días): (1) `GROUP BY company` → top 5 publicadoras; (2) explode de
  `techs` (sep `;`) cruzado con `seniority_real` → top 5 techs por seniority; (3)
  distribución `rol_categoria`.
- Formato:
  ```
  📈 Mercado tech · últimos 30 días
  🏢 Más activas: Banco X (14) · Consultora Y (11) · ...
  🧰 Data: Python 32% · SQL 28% · AWS 21%
  🧰 Senior: Scala 35% · NiFi 24% · AWS 22%
  ✅ 61% del pool es dev · 24% declara salario
  ```
- IA opcional: 1 llamada `_ia_call` (temperature 0.3) para 2-3 bullets de interpretación;
  **fallback determinístico sin IA** (try/except; el bloque numérico siempre sale igual).
- Anti-repetición: bucket `YYYY-MM` + `body_hash` (cifras distintas si el pool cambió).

### Presupuesto de rate limits

Peor caso por barrido: 10 ofertas + 2s sleep ≈ 20s ≪ 20 msg/min. Digests: +1 diario, +2
semanales, +1 mensual. Holgado (el riesgo real es canal sub-alimentado, H1/H2).

## 4. Plan de implementación

### 4.1 Nuevo módulo `jobhunt/channel.py` (todo el dominio canal en un archivo)

| Función | Firmas | Notas |
|---|---|---|
| `normalize_date` | `(raw: str, now=None) -> str` | ISO datetime, YYYY-MM-DD, DD-MM-YYYY, "21 de Jul, 2026", "hace X horas/días", epoch → `YYYY-MM-DD`/`''`. Defensiva: la DB ya está normalizada. |
| `canonical_date` | `(row: dict, now=None) -> str` | reglas §1.1 (`min(date_posted, first_seen)` + clamp); pura |
| `age_days` | `(row: dict, now=None) -> int` | `now - canonical_date`; negativa→0 |
| `is_dev` | `(rol_categoria: str, title: str, cfg) -> bool` | set dev + `relevance.nontech_titles` fallback (H4) |
| `select_channel_offers` | `(conn, cfg) -> list[dict]` | query §3-A |
| `render_offer_post` | `(offer: dict) -> str` | formato §3-A, `esc()` de notify, HTML-safe |
| `publish_channel` | `(cfg, conn, dry_run=False) -> dict` | gates + post + UPDATE `notified_channel_at` (solo si ok) + INSERT `channel_posts(kind='offer')` + stats `{candidates, posted, skipped_age, skipped_score, skipped_notified}` (H10) |
| `render_daily_digest` / `publish_daily_digest` | `(cfg, conn, dry_run=False)` | §3-B |
| `render_weekly_digests` / `publish_weekly_digests` | `(cfg, conn, dry_run=False)` | §3-C+D, 1 tick, 2 mensajes |
| `publish_tendencias` | `(cfg, conn, dry_run=False)` | §3-E |
| `channel_status` | `(conn, cfg) -> str` | última publicación, candidatas en cola, distribución market_score, **posibles republicaciones 14d (informativo)**, umbral vigente (H10/H15) |

Envío por `bot._tg_api` (reusa allowlist + retries 403/429). `dry_run=True` renderiza y
loguea sin llamar a la API (reemplaza la "oferta ficticia en DB real" del despliegue, H8).

### 4.2 Cambios en módulos existentes

- `db.py`: migración `market_score INTEGER DEFAULT 0`, `notified_channel_at TEXT DEFAULT
  ''`, `date_canonical TEXT DEFAULT ''` (patrón `ai_idiomas` en `init_db`) + tabla
  `channel_posts` + índices; `rescore_all` escribe `market_score` y refresca
  `date_canonical`; backup del .sqlite antes de migrar (H8). **`dedup.py` NO se toca** (§2.1).
- `scoring.py`: `compute_market_score(offer, now=None)` según spec v2 con frescura basada
  en `canonical_date` (H12) y `max(0, subtotal-10)` explícito (H14). Parser salarial:
  `_salary_to_clp_monthly` del mismo módulo (H13).
- `config.py`: dataclass `ChannelCfg` + parsing .env (§4.3); validación:
  `TELEGRAM_CHANNEL_ID` no numérico → canal OFF con log, no crash (H7); el canal ya está
  en `TELEGRAM_ALLOWED_CHATS` (verificado).
- `cli.py`: al final de `cmd_run` (después de rescore_all, único punto de publish,
  H5/H6): `publish_channel` + digests si toca hora/día; `phase()`s para progreso. Flag
  `python -m jobhunt channel [--dry-run] [--digest]`.
- `bot.py`: tick `_digests_maybe(cfg, state)` en el daemon loop (patrón
  `_ia_sweep_maybe`: hora agendada + key por día/semana/mes en state); comando admin
  `/channel` → `channel_status` al grupo interactivo (no al canal); alerta H10b: si
  `publish_channel` reporta `posted=0` en `CHANNEL_SILENCE_SWEEPS=6` barridos
  consecutivos → warning al admin.

### 4.3 `.env` completo (bloque nuevo)

```bash
# ---------- CANAL (broadcast) ----------
TELEGRAM_CHANNEL_ID=-1004495706494
CHANNEL_ENABLED=true
CHANNEL_MIN_SCORE=70
CHANNEL_MAX_POSTS=10
CHANNEL_SLEEP_S=2.0
# --- antigüedad ---
CHANNEL_MAX_AGE_DAYS=14              # ventana canónica (date_canonical)
CHANNEL_MAX_FIRST_SEEN_HOURS=0      # 0=OFF; ej 72 = solo primeras 72h en el pool
# --- anti-repetición (SOLO por group_id — decisión 2026-09-03) ---
CHANNEL_DIGEST_HASH_GUARD=true       # skip si body_hash == último del mismo kind
# --- gate dev ---
CHANNEL_REQUIRE_DEV=true             # rol_categoria ∈ set dev (H4)
# --- digests ---
CHANNEL_DIGEST_DAILY=true
CHANNEL_DIGEST_DAILY_HOUR_UTC=21
CHANNEL_DIGEST_WEEKLY=true
CHANNEL_DIGEST_WEEKLY_DAY_UTC=6      # 0=lunes … 6=domingo
CHANNEL_DIGEST_WEEKLY_HOUR_UTC=13
CHANNEL_DIGEST_TENDENCIAS=true       # mensual, día 1 13:00 UTC
CHANNEL_DIGEST_MIN_SCORE=60
CHANNEL_DIGEST_MAX=8
# --- observabilidad ---
CHANNEL_SILENCE_SWEEPS=6             # alerta admin si 6 barridos sin publicar
```

### 4.4 Orden de implementación y qué probar

1. **Migraciones + fechas** (`db.py`, `channel.normalize_date/canonical_date`).
   Probar: pytest de `normalize_date` (6 formatos de fuente) y `canonical_date`
   (vacío→first_seen; futuro→clamp a first_seen; normal→min; republicación re-datada en
   fila nueva→fecha fresca; merge conserva fecha original).
2. **Semántica merge vs fila nueva** (tests de integración sobre `upsert` + `notified_channel_at`):
   reposte mismo URL de fila notificada → merge → skip; reposte cross-fuente con título
   variante → fila nueva → publica (si edad+score); fila expirada que renace con URL
   distinto → fila nueva → publica; fila nunca notificada que cruza gate tras merge →
   publica (primera notificación).
3. **Gate + publish individual** (`publish_channel`, `render_offer_post`). Probar con mock
   de `_tg_api`: orden DESC, tope max_posts, skip por edad/score/notificado, no-op sin
   chat_id, `notified_channel_at` solo si ok=true, dry-run no llama API, caso "recién
   insertada sin IA no pasa 70" documentado (H1).
4. **Digests** (B/C/D determinísticos). Probar: bucket impide doble envío, `body_hash`
   impide digest idéntico, secciones vacías omitidas, sin candidatas → cero mensajes.
5. **Daemon + observabilidad** (`_digests_maybe`, `/channel`, alerta silencio). Probar:
   key por hora/día dispara una sola vez; alerta solo tras N barridos; conteo informativo
   de republicaciones correcto.
6. **Despliegue**: backup DB → migrate → `python -m jobhunt channel --dry-run` (revisar
   candidatos/skips en log) → restart `jobhunt.service` → observar barrido 20:00 UTC →
   ajustar `CHANNEL_MIN_SCORE`/`CHANNEL_MAX_AGE_DAYS` según volumen real; distribución de
   `market_score` visible en `/channel` para recalibración mensual (H15).

## 5. Interacción con la review QA (mapa)

| Hallazgo | Resolución en este diseño |
|---|---|
| H1 (gate sin enriquecer) | publish al final de `cmd_run` post-IA-complementaria; test documenta el caso límite |
| H3 (backfill por deriva) | gate `date_canonical >= now-14d` evaluado al publicar; imposible publicar >14d |
| H4 (no-dev al canal) | `CHANNEL_REQUIRE_DEV` con rol_categoria + fallback `nontech_titles` |
| H5 (doble publish) | `publish_channel(cfg, conn)` único punto: final de `cmd_run` |
| H6 (/search manual publica) | documentado: sí, misma vía; es deseable |
| H7 (allowlist) | validación no-numérico → OFF con log; chat ya en allowlist |
| H8 (oferta ficticia) | `--dry-run` + backup pre-migración |
| H9 (dedup cross-source) | **resuelto por decisión de producto**: republicación = evento nuevo; gid fusionado ya notificado no re-publica; monitoreo informativo en `/channel` |
| H10 (observabilidad) | stats por barrido, `/channel`, alerta silencio |
| H11 (fuente/inglés en post) | líneas `🌐 fuente` y `🗣 EN!` en el formato |
| H12 (frescura sin fallback) | `canonical_date` = min(date_posted, first_seen) con clamp |
| H13 (parser inexistente) | `_salary_to_clp_monthly` como parser único |
| H14 (anomalías tabla) | `max(0, subtotal−10)` explícito; asimetría documentada en spec v2 |
| H15 (recalibración) | distribución market_score en `/channel` |
| H16 (links muertos) | nota en descripción del canal; message_id ya persistido para v4 |
| H2 (cobertura IA) | fuera de alcance; el volumen se observa con `/channel` y se recalibra el umbral |

## 6. Fuera de alcance (v3)

- Edición/borrado de posts cuando la oferta expira (message_id persistido; deuda H16).
- "Revive con mismo URL tras ≥7d inactive → reset notified_channel_at" (knob futuro,
  §1.6/§2.1 — hoy el merge no re-notifica).
- Volver a bloquear por similitud título+empresa si el ruido cross-plataforma supera lo
  tolerable (la decisión §D es reversible con datos de `/channel`).
- Segundo canal (junior, remoto-only): trivial con `CHANNEL2_*`.
- Backfill histórico al canal: NO (mantenido de spec v2).
- Fit score personal y grupo interactivo: intactos.