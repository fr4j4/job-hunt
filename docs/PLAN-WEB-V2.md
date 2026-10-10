# Plan: jobhunt Web v2 — explorador de ofertas y análisis de mercado

Estado: propuesta (no implementada). Fecha: 2026-10-10.

## 1. Diagnóstico de la web actual

3 páginas Jinja server-rendered (`/`, `/oferta/{id}`, `/fuentes`), 76 líneas de CSS, **cero JavaScript** (CSP `default-src 'none'`).

| Área | Hoy | Límite |
|---|---|---|
| Ofertas | Tabla única, 50/página, filtros por `<form>` GET, orden por clic | Un solo formato; cada filtro recarga la página; no hay vista tarjetas/kanban/comparación |
| Detalle | Ficha + desglose del score + análisis IA | Sin contexto de mercado ("¿este sueldo es alto para su rol?") |
| Fuentes | Tabla de conteos de los últimos 10 barridos | Sin gráficos ni tendencias |
| Análisis | **No existe en la web.** `market.py` + `charts.py` generan un PDF con matplotlib vía `/report` | Estático, sin interacción, no explorable |
| Estado del usuario | No existe (no se puede guardar/postular/descartar) | La web es solo lectura |

Fortalezas a conservar: auth por enlace de un solo uso + cookie HttpOnly, sesiones hasheadas, cabeceras estrictas, escucha en loopback, el motor de score y su desglose explicable.

## 2. Qué datos hay hoy (DB real, 2026-10-09)

1.095 ofertas (1.093 activas), 274 empresas, 7 fuentes, 12 barridos en `scan_log`, historia desde 2026-10-08.

**Cobertura por campo (el factor que más limita el análisis):**

| Campo | Cobertura | Observación |
|---|---|---|
| título, empresa, fuente, url, score, market_score, ai_encaje | ~100% | Base sólida |
| descripción | ~92% | Computrabajo/Jooble menos |
| `rol_categoria` | 100% (12 categorías) | Buena segmentación; "Analista/Empresa" y "Otro" = 27% |
| `seniority_real` | 55% | senior 346, junior 123, lead 73, semi 59 |
| `techs` | 41% (447) | String `;`-separado con abreviaturas (`Py`, `JS`, `TS`) |
| `modality` | **38%** | 62% vacío → gráficos de modalidad sesgados |
| `salary` | **22% (239)**, solo 114 `trusted` | Texto libre; la conversión a CLP mensual se calcula al vuelo en `scoring.py` |
| `industry` | 31% | Casi solo Jooble/Computrabajo |
| `employment_type` | 39% | |
| `ai_benefits`/`ai_idiomas` | 13% / 11% | JSON en texto |
| `applicants_hint` | 7% | Señal de demanda, casi solo LinkedIn |
| `years_official`, `staffing` | **0 filas con valor** | Columnas muertas o bug de extracción — revisar |
| `location` | texto libre | Sin normalizar a comuna/región |

**Estructura que bloquea el análisis:**
1. **Sin historia por oferta.** `ofertas` es un upsert: solo `first_seen`, `last_seen`, `occurrences`. Si el sueldo, el score o la vigencia cambian, se pierde. Sin esto no hay tendencias reales de mercado (hoy hay 2 días de datos).
2. **Columnas multivaluadas como texto**: `techs` (`;`), `ai_benefits`/`ai_red_flags`/`ai_green_flags`/`ai_idiomas` (JSON), `sources`/`found_by` (CSV). Agregar por tecnología exige parsear en Python.
3. **Sueldo no numérico** persistido. Falta `salary_clp_min/max/mid` y periodo normalizado.
4. **Empresa y ubicación sin normalizar** (variantes del mismo nombre cuentan como empresas distintas).
5. **Series de tiempo solo de volumen por fuente** (`scan_log.sources_summary`, JSON).
6. Sin estado de usuario (guardada, postulada, entrevista, descartada, notas).

## 3. Decisión de tecnología

**Recomendación: Svelte 5 + Vite + TypeScript (SPA estática) sobre el FastAPI existente, con Apache ECharts (importación por módulos) para gráficos.**

