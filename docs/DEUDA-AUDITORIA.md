# Deuda técnica post-auditoría

Rama `feature/auditoria-integral` (9 commits sobre `main`). Fixes P1 aplicados +
modularización en `jobhunt/{domain,salarios,ia,fetch,telegram,app}` con capas
compat. Suite: 200 verdes (`.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`, ~9s).

Contrato intocable (no se toca en esta rama ni en el retoma): firma de
`enrich.apply_ia_result`, guard A3, refresco de `techs` en cada aplicación,
guard anti-alucinación, etiquetado `ia_model`, columnas de la DB, `main` como
único escritor SQLite, threads solo HTTP.

Cada ítem fue verificado con grep contra el HEAD de esta rama (02193ba). Donde
la descripción original ya no aplicaba se marca "resuelto en `<commit>`".

## 1. Deuda conocida — documentada, no resuelta

| Ítem | Estado actual | Plan |
|---|---|---|
| llama.cpp local roto | El endpoint OpenAI-compat de llama.cpp no soporta `response_format: json_object` igual que Ollama (`jobhunt/ia/client.py:32-38` construye el body con `response_format` sin fallback) | Probar `grammar`/`json_schema` en llama-server ≥ b4xxx antes de reactivar IA local |
| VRAM compartida modelo local + otras cargas | Knob ya existe: `IA_LOCAL_CONCURRENCY` (`jobhunt/config.py:334`, clamp 1-6) | Medir consumo real antes de subir el default de 2 |
| 68 ofertas Cloudflare/403 | `fetch_page` ya distingue `blocked` de `error`/`not_found` (`jobhunt/fetch/page.py:39-47`, status 403/429 → `blocked`) | Backoff exponencial por dominio + serializar reintentos por fuente (hoy no hay backoff, solo el flag) |
| 15 ofertas con IA sin techs | — | Reproceso con `iaclear` filtrado a esas 15 fichas (no un re-barrido completo) |
| Distribución `market_score`: 79% del pool < 50 | `compute_market_score` (`jobhunt/scoring.py:269-327`, v9: salario 36, modalidad 16, transparencia 20, ubicación 10, frescura 10, beneficios 8) | Propuesta v10 (salary 24, modality 18, transparency 26, location 10, freshness 12, benefits 10) — **PENDIENTE de medir contra el pool real**, no aplicada |
| `ai_red_flags`/`ai_green_flags` nunca consumidos | IA los produce (`jobhunt/ia/prompts.py:11`) y `enrich.py` los persiste, pero ningún render los lee (`jobhunt/telegram/render.py` no los referencia) | Mostrarlos en `render_offer_post` o quitarlos del prompt para no gastar tokens en algo que no se usa |

## 2. P2 no resueltos de la auditoría v4.1

