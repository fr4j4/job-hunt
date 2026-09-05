# Arquitectura de jobhunt

Refactor de estructura (paso 1-6): el monolito original (`enrich.py`,
`channel.py`, `scoring.py`, `bot.py`, `cli.py`, `stats.py`) se partió en un
paquete nuevo de módulos hoja (puros, sin DB, sin ciclos) que los módulos
viejos importan por nombre. Comportamiento sin cambios: mismos contratos,
mismas columnas de DB, mismos tests (que siguen monkeycheando nombres en los
módulos viejos).

## Diagrama de capas

```
┌─────────────────────────────────────────────────────────────────┐
│ APP (orquestación, punto de entrada)                             │
│   jobhunt.cli   — comandos: run/rescore/enrich/ia/report          │
│   jobhunt.bot   — daemon/watch: cron interno + comandos Telegram  │
└───────────────────────────┬───────────────────────────────────────┘
                             │ usa
┌───────────────────────────▼───────────────────────────────────────┐
│ DOMINIO DE PRODUCTO (reglas de negocio, aún en módulos viejos      │
│ que resuelven llamadas por su propio namespace — monkeypatch)      │
│   jobhunt.channel  — modo canal: gates, publish, digests           │
│   jobhunt.market   — pipeline reporte /market (4 fases → PDF)      │
│   jobhunt.charts   — PNGs matplotlib para digests del canal        │
│   jobhunt.notify   — digests Telegram modo run (re-exporta telegram/) │
│   jobhunt.enrich   — anillos A/B/C de enriquecimiento + apply_ia_result │
│   jobhunt.dedup    — dedup cross-plataforma (URL/fingerprint/fuzzy)│
│   jobhunt.relevance— gate de relevancia para fuentes tipo-feed     │
│   jobhunt.scoring  — motor de scoring paramétrico                  │
│   jobhunt.app.batch/state — pipeline de lotes IA + estado daemon   │
└───────────────────────────┬───────────────────────────────────────┘
                             │ importa (nunca al revés)
┌───────────────────────────▼───────────────────────────────────────┐
│ PAQUETE NUEVO — módulos hoja, puros/cliente, sin DB, sin ciclos    │
│                                                                     │
│   jobhunt.ia/        — prompts, schemas, coerción, cliente HTTP    │
│     client.py    CloudClient, LocalClient (requests.post+retries)  │
│     prompts.py   prompts EXTRACT lote/detalle                      │
│     schemas.py   _LOTE_SCHEMA (JSON schema estricto)                │
│     coercion.py  _coerce_salario, _normalizar_extract_local        │
│                                                                     │
│   jobhunt.fetch/     — ficha de una oferta ya encontrada            │
│     page.py      fetch_page, parse_jobposting (JSON-LD, Anillo A)   │
│                                                                     │
│   jobhunt.scoring... salarios/  — árbitro y estadística de salario  │
│     arbiter.py   SalaryArbitrator (decisión pura, sin DB)           │
│     stats.py     MAD+IQR robusto, classify_salary, annual_likely    │
│                                                                     │
│   jobhunt.telegram/  — cliente HTTP Bot API + render HTML puro      │
│     api.py       TelegramClient (getUpdates/sendMessage/editMessage)│
│     render.py    build_digest_text, table_block, tags de oferta     │
│                                                                     │
│   jobhunt.domain/    — vocabulario compartido (fechas/roles/texto/techs) │
│     fechas.py    canonical_date, normalize_date, age_days           │
│     roles.py     is_dev, _NONDEV_CATEGORIES (gate dev/no-dev)       │
│     texto.py     _norm (normalización de texto, sin deps)           │
│     techs.py     tabla única de tecnologías (ABBR/NAME/MARKET_ABBRS)│
│                                                                     │
│   jobhunt.app/       — orquestación (SÍ importa domain/ia/fetch/    │
│     batch.py     salarios; resuelve enrich.apply_ia_result etc.     │
│     state.py     vía import diferido para no crear ciclo)           │
└───────────────────────────┬───────────────────────────────────────┘
                             │ usa
┌───────────────────────────▼───────────────────────────────────────┐
│ BASE                                                                │
│   jobhunt.db      — esquema SQLite, upsert, rescore_all             │
│   jobhunt.config  — Config/.env                                     │
└─────────────────────────────────────────────────────────────────────┘

jobhunt.sources/  (fuentes de listado — LinkedIn/Indeed/Computrabajo/
Glassdoor/Laborum/Jooble/Accenture/AIRA) vive AL LADO de fetch/: sources
busca resultados de portal, fetch descarga+parsea la ficha de detalle.
Usado directamente por cli.cmd_run, sin capa intermedia.
```

