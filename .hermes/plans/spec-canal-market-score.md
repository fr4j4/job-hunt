# Spec: Market score + modo canal (broadcast) — v2 completa

## 1. Resumen

Separar el score actual (fit personal contra el perfil de Francisco) de un score
de mercado (objetivo, comunitario) y usar el market score como gate del modo
canal: broadcast de ofertas nuevas de calidad, 1 post por oferta, sin paginación.

- **Grupo (sin cambios):** ancla paginada, /search vivo, /score, /jobs — filtrados
  por fit score personal.
- **Canal (nuevo):** feed broadcast automático — filtrado por market score objetivo.

## 2. Market score — definición

Score objetivo 0-100, determinístico, sin perfil. Calculado en la misma pasada
que el fit score (misma función rescore_all, mismo versionado).

### Componentes (100 pts)

| Componente | Pts máx | Regla |
|---|---|---|
| **Salario declarado** | 40 | ≥2.7M = 40 · 1.9-2.7M = 30 · 1.3-1.9M = 15 · <1.3M = 5 · no declarado = 5 (incertidumbre castigada, no anulada) |
| **Modalidad** | 20 | remoto = 20 · híbrido = 10 · presencial = 5 · no declarada = 8 |
| **Transparencia** | 15 | empresa visible = 8 · contrato/jornada declarados = 4 · descripción completa (>400 chars) = 3 · red flags IA (staffing, "importante empresa") = −10 sobre el subtotal |
| **Stack demandado** | 15 | por cada tech del set de mercado (Python, Java, Scala, AWS, React, TypeScript, Kubernetes, Docker, NiFi, SQL, Node, Go, .NET, Spring, Angular, Postgres): +2.5, tope 15 |
| **Frescura** | 10 | <48h = 10 · ≤7 días = 7 · ≤14 días = 5 · más viejo = 3 |

Techs del set de mercado: constante en scoring.py (MARKET_TECHS). No es el perfil
de Francisco — es lo que el pool observado paga (top techs del análisis: Scala 35,
Python 30, SQL 28, NiFi 24, Java 16, React 12...).

### Red flags (descuentos, no piso)

- staffing detectado → −10
- company vacía o "Importante empresa del sector" → −5 (se aplica dentro de transparencia)
- Mínimo de market score: 0 (nunca negativo)

### Calibración con el pool actual

Sobre 354 ofertas con IA procesada, la distribución esperada:
- ≥80: ofertas con salario declarado + remoto/híbrido + stack (las "oro": 23people 3.05M remoto, TINET, Consultora DT 2.1M...) — ~5-10
- 60-79: buenas con hueco (sin salario o híbrido) — ~20-30
- 40-59: promedio (la mayoría: sin salario, presencial)
- <40: ruido no-dev, COBOL, guardias, etc.

CHANNEL_MIN_SCORE default: 70 (≈ el P60 del subconjunto con datos completos).
Ajustable en .env sin tocar código.

## 3. Cambios de código

### 3.1 `jobhunt/scoring.py`

- Nueva constante `MARKET_TECHS = {"python", "java", "scala", "aws", "react",
  "typescript", "kubernetes", "docker", "nifi", "sql", "node", "go", ".net",
  "spring", "angular", "postgres"}`
- Nueva función `compute_market_score(offer: dict, now: datetime | None = None) -> int`
  (pura, testeable, sin DB):
  - entrada: title, company, salary, modality, techs, description,
    description_source, date_posted, ai_red_flags, staffing
  - parsea salario con la misma regex CLP de market.py (`_parse_salary_clp`)
  - retorna 0-100
- Sin efectos secundarios; el rescore la llama junto a compute_score.

### 3.2 `jobhunt/db.py`

- Migración liviana (patrón ai_idiomas): columna `market_score INTEGER DEFAULT 0`
  si no existe; columna `notified_channel_at TEXT DEFAULT ''`
- `rescore_all` actualizado: escribe AMBOS scores (fit en score, market en
  market_score) en la misma pasada.
- `market.py` (reporte): agregar distribución de market_score a la metodología
  (1 línea en la sección de limitaciones, no cambia el análisis).

### 3.3 `jobhunt/config.py`

```python
@dataclass
class ChannelCfg:
    enabled: bool = True
    chat_id: str = ""            # vacío = modo canal OFF (no-op silencioso)
    min_score: int = 70
    max_posts: int = 10
    sleep_s: float = 2.0
```

