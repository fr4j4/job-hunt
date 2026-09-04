# /report — Análisis de mercado con PDF

## Objetivo
Comando `/report` en el bot de Telegram de jobhunt que genera un análisis completo del mercado de ofertas, con narrativa orientada a buscadores de empleo, gráficos, y entrega final en PDF al chat.

## Decisiones de diseño (ya validadas contra el código)

1. **Nueva librería**: `market.py` (~450 líneas) en `jobhunt/`. El resto de módulos quedan intactos salvo bot.py (registro de comando) y config.py (opcional `REPORT_*`).
2. **Dependencias**: `matplotlib` 3.11.1 y `reportlab` 5.0.1 ya instaladas en .venv y verificadas. Ambas puras-Python + binarios wheel, sin dramas de PEP 668 porque es venv propio.
3. **Envío binario a Telegram**: `_tg_api` actual solo manda JSON. Se agrega `_tg_send_document(cfg, chat_id, path, caption)` usando multipart/form-data con urllib (mismo estilo que `_tg_api`, sin nueva dependencia).
4. **Arquitectura de 3 fases**, replicando el patrón `_ia_batch_async` (thread + estado global + progress con throttle):

```
/report
  ├─ ACK inmediato: "📊 Análisis iniciado — fase 1/3: agregación"
  ├─ FASE 1 (f1_agregar): SQL sobre ofertas.sqlite → dict de aggregates (~1s)
  ├─ msg: "✓ Pool agregado: 244 ofertas · fase 2/3: gráficos"
  ├─ FASE 2 (f2_graficos): 6 PNG matplotlib → data/reports/charts_YYYYMMDD/ (~10s)
  ├─ msg: "✓ 6 gráficos · fase 3/3: narrativa IA"
  ├─ FASE 3 (f3_narrativa): 2 llamadas IA (market_story + tldr) vía mismo
  │   endpoint que ia_extract (~40-90s con deepseek-v4-flash)
  ├─ FASE 4 (f4_pdf): reportlab compone PDF → data/reports/mercado_YYYYMMDD.pdf (~5s)
  ├─ sendDocument del PDF + resumen en texto del mensaje final
  └─ falla parcial: si IA cae, PDF sale con plantilla sin narrativa (flag en portada)
```

5. **Progreso**: mismo throttle de `_mk_progress_cb` (1 msg/30s) para fases, y por-oferta silencioso (no hay loop largo en f2/f4). Estado en `_REPORT_STATE` (dict global, mismo patrón `_IA_STATE`).
6. **Lock de operaciones**: reutiliza `_op_busy()` agregando `report` — evita que /enrich y /report pisen la DB.
7. **IA**: reutiliza `cfg.ia` (base_url, api_key, model). Nuevo `REPORT_SCHEMA` JSON con campos narrativos. `temperature: 0.3` (narrativa, no extracción).

## Spec funcional

### FASE 1 — Agregación (pura SQL, sin IA)
Queries sobre `ofertas WHERE active=1`:
- `seniority_real` GROUP BY → conteo + %
- `modality` GROUP BY
- techs: split de `techs` por ';' → Counter top 15
- categorías de rol: regex sobre `title` (misma lista del análisis manual: Full Stack, Backend, Data, Mobile, AI/ML, Tech Lead, DevOps, QA, Frontend)
- salarios: regex `salary` (formato "CLP N" y "$ N.NNN.NNN (Mensual)") → lista CLP → mediana, p25, p75, min, max, distribución por tramo
- empresas: `company` GROUP BY top 12 + % sin empresa visible
- fuentes: `source` split ':' → plataforma
- fechas: `date_posted` → ofertas por día (últimos 14 días)
- red/green flags: `ai_red_flags`/`ai_green_flags` JSON-parse → Counter top 8
- inglés: `ai_ingles` GROUP BY
- ubicación: regex sobre `location` → regiones normalizadas
- reposteo: `occurrences` ≥2

Output: dict `agg` con todo. Se guarda en `_REPORT_STATE["data"]`.

### FASE 2 — Gráficos (matplotlib, Agg backend)
6 PNG a 150 dpi, paleta oscura consistente, títulos en español:
1. `seniority.png` — barras horizontales (Senior/Semi/Lead/Junior/Sin dato)
2. `techs.png` — barras horizontales top 12, % sobre pool
3. `roles.png` — barras categorías de rol
4. `salarios.png` — histograma de tramos (800k, 1.3M, 1.8M, 2.3M, 2.8M, 3M+) + línea de mediana
5. `actividad.png` — líneas ofertas/día últimos 14 días
6. `empresas.png` — barras top 10 empresas
Cada uno `fig.savefig(...)` y `plt.close(fig)` — nunca `plt.show()` (headless).
Si una figura no tiene datos (ej: 0 salarios), genera PNG con texto "sin datos" en vez de fallar.