## Responsabilidades por paquete

- **jobhunt.cli** — comandos de entrada (`run`, `rescore`, `enrich`, `ia`,
  `report`); `cmd_run` orquesta el barrido completo.
- **jobhunt.bot** — daemon (`watch`): cron interno + comandos Telegram
  (`/search /latest /score N`); usa `app.state` para el estado compartido.
- **jobhunt.channel** — modo canal broadcast: `canonical_date`, `is_dev`,
  `select_channel_offers`, `publish_channel`, `render_offer_post`.
- **jobhunt.market** — pipeline `/market`: agregar → gráficos → narrativa IA
  → PDF (reportlab).
- **jobhunt.charts** — 6 PNGs matplotlib (Agg) para digests del canal.
- **jobhunt.notify** — digests Telegram del modo `run` (re-exporta
  `telegram.render`).
- **jobhunt.enrich** — enriquecimiento por anillos (A: JSON-LD, B: regex,
  C: IA) y `apply_ia_result` (único escritor de campos IA en DB).
- **jobhunt.dedup** — 3 capas: URL normalizada, fingerprint título+empresa,
  fuzzy (Jaccard/secuencia).
- **jobhunt.relevance** — gate de relevancia (área estructurada, keywords,
  IA) para fuentes tipo-feed (AIRA).
- **jobhunt.scoring** — `compute_score`, `compute_market_score`,
  `_salary_to_clp_monthly` (motor paramétrico contra `Profile`/`Scoring`).
- **jobhunt.db** — esquema, `upsert`, `register_criteria_version`,
  `rescore_all` (único escritor de la tabla `ofertas`, patrón MAIN).
- **jobhunt.ia** — `CloudClient`/`LocalClient` (HTTP), prompts, schemas,
  coerción de tipos de la respuesta IA.
- **jobhunt.fetch** — `fetch_page`/`parse_jobposting` (ficha de detalle,
  Anillo A, JSON-LD).
- **jobhunt.salarios** — `SalaryArbitrator` (árbitro feed vs texto),
  `classify_salary`/estadística robusta MAD+IQR.
- **jobhunt.telegram** — `TelegramClient` (Bot API) + render HTML puro de
  digests.
- **jobhunt.domain** — `fechas` (canonicalización), `roles` (gate dev/no-dev),
  `texto` (`_norm`), `techs` (tabla única de tecnologías, antes triplicada).
- **jobhunt.app** — `BatchRunner`/`worker_ia`/`consume_lote` (pipeline de
  lotes IA del barrido) y `IAState`/`SearchState`/`StopEvent` (estado del
  daemon como clases).
- **jobhunt.sources** — un módulo por portal (`linkedin`, `computrabajo`,
  `indeed`, `glassdoor`, `laborum`, `jooble`, `accenture`, `aira`): listado
  de ofertas por búsqueda o feed completo.

## Flujo de datos de un barrido (`jobhunt run`)

```
sources/*.jobs()  →  dedup + upsert (db.upsert, fit score al vuelo)
      │
      ▼
Anillo A: enrich (fetch.fetch_page + fetch.parse_jobposting, JSON-LD)
      │
      ▼
Anillo B: enrich (regex — seniority_real, staffing, matices)
      │
      ▼
Anillo C: IA — lote (app.batch.worker_ia/consume_lote) o individual/local
      │  consume_lote llama enrich.apply_ia_result (único escritor DB)
      ▼
rescore_all (db.py, compute_score + compute_market_score)
      │
      ▼
canal (channel.publish_channel, gate A3) + digests (notify/bot, telegram.render)
      │
      ▼
report /market (opcional, bajo demanda): agregar → charts → narrativa IA → PDF
```

