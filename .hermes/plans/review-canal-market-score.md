# Review producto/QA — spec canal broadcast + market score (v2)

Revisor: perspectiva producto/QA pragmático. Verificado contra código real
(bot.py, scoring.py, config.py, db.py, cli.py, notify.py, market.py) y la DB de
producción (data/ofertas.sqlite, 578 activas al 2026-09-03) + .env + systemd unit.

## Hallazgos

### H1 — El gate de canal evalúa ofertas SIN enriquecer: las "nuevas" no pueden pasar 70 en su primer barrido (P0)
**Secciones afectadas:** §2 (componentes), §3.5/§3.6 (momento del publish), §5 (comportamiento), §7 (despliegue).

El market score depende de `salary`, `modality`, `techs`, `ai_red_flags` — campos
que llena el batch IA nocturno (03:00 UTC, IA_BATCH_SIZE=40). `publish_channel`
corre dentro del MISMO barrido que inserta las ofertas (00:00 UTC), antes de la IA.
Estado real del pool: 357/578 sin modalidad, 473/578 sin techs, cola IA = 168
pendientes; solo 106/578 tienen salario.

Simulación de la puntuación de la spec sobre las 578 activas: una oferta recién
insertada sin datos IA tiene techo ~49-53 pts (salario 5 + modalidad 8 +
transparencia 11 + stack por título ~10-15 + frescura 10). **Ninguna oferta nueva
puede alcanzar el umbral 70 en su primer barrido.** Solo cruza el gate días
después, cuando la IA la enriquece — y para entonces frescura = 3 pts y el post
dirá "hace 2-4 días".

Consecuencia observable para el suscriptor: canal silencioso la mayoría de los
barridos, y cuando publica, publica ofertas viejas. Eso contradice el propósito
del producto (feed fresco de calidad). Opciones: publicar también después del
batch IA (03:00+), relajar el gate a campos pre-IA (título/empresa/link),
o documentar el retraso de 1 ciclo como comportamiento aceptado.

### H2 — Cobertura IA insostenible: el umbral 70 se calibró contra una población que se encoge (P0)
**Secciones afectadas:** §2 (calibración), §3.2, §5.

IA_BATCH_SIZE=40/noche vs ~340 nuevas/día: la cola crece ~300/día (ya hay 168 de
atraso). La calibración de §2 ("P60 del subconjunto con datos completos") se hace
sobre el subconjunto que HOY tiene datos; ese subconjunto es minoritario y su
proporción cae con el tiempo. Distribución real medida con las reglas de la spec:
`80+: 4 · 70-79: 14 · 60-69: 11 · 40-59: 111 · <40: 438` → **18/578 pasan 70 HOY**.
El flujo diario que cruza el umbral será ~1-5 posts/día, no el flujo continuo que
sugiere la spec. No es problema de rate limits: es problema de canal sub-alimentado.
La spec debe declarar el volumen esperado por barrido y una política para la cola
IA (subir batch_size, priorizar ofertas nuevas, o aceptar canal de bajo volumen).

### H3 — "Backfill NO" se viola por deriva del score: resurección de ofertas viejas (P0)
**Secciones afectadas:** §5 (Backfill: NO / primer activación), §3.2 (rescore_all escribe ambos scores).

`notified_channel_at=''` + `active=1` + `market_score>=70` es el gate. Como
`rescore_all` recalcula TODO el pool activo en cada pasada (y la IA llena campos
días después), una oferta de hace 2 semanas puede saltar de 53 → 78 cuando la IA
le completa salario/remoto. Al siguiente barrido entra al canal con "hace 2
semanas". Eso ES backfill por deriva, exactamente lo que §5 dice que no pasa.
Fix: gate adicional por edad (`first_seen < N horas`, ej. 72h) o excluir
explicitamente ofertas viejas aunque crucen el umbral después.

### H4 — El market score no filtra no-dev: un COBOL mainframe bien pagado sale al canal (P1)
**Secciones afectadas:** §2 (componentes/set de mercado), §8.

Caso real en la DB: "Analista Programador Mainframe COBOL, $2.500.000" → salario
30 + modalidad 8 + transparencia ~11 + stack (SQL) 5-15 + frescura → 61-75, puede
pasar 70 y transmitirse a un canal "ofertas tech". El score mide "buena oferta"
(salario+modalidad+transparencia), no "dev". La columna `rol_categoria` YA existe
(85 "Ingeniería no-software", 32 "No-tech", 10 "Soporte/TI", 242 vacío) y la regex
`nontech_titles` existe en RelevanceCfg — la spec no usa ninguna. Fix barato:
gate/penalización por rol_categoria ∉ {Software, Backend, Data, Full Stack,
DevOps/Cloud, AI/ML, Seguridad} o hard-filter con el regex existente. Para un canal
comunitario esto es la diferencia entre "útil" y "ruido".

