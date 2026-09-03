# jobhunt

Monitor de ofertas tech Chile — 4 plataformas, score paramétrico, dedup cross-site.

## Setup

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env    # ← EDITA tu perfil aquí
    python -m jobhunt run

## Parametrización

Todo vive en `.env`:
- `PROFILE_*`: quién eres (techs, años, salario, preferencias) — el score se calcula contra esto
- `SCORE_*`: pesos del motor de afinidad
- `ALERT_MIN_SCORE`: umbral que define qué llega a Telegram
- `QUERIES_*`: qué buscar por plataforma (perfil)
- `SAMPLE_*`: queries amplias para estadísticas de mercado

## Cambiar el criterio de match

1. Edita pesos/keywords en `.env`
2. `python -m jobhunt rescore` → re-evalúa todo el pool en <1s (cero re-scraping)
3. El criterio queda versionado en `score_versions` (auditable)

## Comandos

    python -m jobhunt run        # barrido completo
    python -m jobhunt rescore    # re-evaluar con criterio actual
    python -m jobhunt enrich     # backfill descripciones (JSON-LD)
    python -m jobhunt ia         # batch IA (deepseek-v4-flash)
    python -m jobhunt report     # stats de mercado

## Cron sugerido

    0 */4 * * *  cd /mnt/data2/projects/jobhunt && .venv/bin/python -m jobhunt run

## Fuentes

LinkedIn · Computrabajo · Indeed · Glassdoor · Laborum (API searchV2) · Accenture (findjobs) · Jooble (scraping headless).

### Jooble (scraping headless) — dependencia extra

La API REST de Jooble exige sesión de usuario, así que usa Playwright + Xvfb:

    pip install playwright
    playwright install chromium
    sudo apt install xvfb

El barrido debe correr con `xvfb-run` para Jooble (o el daemon detecta Xvfb
y lanza headed automáticamente). Sin playwright la fuente se salta con warning.

## Paginación — límites verificados (sep 2026)

| Fuente | Páginas | Límite |
|---|---|---|
| Laborum | 3 × 3 modalidades | API real (corta por `total`) |
| Accenture | 2 | API pública |
| Jooble | scroll infinito (~100) | requiere Playwright+xvfb; `&page=N` es cosmético |
| LinkedIn | 1 (guest, últimos 7 días) | paginar dispara rate-limit |
| Indeed | 1 (20/query) | GraphQL móvil sin cursor/offset (introspección off); web tras Security Check |
| Glassdoor | 1 (20/query) | `pageNumber` y `paginationCursors` ignorados por el API en búsquedas COUNTRY |

Compensación en fuentes de 20/query: más queries distintas en `QUERIES_*`/`SAMPLE_*`.
