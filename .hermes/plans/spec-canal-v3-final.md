# Spec v3 CONSOLIDADA — Modo canal broadcast + market score

**Estado:** consolida spec v2 + revisión arquitectura (P0-P2) + review QA (H1-H16)
+ diseño antigüedad/anti-repetición/formatos (v3). Este documento es el único
contrato de implementación; las 3 piezas anteriores quedan como referencia.

**Decisiones de producto pinzadas (usuario, 2026-09-03):**
1. Republicación = evento nuevo (NO dedup por similitud título+empresa; solo
   group_id vía notified_channel_at). Trade-off aceptado: mismo puesto puede
   aparecer 2× si llega por filas distintas; /channel reporta el conteo para
   poder revertir con datos.
2. Ventana de antigüedad: 14 días (configurable), fecha canónica robusta por
   fuente (ver §2).
3. Canal = broadcast puro, sin paginación/botones. Grupo interactivo intacto.
4. Todas las ofertas nuevas pasan por IA al indexar (ya implementado).
5. Canal requiere ofertas DEV (rol_categoria) — no COBOL/guardias/pricing.

## 1. Market score (0-100, objetivo, sin perfil personal)

Función pura: `compute_market_score(offer, now=None) -> int` en scoring.py.
Corre en la MISMA pasada de rescore_all que el fit score (dual-write).

### 1.1 Componentes

| Componente | Pts máx | Regla exacta (incluye bordes) |
|---|---|---|
| Salario | 40 | CLP mensual: ≥2,700,000 → 40 · [1,900,000, 2,700,000) → 30 · [1,300,000, 1,900,000) → 15 · <1,300,000 → 5 · sin salario o no-parseable → 5 |
| Modalidad | 20 | remoto=20 · híbrido=10 · presencial=5 · vacía=8 (castiga incertidumbre menos que presencial — asimetría documentada) |
| Transparencia | 15 | empresa visible (no vacía, no genérica) = 8 · contrato o jornada declarados = 4 · descripción ≥400 chars = 3 |
| Stack demandado | 15 | +2.5 por tech del set MARKET_TECHS presente en `techs` (col. con abreviaturas) o título (con word boundaries), tope 15 |
| Frescura | 10 | <48h=10 · ≤7d=7 · ≤14d=5 · >14d=3 · sin fecha → edad por first_seen (cota honesta: Indeed filtro 168h) |

### 1.2 Descuentos (capados)

- staffing detectado (recalcular en vivo con `_staffing(job)` — la columna DB
  está siempre 0): `subtotal = max(0, subtotal - 10)`
- company vacía o genérica ("Importante empresa del sector") → −5 dentro de
  transparencia (subtotal clampado a ≥0; el resto de componentes intacto)
- Score final: `max(0, total)` — nunca negativo.

### 1.3 MARKET_TECHS con mapa de abreviaturas (P0 resuelto)

La columna techs guarda abreviaturas (`Py;TS;K8s;NiFi`). Matching:
- En `techs`: set de abreviaturas canónicas (Py, Java, TS, JS, React, Angular,
  Node, AWS, GCP, Azure, K8s, Docker, NiFi, SQL, Postgres, Mongo, Go, .NET,
  Scala, Spring, CI/CD, FastAPI, Django, Redis, Kafka, TF, Terraform) — +2.5
  por match directo.
- En `title`: solo palabras completas con `\b` (evita `go`⊂"investigación",
  `java`⊂"javascript"): `python|java\b|scala|typescript|kubernetes|k8s|docker|
  nifi|react|angular|spring|\.net|aws|golang|node|postgres|graphql`.
- Set dev en código (constante), test fixture con cada abreviatura del pool.

### 1.4 Parser de salario ÚNICO (P0 resuelto)

`_salary_to_clp_monthly(s) -> int | None` en scoring.py (el existente), con
CASOS DE PRUEBA por cada formato real observado:
- `$ 2.500.000,00 (Mensual)` → 2,500,000
- `CLP 2578680` → 2,578,680 (BUG ×950 actual corregido: 7 dígitos plano tras
  "CLP" NO es USD — es el formato que escribe la IA en enrich.py:363)
- `CLP 15000` → None (horario/no plausible mensual; banda 300k-20M)
- `USD 4000` / `4000` (heurística USD existente) → ×950 ≈ 3,800,000
- `''`/None → None
Toda la spec usa ESTE parser; prohibido referenciar `_parse_salary_clp`
(no existe).

### 1.5 Calibración reproducible (P1 resuelto)