Por qué:
- Bundle pequeño (app ~40–60 KB gz + ECharts tree-shaken ~150–250 KB gz). Más liviano que React y menos boilerplate para estado reactivo (filtros cruzados).
- ECharts cubre todo lo necesario sin código a medida: boxplot, violín (custom), heatmap, treemap, sankey, scatter, calendar, sunburst, brush/zoom y exportar PNG.
- Tabla/lista virtualizada con TanStack Virtual (compatible con Svelte) → 10k filas fluidas.
- El backend Python no cambia de rol: pasa de renderizar HTML a servir JSON + el `dist/` estático.

Alternativas descartadas:
- **React + shadcn**: ecosistema más grande, pero más peso y más ceremonia para una app de un solo usuario. Elegirlo solo si ya lo dominas; el plan no cambia, solo el framework.
- **htmx + Alpine (sin build)**: ideal para sitios CRUD, insuficiente para un explorador con filtros cruzados y gráficos enlazados.
- **Observable Plot / D3 puro**: más elegante, más trabajo; ECharts da interacción lista.
- **Streamlit/Dash/Grafana/Metabase**: rápidos pero no dan la experiencia de producto ni integran la auth actual.

**Dónde calcular:** modelo híbrido. `/api/snapshot` entrega todas las ofertas en formato columnar compacto, **sin descripción** (~1.100 filas ≈ 250–400 KB, ~60 KB gzip; escala bien hasta ~20k filas). El navegador filtra y agrega en memoria (filtro cruzado instantáneo, sin viajes al servidor). La descripción y el detalle se piden bajo demanda. Las agregaciones pesadas/históricas (tendencias) se precalculan en el servidor.

## 4. Arquitectura

```
jobhunt/
  api/                  # NUEVO: routers JSON (FastAPI), sin lógica de negocio nueva
    ofertas.py          #   snapshot, detalle, estado de usuario
    analisis.py         #   series y agregados precalculados
    sistema.py          #   fuentes/salud, perfil, versión de score
  web/
    app.py              # auth + cabeceras + monta /api y sirve dist/ (fallback SPA)
    legacy/             # las páginas Jinja actuales, bajo /legacy hasta tener paridad
  analytics/            # NUEVO: materializa tablas derivadas (techs, salarios CLP, snapshots)
frontend/               # NUEVO
  src/{lib,routes,stores,charts,components}
  vite.config.ts        # build -> jobhunt/web/dist
```

- **Auth sin cambios** (enlace `/web` del bot → cookie). El front detecta 401 y muestra la pantalla de acceso.
- **CSP** pasa a `script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:` — sin `unsafe-inline`; Vite emite todo como archivos externos. Se mantienen el resto de cabeceras.
- **Estado en la URL**: todos los filtros/vistas se serializan en el query string (compartible, botón atrás funciona, vistas guardadas = URLs).
- **Build**: `npm run build` genera `jobhunt/web/dist`; el daemon solo sirve archivos. `dist/` va en `.gitignore`; un `make web` (o paso en README) lo reconstruye. Node es dependencia de desarrollo, no de ejecución.
- **Sin romper nada**: Telegram, daemon y CLI no se tocan. La web vieja sigue disponible en `/legacy` hasta el hito final.

## 5. Cambios de datos (la base del análisis)

Migraciones aditivas e idempotentes en `db.py`, rellenadas por un módulo `analytics/materializar.py` que corre al final de cada barrido (y como comando `python -m jobhunt materialize` para backfill):

