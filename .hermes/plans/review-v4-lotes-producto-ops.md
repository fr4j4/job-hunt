# Review producto/operaciones — spec v4 (lotes IA paralela + publish incremental)

Revisor: perspectiva producto/operaciones. Verificado contra código real (cli.py `cmd_run`,
bot.py `_digests_maybe`/`_run_search_async`/daemon loop, channel.py `publish_channel`/`_GATE_SQL`,
enrich.py `run_ia_batch` nocturno, config.py, .env) y la DB de producción
(data/ofertas.sqlite: 252 activas post-reset, market_score=0 en todas, 32/252 con salary,
20/252 con modality, 48/252 con description>400).

## P0

### P0-1 — El tope anti-flood se reinterpreta silenciosamente: 10/barrido → hasta 10×7 lotes sin tope por barrido (§2.3, §2.7)
En v3/config.py `CHANNEL_MAX_POSTS=10` es "tope por barrido (anti-flood)". v4 llama
`publish_channel` una vez por lote → el tope estructural por barrido pasa a ser
7×10=70, y no existe ningún tope por barrido ni por día que lo limite. Las sobrantes
de un lote lleno quedan "para el publish del lote siguiente" (§2.3), así que tampoco
frenan el total.

El escenario peor es real y está vigente: pool vacío tras el reset de hoy → 252 nuevas,
todas con IA inline el mismo día → hasta 70 posts en ~95 min. La tasa real de paso es
desconocida: la calibración de v3 (≥70 ≈ 3% del pool, §1.5) se midió sobre un pool
enriquecido a lo largo de días con mezcla no-dev; 252 dev frescas todas IA-izadas es
otra población, y la spec no declara el volumen esperado por barrido (pendiente que la
review QA previa ya marcó como H2).

Fix barato: knob `CHANNEL_MAX_POSTS_PER_SWEEP` (o diario) que desactive el publish al
alcanzarse, o umbral dinámico (subir `min_score` cuando candidatas > N). Declarar el
volumen esperado por barrido en §2.3.

## P1

### P1-1 — Colisión del digest diario 21:00 UTC con un barrido largo/degradado (§2.1, §4.3; bot.py:1233)
`_digests_maybe` corre desde el loop del daemon y NO consulta `_op_busy`: puede
publicar el digest a mitad de un barrido por lotes. Con barrido sano (20:00 + 7 lotes
× ~5 min ≈ termina 20:40) no hay colisión; pero con degradación (breaker a 1 worker
≈ IA ×2, o lotes cortados por `IA_LOT_TIMEOUT_S`) el barrido cruza las 21:00 y el
digest lee un pool a medio procesar: las ofertas de los lotes restantes aún tienen
`market_score` viejo (0 en el pool reseteado) y las de los lotes ya publicados están
excluidas por `notified_channel_at=''` en `_GATE_SQL` → "Top del día" sale casi vacío
precisamente el día de más actividad. Mismo choque el domingo: sweep 12:00 vs weekly
13:00.

Fix: si `_op_busy()` o un flag de barrido-en-curso está activo, posponer el digest a
la siguiente pasada del loop (el guard por bucket ya evita doble envío).

### P1-2 — La red de seguridad "va a la cola nocturna" no toma a la mayoría: exige description>400 (§2.4, §7; enrich.py:385-389)
Las ofertas que quedan sin IA (fallo de `ia_extract` o lote cortado por timeout) "la
toma el batch nocturno". Pero el batch nocturno selecciona
`length(description)>400 AND ia_model=''` (enrich.py:385): hoy solo 48/252 activas
cumplen; las 164 con descripción corta/ausente quedarían permanentemente sin IA →
techo de score pre-IA (~49-53, mismo mecanismo del H1 de la review anterior) → nunca
pasan el gate 70 → invisibles al canal aunque sean dev y buenas.

La asimetría es interna del propio código: el flujo inline de v4 procesa las nuevas
SIN exigir descripción ("la IA trabaja con lo que haya", enrich.py:366) pero el
rescate nocturno no hereda ese criterio.

Fix: seleccionar la cola nocturna por `ia_model=''` a secas, o re-encolar los
pendientes de lotes cortados dentro del mismo barrido (fin de §2.1).

### P1-3 — Circuit breaker sub-especificado y demasiado sensible (§2.4)
- Sin semántica de reset definida: ¿"≥3 fallos consecutivos" se resetea con un éxito?
  Con 2 workers intercalando resultados, una intermitencia parcial (mezcla de 200 y
  429) puede no tripar nunca el breaker (los éxitos intercalados rompen la racha) — o,
  si no hay reset por éxito, un único fallo temprano degrada todo el barrido. La spec
  no define cuál de los dos comportamientos quiere.