### H5 — Contradicción interna en la firma de publish_channel (P1)
**Secciones afectadas:** §3.5 vs §3.6.

§3.5 define `publish_channel(cfg, conn, offers: list[dict])` y describe el filtro
sobre `offers`; §3.6 la llama con `publish_channel(cfg, conn, [])` comentando que
"consulta la DB por las nuevas". Además §3.5 dice que se llama desde `sweep()` Y
desde `cmd_run` — pero `sweep()` ya ejecuta `cmd_run` internamente (bot.py:
_do_sweep → cmd_run): con ambas llamadas habría doble publicación por barrido
(el dedup por notified_channel_at lo salvaría, pero por coincidencia, no por diseño).
Pinzar UNA definición: `publish_channel(cfg, conn)` que consulta la DB, invocada
solo desde cmd_run (que cubre cron, /search manual y CLI con un solo camino).

### H6 — Comportamiento manual /search no documentado (P1)
**Secciones afectadas:** §3.5, §5.

Si publish vive en cmd_run (§3.6), un `/search` manual del admin publica al canal
PÚBLICO fuera del horario cron. Deseable (frescura) pero debe estar en §5:
"el canal publica también en barridos manuales". Si no, comportamiento sorpresa.

### H7 — TELEGRAM_ALLOWED_CHATS es dependencia oculta del canal (P1)
**Secciones afectadas:** §3.3, §4, §5.

`_tg_api` lanza PermissionError si el chat_id no está en TELEGRAM_ALLOWED_CHATS
(config.py/bot.py reales). El canal -1004495706494 YA está en el allowlist (verificado
en .env), pero la spec §4 no lo menciona: si se rota de canal y se olvida el
allowlist, publish falla en silencio para siempre (solo warnings en journalctl).
§4 debe incluir: "añadir el chat del canal a TELEGRAM_ALLOWED_CHATS" + validación
al cargar config (chat_id no numérico → deshabilitar canal con log, no crash en
int()).

### H8 — Post de prueba contamina el pool de producción (P1)
**Secciones afectadas:** §7 (despliegue paso 2).

"Insertar una oferta ficticia de prueba con market_score alto" en la DB real: esa
fila aparecerá en el digest del grupo, /score, /latest, entrará a la cola IA (el
batch nocturno la enriquecerá gastando llamadas), y ensucia el reporte de mercado.
"Borrar el post de prueba" borra el mensaje de Telegram, no la fila. Mejor: flag
`--dry-run` de publish_channel (renderiza sin enviar) o DB de prueba aparte.
También falta en §7: backup de la DB antes de la migración (trivial pero barato).

### H9 — Dedup cross-source: reposteo del mismo empleo con group_id distinto (P1)
**Secciones afectadas:** §5 (Reposteos occurrences>1).

La afirmación "NO re-postean (notified_channel_at ya seteada)" solo cubre merges
al MISMO group_id. El mismo empleo publicado por otra fuente con título distinto
crea group_id nuevo → es "nueva" → sale al canal duplicado. La calidad del dedup
(dedup.py) define la tasa de spam del canal. Añadir: chequeo de colisión de URL/
fingerprint previo al publish, o monitoreo manual la primera semana.

### H10 — Sin observabilidad de canal: no hay forma de operar sin supervisión manual (P1)
**Secciones afectadas:** §3.5, §7, falta sección.

Para producción sin supervisión faltan: (a) log estructurado por barrido
(candidatos ≥70, publicadas, saltadas por edad/dedup) — la spec solo loguea el
total; (b) alerta al chat de admin si publish_channel publica 0 posts en N barridos
consecutivos (canal muerto, umbral mal calibrado, allowlist roto — fallos silenciosos
hoy); (c) comando admin /channel status (última publicación, cola de candidatas,
umbral vigente) — copiar el patrón de /stats existente. §7 "observar el próximo
barrido y ajustar" es operación manual, no producción.

### H11 — El post no dice la fuente ni el inglés requerido (P2)
**Secciones afectadas:** §3.4.

El formato omite source (Laborum vs LinkedIn remoto cambia la confianza del lector)
y el flag de inglés (`ai_ingles`, "🗣 EN" solo aparece si hay dato, pero inglés
EXCLUYENTE es señal clave para un dev promedio y hoy se omite si ai_idiomas viene
vacío). Dos líneas baratas, alto valor de producto.

### H12 — Frescura sin definición de fallback (P2)
**Secciones afectadas:** §2 (frescura), §3.1 (entrada date_posted).

76/578 ofertas sin date_posted parseable; fuentes como computrabajo repostean con
fechas frescas falsas. La spec no define: ¿tier de frescura con date_posted del
source, o first_seen del sistema? Debe pinzarse (mueve el score hasta 7 pts y el
umbral es 70). Recomendación: min(date_posted válido, first_seen).

### H13 — La spec referencia `_parse_salary_clp` que no existe (P2)
**Secciones afectadas:** §3.1.