| Objeto | Contenido | Para qué |
|---|---|---|
| columnas `salary_clp_min`, `salary_clp_max`, `salary_clp_mid`, `salary_period` en `ofertas` | Sueldo mensual CLP numérico, derivado de `salary`/`salary_raw` con la lógica existente de `scoring._salary_to_clp_monthly` | Estadística salarial sin parsear texto |
| `oferta_techs(group_id, tech)` | `techs` normalizado con alias canónicos (`Py`→Python) | Demanda por tecnología, co-ocurrencia, prima salarial |
| `oferta_tags(group_id, tipo, valor)` | beneficios, red/green flags, idiomas (`tipo` = beneficio/rojo/verde/idioma+nivel) | Frecuencias y filtros |
| `empresa_canon(alias, canonica)` + columna `company_canon` | Normalización de nombres (minúsculas, sin sufijos SpA/Ltda/S.A.) | Rankings de empresas fiables |
| `ubicacion` normalizada (`region`, `comuna`) | Diccionario de comunas/regiones de Chile + "remoto/LatAm" | Mapa y comparación regional |
| **`oferta_eventos(group_id, ts, tipo, antes, despues)`** | Eventos: aparecida, reaparecida, sueldo cambiado, score cambiado, desactivada | Historia por oferta; tiempo de vida; "ofertas que bajaron" |
| **`mercado_diario(fecha, rol, seniority, fuente, n_activas, n_nuevas, n_cerradas, sueldo_p25/p50/p75, n_con_sueldo)`** | Foto agregada diaria | Tendencias robustas y baratas de consultar; sobrevive aunque se purguen ofertas |
| `estado_oferta(group_id, estado, nota, actualizado)` | `nueva · guardada · postulada · entrevista · oferta · descartada` | Kanban y seguimiento personal |
| `vistas_guardadas(nombre, query)` | Filtros nombrados | Accesos rápidos |

Higiene de captura (mejora el análisis desde el origen, independiente del front):
- Investigar por qué `years_official` y `staffing` están en 0 (extractor roto o campo obsoleto).
- Subir cobertura de `modality` (62% vacío): inferirla de título/descripción como fallback y marcar `modality_source` (oficial / inferida).
- Guardar `scan_id` en `oferta_eventos` para enlazar con `scan_log`.
- Registrar `salary_clp_*` también cuando el sueldo viene de la IA, con bandera de procedencia (ya existe `salary_source`/`salary_status`).

**Límite honesto:** con 2 días de historia, las vistas de tendencia mostrarán poco al inicio; `mercado_diario` empieza a acumular desde el primer día en que se despliegue, así que conviene hacer la Fase 1 pronto aunque el front venga después.

## 6. API (JSON, solo lectura salvo estado del usuario)

| Endpoint | Respuesta |
|---|---|
| `GET /api/snapshot?activas=1` | Columnar: `{cols:[...], rows:[[...]], v:<score_version>}` sin descripción. ETag por `max(updated_at)` |
| `GET /api/oferta/{id}` | Ficha completa + desglose score/market + descripción + eventos + comparables de mercado (percentil de sueldo en su rol/seniority) |
| `GET /api/analisis/series?m=nuevas|activas|sueldo&por=rol|fuente|seniority&desde=…` | Series desde `mercado_diario` |
| `GET /api/analisis/techs` | Demanda, co-ocurrencia, prima salarial, tendencia semanal |
| `GET /api/fuentes` | Salud por fuente (reutiliza `salud.py`): n por barrido, errores, racha, tasa de sueldo declarado |
| `GET /api/perfil` | Techs/rol/sueldo del perfil (para "brecha de habilidades") |
| `PUT /api/estado/{id}` · `GET/PUT /api/vistas` | Único punto de escritura; CSRF con la misma `_misma_origen` + token de cabecera |

Todo detrás de la cookie de sesión. Pydantic para esquemas; los tests de `tests/test_web.py` se extienden a la API.

## 7. Producto: vistas y funcionalidades

**Navegación:** Inicio · Ofertas · Análisis · Explorador · Seguimiento · Fuentes. Atajos de teclado (`/` buscar, `j/k` mover, `g`+letra ir a, `?` ayuda). Tema claro/oscuro/auto, responsivo hasta móvil.

### 7.1 Inicio (resumen)
Tarjetas KPI (activas, nuevas hoy/7d, mejores por encaje, mediana salarial de tu rol) con sparkline; "Top para ti hoy"; alertas (fuente caída, oferta guardada que cambió/cerró); mini mapa de calor de actividad por día.