- Coste de una intermitencia de 2 min en ollama-cloud: ~3-4 fallos seguidos → workers=1
  para el RESTO del barrido → las ~230 ofertas restantes a ~8s secuenciales ≈ +15-30
  min, que es exactamente la ventana 20:00→21:00 que dispara P1-1. Además no hay
  backoff ante 429: con `IA_RETRIES=1` cada 429 quema los 2 intentos del par y la
  respuesta correcta (esperar 30-60s) no está en la spec.
- Fix: reset del contador por éxito; umbral ≥5; ante 429, pausa con backoff antes de
  reducir workers; recuperación automática a N workers tras K éxitos consecutivos.

### P1-4 — Operabilidad: sin métricas por lote no se distingue "lento" de "degradado" (§2.7, §2.8; cli.py:150-151, bot.py:1293-1307)
- El mensaje vivo solo existe en `/search` manual; los barridos del cron llaman
  `_do_sweep(cfg)` sin `on_phase` (bot.py:1318): el operador no ve lotes, IA ni
  publish de los barridos programados — solo journalctl.
- `scan_log` (cli.py:150) guarda solo total_seen/new_count. No hay: duración por lote,
  fallos IA, trips del breaker, lotes cortados por timeout, publicadas por lote. Un
  barrido con breaker activo y 20 ofertas sin IA es indistinguible de uno sano desde
  `/channel` o `/stats`.
- Fix: resumen por barrido en scan_log (lotes N/M, ia_ok/ia_fail, timeout_cuts,
  published_total, breaker_trips) + exponerlo en `/channel status`; warning visible
  cuando `IA_LOT_TIMEOUT_S` corte un lote (hoy el corte no produce señal operativa).

## P2

### P2-1 — §2.3 sobreestima el fit score pre-IA como predictor del market score (§2.1, §2.3)
El orden de lotes usa el fit score calculado al indexar (cli.py:142), cuando 92% de
las ofertas aún no tiene salary (32/252) ni modality (20/252): el fit pre-IA es
base+stack de título, mientras el market score final está dominado por salary (40) +
modalidad (20), campos que llena la IA. El orden de lotes es un predictor débil: una
de 88 pts puede caer en el lote 7 y publicarse después que una de 72 del lote 1.
Mitigante: cada `publish_channel` ordena por `market_score DESC` y todo sale en <2h —
el daño es cosmético, pero la afirmación de §2.3 ("las ofertas publicadas primero son
las mejores del pool") es falsa tal cual: reformular o documentar la inversión posible.

### P2-2 — Progreso intra-lote invisible: un cuelgue de 600s se ve igual que un lote sano (§2.8)
El mensaje vivo cambia solo por lote (~5-10 min). Dentro del lote no hay señal: un
`ia_extract` colgado hasta `IA_LOT_TIMEOUT_S=600` es indistinguible del progreso
normal. El callback `progress` que ya existe en `run_ia_batch` permite actualizar
cada ~60s ("IA 112/252") sin tocar el throttle de 15s.

### P2-3 — `IA_LOT_TIMEOUT_S` ambiguo en alcance y con efecto compuesto (§2.4, §3)
No define si los 600s cubren solo la IA del lote o también enrich+rescore+publish
(enrich 8×~12s + publish 10×2s ya suman ~2-3 min). Y al cortar, los pendientes caen
en el hueco de P1-2. Con 2 workers, 600s implica corte si la latencia media supera
~30s/oferta (4× la nominal de 8s): plausible en una degradación real, no un caso
exótico.

### P2-4 — Sobrantes del tope por lote: retraso de hasta +4h e inversión de orden (§2.3)
Si los lotes siguientes también llenan el tope, las sobrantes de un lote no se
publican "en el lote siguiente" como dice §2.3, sino recién en el primer publish del
barrido siguiente (+4h): un 86 pts espera mientras salen 70-75. Documentar el caso o
reservar cupo del tope siguiente para sobrantes del anterior.

## Sin problema (pregunta explícita del encargo)
- **Mensajes al grupo (#6): el goteo no genera ruido.** El progreso es 1 mensaje vivo
  EDITADO (throttle 15s, bot.py:527-544) + 1 resumen final en `/search`; los barridos
  del cron no emiten mensajes al grupo; los digests suman 1-3/día. Los 7 lotes no
  añaden mensajes: el único volumen nuevo es el canal mismo (cubierto en P0-1).

## Prioridades
- **P0:** P0-1 (tope por barrido + volumen esperado).
- **P1:** P1-1 (digest vs barrido), P1-2 (cola nocturna no rescata), P1-3 (breaker),
  P1-4 (métricas por lote).
- **P2:** P2-1 (orden), P2-2 (intra-lote), P2-3 (timeout scope), P2-4 (sobrantes).