| ID | Archivo:línea | Hallazgo | Plan corto |
|---|---|---|---|
| DB-2..DB-5 | `jobhunt/db.py:257-311`, `jobhunt/dedup.py:14-33` | `find_duplicate` hace `SELECT ... WHERE active=1` y escanea TODAS las filas activas con `norm_title`/`companies_match`/`similar` por cada upsert — O(n) por inserción, O(n²) por barrido | Índice o cache de `(norm_title, norm_company)` en memoria por barrido, invalidado al final |
| DB-6 | `jobhunt/db.py:335-341` | `norm_title` se recalcula sin cache para cada fila del pool en cada llamada a `find_duplicate`, aunque el título no cambia entre invocaciones del mismo barrido | `functools.lru_cache` sobre `norm_title`/`norm_company` (strings acotados, cache chico) |
| DB-7 | `jobhunt/cli.py:171-172` | `UPDATE ofertas SET score=?, score_version=?` recalcula y sobreescribe el score en CADA barrido para TODA oferta vista (nueva o repetida), incluso cuando ya tiene score IA-informado de un rescore posterior | Solo actualizar score en el upsert si `is_new`; dejar el resto a `rescore_all`/`rescore_ids` |
| DB-8 | `jobhunt/db.py:22,32` (`backfill_idiomas.py`, `migrate`) | Scripts de backfill abren su propia `sqlite3.connect` en vez de pasar por `database.connect` (bypassa el único-escritor documentado) | Documentar excepción (son scripts offline, no compiten con el daemon) o forzarlos a recibir `conn` inyectada |
| F5 | `jobhunt/enrich.py:415,422` | `fetch_fails < 3` excluye la ficha del SELECT de re-enrich para siempre — no hay reset ni cola de "última oportunidad" | Job periódico que resetee `fetch_fails=0` para ofertas con `fetch_fails>=3` y `last_seen` reciente (posible cambio de bloqueo temporal) |
| CONC-4..6 | `jobhunt/bot.py:495,505,794-802,1184-1294,1424` | `_IA_STATE` se libera (`reset()`) antes del rescore final; `_ack_done` corre en un thread separado con ventana de 2s; el patrón check-then-spawn (`if _IA_STATE["running"]` seguido de `.update(running=True)`) no es atómico | Mover el `reset()` después del rescore; usar un lock explícito en vez de check-then-spawn sobre un dict compartido |
| CONC-9 | `jobhunt/bot.py:491,721-802` | `_op_busy()` guarda la mayoría de comandos pero no todos los destructivos (ej. limpieza de inactivas en `bot.py:1057`, sin verificar contra `_op_busy()`) | Auditar cada comando de escritura/borrado y envolverlo con el mismo guard que `/run`, `/enrich`, `/ia` |
| S5 | `jobhunt/scoring.py:200-283` vs `jobhunt/salarios/stats.py:34` | Dos parsers de salario CLP independientes (`_salary_to_clp_monthly` sin banda 300k-20M explícita en el comentario de `stats.py:10`, y `parse_salary_clp` con banda propia) — pueden divergir en el mismo valor | Unificar en `salarios.stats.parse_salary_clp` y que scoring importe de ahí (compat re-export ya existe en `jobhunt/stats.py`) |
| S7 | `jobhunt/scoring.py:269-283` | `compute_market_score` usa el salario raw parseado sin filtrar por `salary_status` (`enrich.py:137,217,349` sí distinguen `suspect`/`implausible`) — una oferta con salario descartado por el árbitro igual puntúa alto | Pasar `salary_status` a `compute_market_score` y tratar `suspect`/`implausible` como "no declarado" |
| S8 | `jobhunt/scoring.py:171-172` | `x.lower().startswith(t[:4])` compara abreviaturas cortas (`"py"`, `"k8s"`) contra prefijos de 4 chars del stack del perfil (`"pyth"`, `"kube"`) — nunca matchean por longitud | Comparar contra el nombre completo normalizado (`NAME_BY_ABBR`) en vez de prefijos de 4 chars |
| S9 | `jobhunt/scoring.py:26-27` | `_years_from_description` solo matchea `\d{1,2}\+?\s*(años|anos|years)` — no cubre "3 a 5 años" ni números en palabras | Ampliar regex a rangos (`\d+\s*(?:a|-)\s*\d+\s*años`) si se detecta que aporta señal |
| S11 | `jobhunt/scoring.py:257-263` | `_beneficios_reales` filtra el string `'"No especificado"'` como campo completo, pero si la IA devuelve `["No especificado"]` (array con un solo ítem literal) el chequeo `len(arr)>0` lo cuenta como beneficios reales | Filtrar también valores del array que matcheen `"no especificado"`/`"ninguno"` normalizado |
| S12 | `jobhunt/scoring.py:71-76` | `_staffing` usa `re.search` sobre `"top 1%\|talent\|staffing\|..."` sin `\b` — `"talent"` puede matchear substring dentro de otra palabra | Añadir `\b...\b` a cada término del patrón |
| S13 | `jobhunt/relevance.py:133,149-152` | Con `mode="ia"` y sin `api_key`, las ofertas ambiguas se descartan silenciosamente (rama `else` solo documenta el caso `mode=="keywords"`) | Loguear warning explícito cuando `mode in ("ia","hybrid")` y falta `api_key`, y considerar fallback a `keywords` en vez de descarte total |
| CH-5 | `jobhunt/domain/techs.py:91-96` | `TITLE_RE` usa `\b\.net\b` — el `\b` antes de `.` no funciona como boundary real (ya documentado en el comentario de la línea 91-92, pero sin fix) porque `.` no es `\w` | Usar `(?<![\w.])\.net\b` o el patrón ya correcto de `_TECH_PATTERNS` (línea 110: `\.net\b\|\bdotnet\b`) |
| CH-7 | `jobhunt/channel.py:678`, `jobhunt/charts.py:118-122` | `seniority_real` es texto libre de la IA sin enum controlado; el filtro hace `sen.lower() in (r.get("seniority_real") or "")` (substring, no exact match) | Definir enum cerrado en el prompt (`junior/semi/senior/lead/data`) y validarlo en `apply_ia_result` |
| CH-11 | `jobhunt/channel.py:684-689`, `jobhunt/market.py:360-379` | `publish_trends` llama `_ia_call` sin comprobar `cfg.ia.enabled` — con IA apagada igual dispara un POST HTTP que falla y cae al `except` | Guard explícito `if cfg.ia.enabled and cfg.ia.api_key:` antes de llamar `_ia_call` |
| SEC-4 | `jobhunt/bot.py:353,375-413` | `/jobs <filtro>` acepta tokens libres en `f["loc"]` (`bot.py:353`) que se interpolan sin `esc()` en `_describe_filters` y se envían con `parse_mode: HTML` (`bot.py:1131`) — inyección de HTML en el mensaje de Telegram | Pasar cada término de `_describe_filters` por `esc()` antes de interpolar |
| SEC-6 | `jobhunt/enrich.py:271`, `jobhunt/fetch/page.py:184-189` | Chromium se lanza con `--no-sandbox` (duplicado en dos módulos) | Evaluar sandbox real en el contenedor de despliegue; si es necesario mantenerlo, documentar por qué (rootless container) |
| SEC-7 | `jobhunt/market.py:505-538` | `Paragraph(...)` de reportlab recibe texto de IA/DB (`agg['ia_model']`, bullets de `narr`) sin pasar por `xml.sax.saxutils.escape` — reportlab interpreta el string como mini-XML | Envolver todo texto dinámico con `escape()` antes de `Paragraph()` |
| F8 | `jobhunt/sources/indeed.py:4,45` | `ssl._create_unverified_context()` deshabilita verificación TLS para toda la fuente Indeed | Investigar por qué falla la verificación (cert intermedio faltante) y usar `certifi` en vez de desactivar TLS |
| F9 | `jobhunt/fetch/page.py:25,86`, `jobhunt/sources/linkedin.py:4` | User-Agent truncado: `"Chrome/120 Safari/537.36"` sin `(KHTML, like Gecko)` ni versión completa (`120.0.0.0`), a diferencia de `accenture.py`/`laborum.py` que sí llevan el UA completo | Unificar todos los UA a un único string completo y realista, compartido desde un módulo (hoy está duplicado 3 veces con variantes distintas) |
| T-P2-2 | `tests/test_v41.py:115-127` | `test_publish_commit_por_post` sigue siendo teatro: lee `notified_channel_at` sobre la MISMA conexión `:memory:` sin commitear — no prueba durabilidad real (mutación: borrar `conn.commit()` en `channel.py` no rompe el test) | Segunda conexión (archivo temporal) que lea después del "crash" simulado |
| T-P2-3 | — | Sin test de integración end-to-end de `publish_channel` con ≥2 workers reales ni de `run_daemon` (`jobhunt/bot.py:1383+`, cero matches de `run_daemon` en `tests/`) | Un test con `BatchRunner` real + `publish_channel` sobre DB temporal; un test de `run_daemon` con 1 iteración de loop mockeando `time.sleep` |