Distribución real simulada sobre 578 activas: ≥70 → 16-18 (2.8-3.1%), 60-79 →
29, <40 → 438. NOTA: 70 es ~P97 del pool, NO "P60" (error de v2 corregido).
El script de simulación pasa a fixture del test suite
(`tests/test_market_score_calibration.py`) con 3 ofertas reales etiquetadas
(23people ≥80 · TINET 55-70 · guardia <30). Volumen esperado del canal: ~2-6
posts/día hoy, crece con cobertura IA; recalibración mensual vía /channel.

## 2. Antigüedad multiplataforma (P0 — gate de edad)

Ver detalle completo en design-canal-antirep-edad-formatos.md §1-2 (438 líneas,
verificado contra DB real). Resumen contractual:

- **Columna `date_canonical`** = `min(date_posted, first_seen[:10])` con clamp
  (sin fecha → first_seen; nunca más fresca que first_seen). Escrita en
  upsert (INSERT), enrich_pending (Anillo A) y rescore_all.
- **Jerarquía de confianza por fuente:** Laborum/Accenture/JSON-LD/AIRA alta ·
  Jooble media-alta · Glassdoor media · **Computrabajo baja** (fecha relativa
  "Hace X horas" resetea en cada reposteo — solo influye si crea fila NUEVA) ·
  **Indeed fallback a first_seen** (76 activas sin datePublished).
- **Gate de edad al publicar:** `date_canonical >= date('now','-14 days')`
  con `CHANNEL_MAX_AGE_DAYS=14`. Mata el backfill por deriva de score (H3):
  oferta vieja que cruza umbral tras enriquecerse → bloqueada por edad.
- **Republicaciones (decisión producto):** fila nueva → fecha re-datación
  clampeada (plataforma re-datada fresco = ENTRA); merge → fecha original y NO
  re-notifica (gid ya en notified_channel_at); merge a fila expirada por mismo
  URL → revive, NO re-notifica (edge documentado, knob futuro).
- Knob estricto opcional OFF: `CHANNEL_MAX_FIRST_SEEN_HOURS=0` (72 = solo
  primeras 72h en pool; no activado para no perder ofertas que completan IA
  al 2º-3º día).

## 3. Anti-repetición (decisión producto)

- **Único gate: `notified_channel_at = ''`** (por group_id). Se setea SOLO si
  Telegram aceptó (ok=true) → crash a mitad = reintenta sin duplicar.
- `dedup.py` NO se toca (comportamiento verificado: merge conserva fecha;
  L1-URL escanea expiradas; L2/L3 solo activas).
- Semántica completa: fusionado-a-activo-notificado NO · fusionado-a-activo-
  nunca-notificado-que-cruza SÍ (primera notificación con causa) · fusionado-a-
  expirado NO · fila nueva SÍ (si pasa edad + score + dev).
- Tabla `channel_posts(id, message_id, group_id, kind, bucket, body_hash,
  posted_at)` + índice único (kind, bucket): idempotencia de digests +
  observabilidad + message_id para edición futura.
- Guard anti-digest-idéntico: body_hash del mismo kind → skip.
- Monitoreo informativo (no bloqueante) de republicaciones 14d en /channel.

## 4. Gate del canal (query única de publish_channel)

```sql
SELECT * FROM ofertas
WHERE active=1 AND market_score >= :min_score
  AND notified_channel_at = ''
  AND date_canonical >= date('now', '-' || :max_age || ' days')
  AND (:require_dev = 0 OR rol_categoria IN ('Full Stack','Backend','Frontend',
      'Data','Mobile','AI/ML','Tech Lead','DevOps/Cloud','QA','Software','Seguridad'))
ORDER BY market_score DESC, first_seen DESC LIMIT :max_posts;
```

(`CHANNEL_REQUIRE_DEV=true` resuelve H4 — COBOL/análisis/guardias fuera.
Fallback regex nontech_titles cuando rol_categoria vacío. Tie-break
determinista por first_seen DESC — P2 resuelto.)

## 5. Formatos de post (5 tipos)

Ver detalle completo (trigger, SQL, formato, anti-repetición por bucket) en
design-canal-antirep-edad-formatos.md §3. Resumen:

| # | Formato | Trigger | Gate propio |
|---|---|---|---|
| A | Oferta individual | al final de cmd_run (ÚNICO punto publish — H5/H6) | market_score + edad + dev + not-yet-notified |
| B | Digest diario "top del día" (top 8, tabla) | 21:00 UTC diario | min_score 60 (relajado), bucket + body_hash |
| C | Semanal: mejor remoto por seniority (top 3 × jr/semi/sr/lead) | domingo 13:00 UTC | remoto + variedad (no repetir destacados 4 sem) |
| D | Semanal: ranking salarial (top 5) | mismo tick que C | salary != '' + parser único |
| E | Mensual: tendencias (top empresas, techs × seniority, % dev) | día 1, 13:00 UTC | SQL 30d; IA opcional 1 call con fallback determinístico |