Vars .env: `TELEGRAM_CHANNEL_ID`, `CHANNEL_MIN_SCORE`, `CHANNEL_MAX_POSTS`,
`CHANNEL_SLEEP_S`.

### 3.4 `jobhunt/notify.py`

- `build_offer_post(offer: dict) -> str`: el post de canal. Omite líneas sin dato.
  Sin botones. Link plano. Formato HTML-safe (esc en todo).
  ```
  🎯 [86] Backend Semi-Senior Java/Spring Boot
  🏢 Consultora DT · 📍 híbrido Stgo
  💰 $2.1M líquido
  🧰 Java · Spring Boot
  🗣 EN · 📅 hace 2 días
  🔗 https://www.laborum.cl/empleos/1118378959
  ```

### 3.5 `jobhunt/bot.py`

- `publish_channel(cfg, conn, offers: list[dict]) -> int`:
  1. si `not cfg.channel.enabled or not cfg.channel.chat_id`: return 0 (no-op)
  2. filtra: `market_score >= cfg.channel.min_score`, `notified_channel_at = ''`,
     `active = 1`
  3. ordena market_score DESC, corta a `max_posts`
  4. por cada una: `sendMessage` al canal con `build_offer_post`,
     sleep `cfg.channel.sleep_s`, UPDATE `notified_channel_at = now`
  5. fallos del canal (403/400) → log warning, no tumba el barrido
  6. retorna número publicado
- Llamado desde `sweep()` (cron) y desde `cmd_run` después del rescore
  (vía callback en `_run_search_async` o directamente en cli.py — decisión:
  en cli.py, `cmd_run` ya tiene conn y las nuevas).

### 3.6 `jobhunt/cli.py`

- Después del rescore del barrido:
  ```python
  if cfg.channel.enabled and cfg.channel.chat_id:
      phase("canal: publicando nuevas")
      n_ch = publish_channel(cfg, conn, [])   # publish_channel consulta la DB por las nuevas
      log.info("canal: %d ofertas publicadas", n_ch)
  ```

## 4. Config (.env)

```
# ---------- CANAL (broadcast) ----------
TELEGRAM_CHANNEL_ID=-1004495706494
CHANNEL_MIN_SCORE=70
CHANNEL_MAX_POSTS=10
CHANNEL_SLEEP_S=2.0
```

## 5. Comportamiento y edge cases

- Canal sin ID → modo OFF, cero llamadas, cero errores.
- Ofertas nuevas < umbral → no se postean (quedan en pool/grupo).
- Más de max_posts sobre umbral → se postean las top por market_score.
- Reposteos (occurrences>1) → NO re-postean (notified_channel_at ya seteada).
- Canal borrado / bot degradado → warning, el barrido continúa.
- Backfill: NO. Solo ofertas nuevas desde la activación del modo canal.
- Primer activación: nada histórico al canal; el canal arranca limpio.
- El fit score personal NO toca el canal (ni viceversa).

## 6. Tests (pytest, primer test suite del repo)

- `test_compute_market_score`: 6 casos de calibración (23people 3.05M remoto
  → ≥80; TINET sin salario híbrido → 55-70; guardia sin nada → <30;
  presencial sin empresa → <40; remoto sin salario con buen stack → 55-70;
  data engineer banca híbrido con techs → 65-80).
- `test_build_offer_post`: omite líneas sin dato, escapa HTML, link presente.
- `test_publish_channel_filtro`: mock de _tg_api — verifica orden DESC, tope
  max_posts, dedup, y no-op sin chat_id.

## 7. Despliegue

1. commit + push + `systemctl --user restart jobhunt.service`
2. Post de prueba al canal: insertar una oferta ficticia de prueba con
   market_score alto, correr publish_channel manual, verificar formato en el
   canal, borrar el post de prueba.
3. Observar el próximo barrido (20:00 UTC) y ajustar CHANNEL_MIN_SCORE si
   el volumen no es el esperado.

## 8. Fuera de alcance (v1)

- Paginación o botones en el canal (explícitamente rechazado)
- Backfill histórico
- Formatos distintos por seniority
- Reenvío/compartir tracking
- Segundo canal (jobs-junior, etc.) — trivial de agregar después con otra
  variable CHANNEL2_* si se quiere