### 7.2 Ofertas — mismos datos, 5 formatos
Barra de filtros persistente (chips + panel): texto, rol, seniority, tecnologías (multi, AND/OR), modalidad, rango de sueldo (slider con histograma), score/encaje, fuente, antigüedad, empresa, con/sin sueldo, staffing, idioma. Contadores en vivo por facet. Selector de vista:
1. **Lista densa** (tabla virtualizada, columnas elegibles/reordenables, orden multi-columna, exportar CSV).
2. **Tarjetas** (resumen IA, chips de techs, sueldo, badges de encaje/antigüedad).
3. **Maestro-detalle** (lista a la izquierda, panel de detalle a la derecha sin recargar — flujo de triage rápido).
4. **Kanban** por `estado_oferta` con arrastrar y soltar.
5. **Comparador**: 2–4 ofertas lado a lado (sueldo, stack, beneficios, flags, score desglosado).
Más: agrupar por empresa/rol/fuente con subtotales; selección múltiple (guardar/descartar en lote); vistas guardadas.

### 7.3 Detalle de oferta
Ficha actual + **contexto de mercado**: dónde cae el sueldo en la distribución de su rol/seniority (percentil), demanda de sus techs, "ofertas similares" (techs + rol), línea de tiempo de eventos (cuándo apareció, cambios, fuentes que la publican), brecha entre tus techs y las pedidas, desglose del score en cascada (waterfall) en vez de tabla. Botón de estado + notas.

### 7.4 Análisis de mercado (dashboard interactivo; reemplaza el PDF)
Filtros globales con **filtro cruzado** (clic en una barra filtra todo el tablero):
- **Volumen y ritmo:** nuevas/cerradas por día (áreas apiladas por rol o fuente), antigüedad de las activas, tiempo de vida.
- **Salarios:** distribución (histograma + boxplot) por rol, seniority, modalidad, fuente; comparación con tu rango objetivo; transparencia salarial (% con sueldo) por fuente/empresa. Siempre mostrando `n` y avisando cuando `n < 10`.
- **Tecnologías:** ranking de demanda, treemap por categoría, **co-ocurrencia** (heatmap), **prima salarial por tecnología**, tecnologías en alza/baja (cuando haya historia), **brecha vs tu perfil** ("qué aprender").
- **Roles × seniority:** matriz de calor de cantidad y de mediana salarial.
- **Empresas:** más contratantes, directa vs staffing, % sueldo declarado, red flags frecuentes.
- **Condiciones:** modalidad, jornada, idiomas/inglés requerido, beneficios más frecuentes, red/green flags.
- **Geografía:** barras por región/comuna (mapa coroplético en una fase posterior si se justifica).
- **Calidad del score:** `score` vs `market_score` (dispersión), distribución de encaje.

### 7.5 Explorador libre
Constructor tipo pivot: elegir **dimensión** (rol, techs, empresa, fuente, mes…), **desglose** opcional, **métrica** (conteo, mediana/p75 de sueldo, score medio, % con sueldo) y **tipo de gráfico** (barras, líneas, áreas, dispersión, heatmap, treemap, tabla). Se serializa en la URL, se guarda como vista, se exporta (PNG/CSV). Es la herramienta para preguntas que no previó el dashboard.

### 7.6 Seguimiento
Kanban + lista de postulaciones, notas, fechas, recordatorios (reutilizando el bot de Telegram para avisar), embudo de conversión personal (guardadas → postuladas → entrevistas).

### 7.7 Fuentes y sistema
Salud por fuente con sparklines y tasa de captura de campos (qué fuente aporta sueldo/techs/modalidad), historial de versiones de score (`score_versions`) y botón informativo con el comando `rescore`.

## 8. Diseño y calidad

- Sistema de diseño propio mínimo: tokens CSS (los de `app.css` ya sirven de base), tipografía del sistema, escala de espaciado, componentes: Chip, Badge, Facet, Card, DataTable, Drawer, Tooltip, EmptyState, Skeleton.
- Gráficos con paleta daltónica-segura y mismas variables claro/oscuro; texto alternativo/tabla de datos accesible por gráfico.
- Accesibilidad: foco visible, navegación por teclado completa, contraste AA, `prefers-reduced-motion`.
- Rendimiento: presupuesto inicial < 250 KB gz de JS (gráficos en chunks cargados al entrar a Análisis), primera pantalla útil < 1 s en LAN, filtros < 50 ms con 5k filas.
- Honestidad estadística: mostrar `n` y datos faltantes en cada gráfico ("38% sin modalidad"), no extrapolar con muestras pequeñas.