Post individual (formato final):
```
🎯 [86] Backend Semi-Senior Java/Spring Boot
🏢 Consultora DT · 📍 híbrido Stgo
💰 $2.1M (líneas sin dato se omiten — sin "líquido" hardcodeado)
🧰 Java · Spring Boot
🗣 EN! · 📅 hace 2 días · 🌐 Laborum
🔗 https://...
```
Reutilizar `salary_tag`/`lang_tag`/`age_tag`/esc de notify.py. Enviado con
`disable_web_page_preview: True` (P2). Sin botones inline.

## 6. Implementación

### 6.1 Módulo nuevo `jobhunt/channel.py`
normalize_date · canonical_date · age_days · is_dev · select_channel_offers ·
render_offer_post · publish_channel(cfg, conn, dry_run=False) -> dict{stats} ·
render/publish_daily_digest · render/publish_weekly_digests ·
publish_tendencias · channel_status (H10). dry_run renderiza sin API
(reemplaza la oferta ficticia — H8; backup del .sqlite antes de migrar).

### 6.2 Cambios existentes
- db.py: migración market_score/notified_channel_at/date_canonical (patrón
  ai_idiomas) + channel_posts + índice. rescore_all: dual-write fit+market,
  refresca date_canonical, **try/except por fila** (un bug de market score NO
  tumba el rescore del fit — P1), versionado: criterios de market score al
  snapshot score_versions (nuevo campo criteria_market).
- scoring.py: compute_market_score + parser único + MARKET_TECHS.
- config.py: ChannelCfg (enabled, chat_id, min_score, max_posts, sleep_s,
  max_age_days, require_dev, digests...) — validación: chat_id no numérico →
  OFF con log (H7); canal ya está en allowlist (verificado).
- cli.py: al final de cmd_run → publish_channel + digests si toca hora. Flag
  CLI `python -m jobhunt channel [--dry-run] [--digest]`.
- bot.py: tick `_digests_maybe` (patrón _ia_sweep_maybe); comando admin
  `/channel` (status al GRUPO, no al canal); alerta silencio
  (posted=0 en CHANNEL_SILENCE_SWEEPS=6 barridos consecutivos → warning).

### 6.3 .env (completo en design §4.3)
TELEGRAM_CHANNEL_ID=-1004495706494 · CHANNEL_ENABLED=true ·
CHANNEL_MIN_SCORE=70 · CHANNEL_MAX_POSTS=10 · CHANNEL_SLEEP_S=2.0 ·
CHANNEL_MAX_AGE_DAYS=14 · CHANNEL_REQUIRE_DEV=true · digests (diario 21UTC,
semanal dom 13UTC, tendencias día 1) · CHANNEL_SILENCE_SWEEPS=6.

## 7. Tests (pytest — primera suite del repo)

1. normalize_date: 6 formatos de fuente. canonical_date: vacío→first_seen,
   futuro→clamp, normal→min, republicación re-datada en fila nueva→fresca,
   merge conserva original.
2. Semántica merge vs fila nueva (integración con upsert + notified_channel_at):
   los 4 casos del §3.
3. compute_market_score: casos de calibración reales (23people ≥80 · TINET
   55-70 · guardia <30 · COBOL $2.5M bloqueado por CHANNEL_REQUIRE_DEV en el
   gate, no en el score) + formatos salariales del §1.4 (incl. el bug ×950).
4. build_offer_post: omite líneas sin dato, escapa HTML, link, sin botones.
5. publish_channel con mock _tg_api: DESC, tope, skips, no-op sin chat_id,
   notified solo si ok, dry-run sin API, idempotencia (2ª llamada → 0).
6. Digests: bucket anti-doble, body_hash anti-idéntico, secciones vacías
   omitidas, silencio honesto.
7. Rescore dual-write + aislamiento de fallos (market bug no rompe fit).

## 8. Despliegue

1. Backup ofertas.sqlite → migrar (nuevas columnas/table).
2. `python -m jobhunt channel --dry-run` → revisar candidatos/skips en log.
3. commit + push + restart jobhunt.service.
4. Observar barrido 20:00 UTC → ajustar CHANNEL_MIN_SCORE / MAX_AGE_DAYS con
   datos reales vía /channel (distribución de market_score visible).

## 9. Fuera de alcance v3

- Edición/borrado de posts al expirar (message_id persistido para v4)
- Knob "revive tras ≥7d inactive" (documentado, no activado)
- Volver a bloquear republicaciones por similitud (reversible con datos de /channel)
- Backfill histórico · segundo canal (CHANNEL2_* trivial después)
- Fit score personal y grupo interactivo: INTACTOS
- Cobertura IA 340/día (fuera de alcance; se observa vía /channel y se
  recalibra el umbral)