market.py tiene `_extraer_salarios_clp` (extrator plural para el reporte), scoring.py
tiene `_salary_to_clp_monthly`, bot.py tiene `_salary_clp` — tres parsers con
heurísticas USD distintas. El implementador que siga la spec al pie buscará una
función inexistente. Pinzar: reusar `_salary_to_clp_monthly` (mismo módulo que
compute_market_score) y documentar la detección USD (950 fijo vs usd_clp_rate
configurable en bot.py — heredar la tasa fija por ahora, anotado).

### H14 — Anomalías de diseño menores en la tabla de puntos (P2)
**Secciones afectadas:** §2.

(a) Modalidad "no declarada = 8" puntúa MÁS que presencial = 5: una oferta idéntica
sin declarar modalidad supera a una presencial declarada — defendible como castigo
a la incertidumbre, pero raro para el usuario del score; documentar la intención.
(b) Red flag −10 "sobre el subtotal" de transparencia: subtotal máx = 15; especificar
`max(0, subtotal − 10)` explícitamente en la tabla para que dos implementaciones
no diverjan. (c) Con el techo sin-salario-en-53 (5+8+15+15+10), las ofertas sin
salario declarado NO pueden pasar 70 nunca — coherente con el umbral, pero debe
quedar explícito en §2 porque define que el canal será ~90% ofertas con salario
visible (~18% del pool hoy).

### H15 — Umbral 70: volumen esperado casi nulo por diseño; plan de recalibración (P2)
**Secciones afectadas:** §2 (calibración), §5, §7.

Medido: 18/578 activos pasarían hoy (4 en 80+). La calibración de §2 (~5-10 "oro"
sobre 354) era sobre el pool con IA, no sobre el flujo de publicación. Resultado
práctico: 0-2 posts por barrido, canal de bajo volumen (probablemente deseable para
una comunidad, pero hay que decirlo). El P60 del subconjunto con datos completos
se moverá cuando crezca la cobertura IA → CHANNEL_MIN_SCORE necesitará recalibración
mensual; añadir la distribución de market_score a /stats (1 query, costo cero).

### H16 — Ofertas expiradas acumulan posts muertos en el canal (P2)
**Secciones afectadas:** §8 (fuera de alcance).

active=0 (valid_through) no genera ninguna actualización en el canal: los links
muertos se acumulan para siempre. Fuera de alcance para v1, pero una línea en la
descripción del canal ("las ofertas pueden expirar") y en §8 como deuda conocida.

## Qué está bien (no tocar)
- Separación fit/market y no tocar compute_score: correcto y bien acotado.
- No-op silencioso sin chat_id + vars .env sin tocar código: buen patrón (consistente
  con el resto de config.py).
- Migración con patrón ai_idiomas (PRAGMA table_info): probado en db.py:66-72.
- Sin botones/paginación en canal y backfill NO: decisiones correctas para v1.
- Dedup por notified_channel_at como idempotencia base: correcto como fundamento
  (falta lo de H9 para el caso cross-source).
- max_posts + sleep 2s: holgado; rate limits de Telegram NO son un riesgo con
  ≤60 posts/día (el riesgo real es el opuesto: H1/H2).
- Tests propuestos (§6): cubren lo crítico del cálculo; añadir el caso
  "oferta recién insertada sin IA no pasa 70" que documenta H1.

## Prioridades

**P0 (bloquean el propósito del producto):**
1. H1 — publicar post-IA o redefinir el gate para ofertas sin enriquecer (el canal
   hoy sería silencioso o publicaría tarde).
2. H3 — gate de edad (first_seen < 72h) para impedir backfill por deriva del rescore.
3. H2 — declarar política de cobertura IA (batch 40/día < 340 nuevas/día) y volumen
   esperado del canal; si no, CHANNEL_MIN_SCORE=70 opera sobre una población que
   no existe.

**P1 (funciona pero degrada la experiencia u opera a ciegas):**
4. H4 — gate dev/no-dev con rol_categoria o nontech_titles (calidad del feed comunitario).
5. H5 — unificar firma/lugar de publish_channel (una sola vía de publicación).
6. H10 — log por barrido + alerta de silencio + /channel status.
7. H7 — allowlist + validación de chat_id en la carga de config.
8. H8 — reemplazar la oferta ficticia en DB real por dry-run/DB de prueba; backup pre-migración.
9. H9 — colisión cross-source antes de publicar.
10. H6 — documentar publicación en /search manual.

**P2 (pulido y robustez):**
11. H11 — fuente + inglés excluyente en el post.
12. H12 — regla de frescura con fallback definido.
13. H13 — referencia a función inexistente; elegir parser único.
14. H14 — explicitar max(0, subtotal−10) y la asimetría no-declarada>presencial.
15. H15 — plan de recalibración del umbral + distribución en /stats.
16. H16 — nota de expiración en la descripción del canal.