## 3. Plan de retoma — 3 sprints cortos

**Sprint 1 (seguridad + integridad de datos, ~1-2 días)**
- SEC-4 (esc en `/jobs`), SEC-7 (escape reportlab), F8 (TLS Indeed), F9 (UA unificado)
- S7 (salary_status en market_score), DB-7 (no sobreescribir score en cada barrido)

**Sprint 2 (perf + scoring, ~2-3 días)**
- DB-2..DB-6 (dedup O(n²) + lru_cache)
- S5 (unificar parser salario), S8 (stack_overlap), S11, S12
- Medir pool real y decidir v10 de `market_score` (con datos, no estimado)

**Sprint 3 (concurrencia + tests, ~2-3 días)**
- CONC-4..6, CONC-9 (guards de busy y liberación de `_IA_STATE`)
- F5 (reset de `fetch_fails`), CH-11 (guard IA en trends)
- T-P2-2, T-P2-3 (tests de durabilidad real + integración daemon/channel)

## 4. Eliminación de capas compat (objetivo v6)

Módulos marcados `# compat: re-export — eliminar en v6 cuando los imports apunten al paquete nuevo`:

| Módulo compat | Re-exporta desde |
|---|---|
| `jobhunt/charts.py` | `domain.techs`, `domain.texto` |
| `jobhunt/scoring.py` | `config`, `domain.fechas` |
| `jobhunt/notify.py` | `telegram.render` |
| `jobhunt/cli.py` | `app.batch` (`BatchRunner`, `consume_lote`, `lotes_por_fit`, `worker_ia`, `_extract_local_con_fallback`) |
| `jobhunt/channel.py` | (re-exports internos, ver header) |
| `jobhunt/enrich.py` | `domain.roles`, `domain.techs`, `ia.prompts` |
| `jobhunt/stats.py` | `salarios.stats` (alias completo, `import *`) |
| `jobhunt/bot.py` | `telegram.api.TelegramClient`, `app.state` |
| `jobhunt/db.py` | `domain.texto._norm` (línea 325, sin marcador de módulo pero mismo patrón) |

Plan v6: una vez que todo el código y los tests importen directo de
`jobhunt/{domain,salarios,ia,fetch,telegram,app}`, borrar estas líneas de
re-export. Verificar antes con `grep -rn "from jobhunt.<módulo_viejo> import" .`
en cualquier consumidor externo (ninguno detectado hoy: uso interno + tests).
Los tests que "leen nombres viejos en `jobhunt.enrich`" (comentario línea 26)
son el único bloqueante conocido — hay que migrar esos monkeypatches primero.