### FASE 3 — Narrativa IA
2 llamadas a `cfg.ia` con JSON forzado:
- `market_story`: input = resumen compacto de FASE 1 (JSON serializado, ~2KB). Output: `{ "relato": "6-8 párrafos markdown-lite para buscadores de empleo (actividad, seniority, techs, salario, geografía, consejos)", "consejos": ["..."] }` — prompt pide tono directo, segunda persona, sin jerga estadística, con los números incrustados.
- `tldr`: 1 llamada corta → `"tldr": "4-5 bullets"` (o se deriva del relato sin segunda llamada si conviene).
Fallback sin IA: plantilla con los números duros de FASE 1 y flag "⚠️ narrativa generada sin IA (modelo no disponible)".

### FASE 4 — PDF (reportlab platypus)
`data/reports/mercado_YYYYMMDD_HHMM.pdf`:
- Portada: título, fecha, N ofertas, fuentes, versión de criterio (score_version), modelo IA
- Secciones en orden: Resumen ejecutivo (tldr) → Actividad → Roles → Seniority → Tecnologías → Salarios (con tabla de tramos) → Empresas → Geografía y modalidad → Red flags → Green flags → Consejos prácticos → Metodología y limitaciones
- Cada sección: párrafo narrativo (si IA OK) + tabla/bullets de datos + gráfico PNG
- Tablas con `Table` + `TableStyle` (grid fino, header gris)
- Footer con timestamp + "generado por jobhunt /report"
- Target < 2 MB (los PNG a 150 dpi quedan ~100-200 KB c/u)

### Envío
- `sendDocument` con el PDF + caption de 200 chars con highlights (mediana salarial, total ofertas, top tech)
- Además, mensaje de texto final: los 3 hallazgos principales + ruta local del PDF
- PDF se conserva en `data/reports/` (no se borra) — historial de reportes

## Comando y estados

- `/report` → lanza en thread (mismo patrón /enrich)
- `/report status` → fase actual + item + minutos (lee `_REPORT_STATE`)
- `/report list` → últimos PDFs generados con fecha y tamaño
- Mientras corre: `_op_busy()` retorna "report" → /enrich y /search esperan
- Cancelación: NO en v1 (thread daemon muere con el proceso si hace falta; documentar)

## Config nueva (.env.example)
```
# ---------- REPORT /market ----------
REPORT_ENABLED=true
REPORT_IA_NARRATIVE=true      # false = PDF solo con datos, sin llamadas IA
REPORT_MAX_SALARY_SAMPLES=60  # tope de ofertas de salario consideradas
REPORT_OUT_DIR=data/reports   # relativo a WorkingDirectory
```
Parsing en config.py → `cfg.report` (dataclass ReportCfg con defaults).

## Edge cases cubiertos
- Pool < 30 ofertas: ACK avisa "muestra chica, análisis orientativo" y sigue (no bloquea)
- matplotlib sin DISPLAY: `matplotlib.use("Agg")` antes de pyplot — headless safe
- SQLite locked por barrido concurrente: FASE 1 usa `mode=ro` URI + retry ×3 backoff 2s
- IA sin respuesta tras retries: FASE 3 → plantilla fallback, PDF igual se entrega
- PDF > 49 MB (imposible en la práctica): sendDocument falla → se envía solo el path
- Thread muerte: try/except global que setea `_REPORT_STATE["running"]=False` y notifica error
- Dos /report simultáneos: `_REPORT_STATE["running"]` + `_op_busy` lo bloquean

## Archivos tocados
| Archivo | Cambio |
|---|---|
| `jobhunt/market.py` | NUEVO ~450 líneas: 4 fases + helpers |
| `jobhunt/bot.py` | +`_REPORT_STATE`, +`/report` handler (3 sub-comandos), +`_tg_send_document`, +entrada en _register_commands y _help_text |
| `jobhunt/config.py` | +dataclass ReportCfg + parsing REPORT_* |
| `.env.example` | +3 líneas |
| `requirements.txt` | +matplotlib, reportlab (comentado "solo /report") |

## Estimación
- Implementación: 1 sesión (~350-450 líneas nuevas)
- Tiempo de ejecución de un /report real: ~60-110s end-to-end (244 ofertas actuales)
- Costo IA: 2 llamadas (~4-6K tokens) por reporte

## Testing (fase posterior o inmediata con DB real)
- CLI: `python -m jobhunt market` (mismo pipeline, imprime camino feliz sin Telegram) → valida FASE 1-4 local
- Luego comando bot real en el grupo de prueba