## 9. Pruebas

- Python: pytest para API y materialización (fixtures con DB de ejemplo); tests de migración idempotente; se mantienen los de seguridad (CSRF, sesión, 401 sin cookie, cabeceras).
- Front: Vitest (stores, filtros, agregaciones) + Playwright e2e (ya disponible en el entorno): login, filtrar, cambiar vistas, kanban, explorador, móvil.
- Contrato: esquema OpenAPI generado → tipos TS (`openapi-typescript`) para que front y back no se desalineen.
- Visual: capturas Playwright por vista (reemplazan `docs/img/web/*.png`).

## 10. Plan por fases

| Fase | Entregable | Esfuerzo aprox. | Criterio de salida |
|---|---|---|---|
| **0. Datos** | Migraciones, `materializar`, `oferta_eventos` y `mercado_diario` acumulando; fix de `years_official`/`staffing`; fallback de modalidad; normalización de empresa/ubicación | 3–4 d | Tablas pobladas tras un barrido; tests verdes. **Empezar ya: la historia se acumula sola** |
| **1. API** | Routers `/api/*`, snapshot columnar, tipos OpenAPI, CSP nueva; legacy bajo `/legacy` | 2–3 d | Contratos testeados; web vieja intacta |
| **2. Esqueleto front** | Vite+Svelte, auth/401, layout, tema, tokens, store de filtros con URL, componentes base | 2 d | Navega y autentica |
| **3. Ofertas** | Filtros facetados, lista virtualizada, tarjetas, maestro-detalle, detalle enriquecido | 4–5 d | Paridad con la web actual + 3 formatos |
| **4. Análisis** | Dashboard con filtro cruzado (volumen, salarios, techs, roles, empresas, condiciones) | 5–6 d | Reemplaza el contenido del PDF |
| **5. Explorador** | Constructor pivot, vistas guardadas, exportes | 3 d | 5 consultas de ejemplo reproducibles por URL |
| **6. Seguimiento** | `estado_oferta`, kanban, notas, comparador, avisos por Telegram | 3–4 d | Flujo guardar→postular completo |
| **7. Pulido y corte** | A11y, rendimiento, e2e, móvil, capturas, README, retiro de `/legacy` | 2–3 d | Presupuestos cumplidos |

Total ≈ 25–30 días-persona. **MVP útil tras las fases 0–3 (~2 semanas)**; el análisis llega en la fase 4. Cada fase se entrega en su propia PR para poder revisar y revertir.

## 11. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Datos esparcidos (22% con sueldo, 38% con modalidad) producen gráficos engañosos | Mostrar `n`/cobertura siempre; mejorar captura en Fase 0; etiquetar inferido vs oficial |
| Poca historia para tendencias | `mercado_diario` desde ya; el front esconde tendencias hasta tener ≥14 días |
| Snapshot completo en el cliente no escala a >20k filas | Paginar por rango de fechas o mover agregaciones al servidor (los endpoints ya lo permiten) |
| Pérdida de seguridad al pasar a JS | CSP sin inline, mismo origen, sin CDN ni terceros, dependencias fijadas, `npm audit` en CI |
| Doble mantenimiento Python+TS | Tipos generados del OpenAPI; lógica de score/sueldo permanece solo en Python |
| Node como requisito de build | Solo en desarrollo; el `dist/` se sirve estático. Alternativa: commitear `dist/` si se prefiere no tener Node en el servidor |

## 12. Decisiones pendientes (del dueño del proyecto)

1. ¿Svelte (recomendado) o React por familiaridad?
2. ¿Quieres seguimiento de postulaciones (escritura en DB) o la web se mantiene solo lectura?
3. ¿`dist/` commiteado o construido localmente?
4. ¿Prioridad: empezar por Fase 0 (acumular historia) en paralelo al diseño visual?
5. ¿Mapa geográfico justifica su costo, o basta con barras por región?