## Invariantes

1. **MAIN único escritor SQLite.** Todas las escrituras a `ofertas` pasan
   por el hilo principal (`db.py`, `enrich.apply_ia_result`). Los workers de
   IA (`app.batch.worker_ia`) corren en threads pero solo hacen HTTP — nunca
   tocan la conexión SQLite.
2. **Threads solo HTTP.** El paralelismo de `BatchRunner` es exclusivamente
   para las llamadas a la IA (`ia.client`); el resultado vuelve por cola al
   hilo principal, que es quien escribe.
3. **IA = autoridad semántica; regex = modo degradado.** Cuando `IA_ENABLED`
   está activo, `techs`/`rol_categoria`/`seniority_real` los decide la IA
   (Anillo C). El regex (Anillo B) solo corre cuando la IA no está disponible
   o no la contesta.
4. **Contrato de `enrich.apply_ia_result(conn, cfg, r, parsed, ctx_version="",
   model=None) -> bool`** — firma congelada, no tocar:
   - **Guard A3**: solo escribe `salary` si `salary_source` está vacío;
     nunca pisa una procedencia ya establecida (feed/texto) ni un `salary=''`
     que el árbitro haya vaciado deliberadamente.
   - **REFRESCA `techs`**: si la IA detecta una lista no vacía, reemplaza la
     columna siempre (una corrida anterior pudo no detectar nada); si la IA
     devuelve `[]`, preserva lo existente.
   - **Guard anti-alucinación** (spec-techs-dev-gate §2.2): se aplica
     DESPUÉS del refresh — si `rol_categoria` cae en `_NONDEV_CATEGORIES`,
     `techs` se fuerza a `''` aunque la IA haya alucinado una lista (en
     SQLite gana el último `SET` de la misma columna).
   - **Etiquetado `ia_model` real**: `model` por defecto es `cfg.ia.model`
     (cloud); en modo local se pasa `cfg.ia.local_model` explícito para que
     la trazabilidad de qué modelo generó cada campo sea verídica.

## Capas de compatibilidad

Módulos viejos que re-exportan símbolos del paquete nuevo (llevan el
comentario `# compat: re-export — eliminar en v6 cuando los imports apunten
al paquete nuevo`) — se mantienen porque los tests monkeypatchean nombres
ahí y/o hay imports externos que los referencian:

| Módulo viejo | Re-exporta desde |
|---|---|
| `jobhunt/channel.py` | `domain.fechas`, `domain.roles` |
| `jobhunt/scoring.py` | `domain.fechas`, `domain.techs`, `domain.texto` |
| `jobhunt/enrich.py` | `domain.roles`, `domain.techs`, `domain.texto`, `fetch.page`, `ia.client`, `ia.coercion`, `ia.prompts`, `ia.schemas`, `salarios.arbiter` |
| `jobhunt/cli.py` | `app.batch` |
| `jobhunt/bot.py` | `telegram.api`, `app.state` |
| `jobhunt/stats.py` | `salarios.stats` (alias completo — tests parchean aquí) |
| `jobhunt/db.py` | `domain.fechas`, `domain.texto` |
| `jobhunt/notify.py` | `telegram.render` |
| `jobhunt/charts.py` | `domain.techs`, `domain.texto` |

**Plan de eliminación (v6):** una vez que los tests y cualquier código
externo importen directamente desde `jobhunt.<paquete_nuevo>.<módulo>`, estas
líneas de re-export se borran y los módulos viejos quedan solo con su lógica
de dominio/orquestación propia (sin los imports de compat).

## Correr la suite

```bash
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
.venv/bin/python -c 'import jobhunt.bot, jobhunt.cli, jobhunt.enrich, jobhunt.channel'
```
