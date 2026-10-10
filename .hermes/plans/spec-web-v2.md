# Spec — Web v2: explorador de ofertas y análisis de mercado

**Fecha:** 2026-10-10 · **Estado:** IMPLEMENTADA en gran parte (ver «Estado de implementación» al final)
**Base:** `docs/PLAN-WEB-V2.md` (visión, stack, fases). Este documento es el contrato ejecutable:
modelo semántico, esquema, motor de exploración, sistema visual, catálogo de visualizaciones,
endpoints, criterios de aceptación, tests y tareas con ID.

**Cambios r2 (vs r1):**
- Nuevo **modelo semántico** (§2): dimensiones tipadas y métricas definidas una sola vez, con
  denominador, `n` mínimo y forma de degradar. Sin esto cada gráfico inventaba su propia cuenta.
- Nuevo **motor de exploración** en el navegador (§6): almacén columnar, filtros por bitset y
  semántica de filtro cruzado precisa.
- Nuevo **sistema visual** (§7): paleta validada contra las superficies reales de la app, asignación
  fija entidad→color, reglas de marcas, tooltips seguros, accesibilidad y tema oscuro.
- Nuevo **catálogo de visualizaciones** (§8): 43 vistas analíticas y 7 micro-visualizaciones con ID, pregunta, forma, datos,
  codificación, interacción, `n` mínimo y qué hacer cuando no hay datos suficientes.
- **Exploración** (§9) rediseñada: filtro cruzado, drill-down, comparar segmentos A/B, gramática del
  Explorador y un recomendador de forma con barandas.
- Fase 0 amplía la capa de datos: `seniority_norm`, `rol_familia`, `mercado_tech_semanal`, snapshot
  con diccionarios.
- Hallazgos nuevos de la DB (§1): con los datos de hoy, casi ningún rol de desarrollo tiene 10
  sueldos. El diseño de los gráficos de sueldo parte de esa escasez, no de un caso ideal.

---

## 0. Principios (pinned)

1. **Aditivo y reversible.** Solo `ALTER TABLE ADD COLUMN` y `CREATE TABLE IF NOT EXISTS`. Telegram,
   daemon y CLI siguen idénticos si la web v2 no existe.
2. **El dato crudo no se destruye** (hereda spec-salarios-robustos §0.2). Lo normalizado es derivado,
   con procedencia.
3. **Una sola fuente de verdad por lógica.** Parseo de sueldo: `scoring._salary_to_clp_monthly`;
   techs: `domain/techs.py`; score: `scoring.py`. El front no reimplementa reglas de negocio. Las
   funciones estadísticas existen en Python y en TS **con vectores de paridad compartidos**.
4. **Honestidad estadística.** Cada número muestra su `n` y su cobertura. Bajo el `n` mínimo de la
   métrica, el gráfico **degrada** (§2.4) en vez de mostrar una conclusión. Nunca se rellena lo que
   falta.
5. **Todo gráfico es una puerta a las ofertas.** Cada marca lleva a la lista filtrada de las ofertas
   que la componen. El análisis no es un callejón sin salida.
6. **La historia sobrevive a la purga.** `/db old|all` (bot.py) hace `DELETE FROM ofertas`; las
   tablas de historia no llevan FK y la purga no las toca.
7. **Seguridad igual o mejor.** Misma auth, `_misma_origen`, bind a loopback; CSP
   `script-src 'self'`; todo texto de ofertas se inserta como texto, también en tooltips y leyendas.
8. **Ningún escritor nuevo bloquea el barrido.** La materialización va en `try/except` con commit
   propio.

---

## 1. Hallazgos de la DB real que condicionan el diseño

Verificados el 2026-10-09 sobre `data/ofertas.sqlite` (1.095 filas, 274 empresas, 12 barridos).

| Hallazgo | Consecuencia |
|---|---|
| **Sueldo declarado en 239 ofertas (22%), concentrado en roles no-dev.** Analista/Empresa 79, Ing. no-software 31, QA 28, Full Stack 22, Backend 8, Data 9, Software 10, Tech Lead 6, DevOps 0, Frontend 0, AI/ML 1 | Los sueldos por rol fino casi nunca alcanzan `n≥10`. Se agrega por **familia de rol** (§2.2), los sueldos se muestran **como puntos** cuando `n<30` (strip plot, cada punto abre su oferta) y los percentiles solo con `n` suficiente |
| Seniority con sueldo: senior 56, junior 31, semi 10, lead 6 | Rol×seniority con sueldo queda casi vacío: se pinta con trama de "sin datos", no con cero |
| `seniority_real` con valores basura (`deseable`, `intermedio`, `mid senior`, `mid-level`, `no`, `noviciado`) y 45% vacío | `seniority_norm` ordinal: `trainee < junior < semi < senior < lead`, más `''` |
| `rol_categoria`: 17 valores (más que los 8 colores posibles) | `rol_familia` de 5 grupos (§2.2) para todo lo que se colorea; el rol fino queda para ejes y filtros |
| `techs` en 41% de las ofertas | La demanda de una tech se calcula **sobre las ofertas con techs conocidas**, no sobre el total (§2.3) |
| `ai_ingles`: desconocido 76%, deseable 20%, requerido 4% | Gráficos de idioma con "desconocido" explícito en gris y cobertura visible |
| `modality` vacío en 62% | `modality_norm` + `modality_source` (oficial/inferida) y "desconocida" siempre visible |
| 11% de las ofertas aparece en ≥2 fuentes (91 en 2, 18 en 3, 8 en 4 o más) | Gráfico de exclusividad de fuentes (V-72): qué fuente aporta ofertas que nadie más tiene |
| `employment_type` mezcla `OTHER`(262) y formatos; `applicants_hint` = `"first 25"`; `location` 35% vacía y con formas mixtas | Normalizadores §3.2 |
| `years_official` y `staffing` sin ninguna fila con valor | Diagnóstico T0-1 antes de usarlos; la UI no los muestra hasta resolverlo |
| `active=0` solo lo pone `enrich.py:405`; no hay cierre por "no vista en N barridos" | Evento analítico `cerrada` (§3.5) |
| 2 días de historia | Los gráficos de tendencia se ocultan hasta tener historia suficiente (§2.4), con contador visible |

---

## 2. Modelo semántico (la capa que comparten API, motor y gráficos)

Vive en `jobhunt/analytics/semantica.py` y se exporta a `frontend/src/lib/semantica.json` (script
T1-7). Un único registro de dimensiones y métricas. El Explorador, los filtros y cada gráfico del
catálogo lo leen. **Ningún gráfico define su propia métrica.**

### 2.1 Dimensiones

| ID | Etiqueta | Tipo | Cardinalidad | Orden | Trabajo de color | Origen |
|---|---|---|---|---|---|---|
| `rol_familia` | Familia de rol | nominal | 5 | fijo (§2.2) | categórico, slots fijos | derivada de `rol_categoria` |
| `rol` | Rol | nominal | 17 | por conteo | ninguno (eje o filtro) | `rol_categoria` |
| `seniority` | Seniority | **ordinal** | 5 + `''` | trainee→lead | rampa ordinal | `seniority_norm` |
| `modalidad` | Modalidad | nominal | 3 + desconocida | remoto, híbrido, presencial | categórico, slots 1–3 | `modality_norm` |
| `fuente` | Fuente | nominal | 8 | fijo (§7.3) | categórico, slots fijos | `source` |
| `encaje` | Encaje IA | **ordinal** | 4 | ninguno→alto | rampa ordinal | `ai_encaje` |
| `ingles` | Inglés | **ordinal** | 3 + desconocido | no→requerido | rampa ordinal | `ai_ingles` |
| `empleo` | Jornada | nominal | 5 | por conteo | ninguno | `employment_norm` |
| `region` / `comuna` | Ubicación | nominal | ~16 / ~60 | por conteo | ninguno | normalizador |
| `empresa` | Empresa | nominal | 274+ | por conteo | ninguno (top-N + Otras) | `company_canon` |
| `tech` | Tecnología | nominal **multivalor** | ~45 | por conteo | ninguno (énfasis perfil) | `oferta_techs` |
| `beneficio` / `alerta` / `a_favor` | Tags IA | nominal multivalor | abierta | por conteo | ninguno | `oferta_tags` |
| `dia` / `semana` / `mes` | Fecha | temporal | — | cronológico | — | `first_seen` (def.) o `date_canonical` |
| `antiguedad` | Antigüedad | cuantitativa (bins) | — | — | secuencial | `age_days` |
| `sueldo` | Sueldo mensual CLP | cuantitativa | — | — | secuencial | `salary_clp` válido |
| `score` / `market_score` | Puntajes | cuantitativa | 0–100 | — | secuencial | columnas |
| `estado` | Mi estado | ordinal | 5 + `''` | guardada→oferta, descartada aparte | rampa ordinal | `estado_oferta` |

Reglas de dimensión:
- **Multivalor** (`tech`, tags): una oferta cuenta una vez por valor. Los totales de una barra de techs
  **no suman** el total de ofertas, y la UI lo dice ("una oferta puede tener varias tecnologías").
- **Alta cardinalidad**: `empresa`, `comuna`, `tech` y tags se muestran como top-N (def. 12) + "Otras
  (k)". "Otras" va siempre al final y en gris.
- **Desconocido**: todo valor vacío se agrupa en "Sin dato", va al final, en gris neutro, y se puede
  ocultar con un interruptor. Ocultarlo actualiza la nota de cobertura.
- **Fecha por defecto = `first_seen`** (cuándo la vio jobhunt, fiable). `date_canonical` es opcional y
  se etiqueta "fecha de publicación declarada (puede faltar o ser imprecisa)".

### 2.2 Taxonomías derivadas (versionadas en `norm_version`)

`rol_familia` (orden fijo = orden de color):

| Familia | `rol_categoria` incluidos |
|---|---|
| Desarrollo | Full Stack, Backend, Frontend, Mobile, Software, Tech Lead |
| Datos e IA | Data, AI/ML |
| Infra y seguridad | DevOps/Cloud, Seguridad, Soporte/TI |
| QA | QA |
| No desarrollo | Analista/Empresa, Ingeniería no-software, No-tech, Profesor/Formación, Otro, `''` |

`seniority_norm`: `trainee` ⇐ {trainee, practicante, noviciado}; `junior`; `semi` ⇐ {semi, semi senior,
mid, mid-level, intermedio}; `senior` ⇐ {senior, mid senior}; `lead` ⇐ {lead, principal, staff,
arquitecto}; el resto ⇒ `''`. Tabla en `analytics/normalizar.py`, con test por cada valor real visto.

### 2.3 Métricas (definición única)

| ID | Definición | Denominador | `n` mín. | Unidad / formato |
|---|---|---|---|---|
| `ofertas` | conteo de ofertas | — | 1 | entero |
| `activas` | `active=1` y sin evento `cerrada` pendiente | — | 1 | entero |
| `nuevas` | `first_seen` dentro del período | — | 1 | entero |
| `cerradas` | eventos `cerrada` dentro del período | — | 1 | entero |
| `pct_con_sueldo` (transparencia) | ofertas con sueldo válido / ofertas | ofertas del grupo | 10 | % |
| `sueldo_p50` / `p25` / `p75` | percentil (interpolación lineal) de `salary_clp` válido | ofertas con sueldo válido | 10 (5 con aviso) | CLP abreviado: `$1,8 M` |
| `sueldo_ic50` | IC 90% de la mediana por bootstrap (B=500, semilla fija) | idem | 10 | rango |
| `demanda_tech` | ofertas que piden la tech / **ofertas con techs conocidas** | ofertas con `techs≠''` | 20 en el grupo | % |
| `lift` | P(A∧B) / (P(A)·P(B)) sobre ofertas con techs | idem | `n(A∧B)≥5` | razón; 1 = independencia |
| `prima_tech` | **estratificada**: en cada estrato `rol_familia×seniority` con ≥3 ofertas con y ≥3 sin la tech, `(p50_con − p50_sin)/p50_sin`; promedio ponderado por `n_con` | ofertas con sueldo válido | `Σ n_con ≥ 10` | % ± IC; etiqueta "indicativa" |
| `score_medio` | media de `score` | ofertas | 5 | 0–100 |
| `pct_encaje_alto` | `ai_encaje='alto'` / ofertas evaluadas | ofertas con encaje | 10 | % |
| `antiguedad_mediana` | mediana de `age_days` | activas | 5 | días |
| `vida_mediana` | mediana de supervivencia **Kaplan-Meier** (aparecida→cerrada; las abiertas son censuradas) | ofertas con evento `aparecida` | 20 y ≥5 cierres | días |
| `pct_multifuente` | ofertas con ≥2 fuentes / ofertas | ofertas | 10 | % |
| `concentracion_top10` | % de ofertas de las 10 empresas con más ofertas | ofertas | 30 | % |

Notas:
- **Sueldo válido** = `salary_clp` entre `ANALYTICS_BAND_MIN` y `ANALYTICS_BAND_MAX` **y**
  `salary_status IN ('trusted','')`. Toda métrica de sueldo informa "n con sueldo / n total".
- La **prima** es la métrica más fácil de malinterpretar: la estratificación evita confundir "Python
  paga más" con "los roles senior piden más Python". Siempre se muestra con IC y `n`, y con el texto
  "correlación, no causa".
- **Supervivencia** porque la mayoría de las ofertas sigue abierta: promediar solo las cerradas
  subestimaría la vida de una oferta.

### 2.4 Degradación por tamaño de muestra (aplica a todo gráfico)

| Situación | Comportamiento |
|---|---|
| `n = 0` | Estado vacío con causa ("ninguna oferta de DevOps declara sueldo") y acción ("quitar filtro X") |
| `n <` mínimo de la métrica | Se muestran **los datos individuales** (puntos/lista), sin agregado. Rótulo "muestra insuficiente para resumir (n=7, mín. 10)" |
| mínimo ≤ `n` < 2×mínimo | Agregado + marca "muestra chica" (texto, no solo color) e IC visible si existe |
| Celda de heatmap bajo el mínimo | Celda con trama de "sin datos" y tooltip con `n`; **nunca** coloreada como cero |
| Historia insuficiente | Gráficos temporales ocultos con "acumulando historia: 3/14 días". Umbrales: series diarias 7 días, semanales 4 semanas, tendencias de tech 6 semanas, supervivencia 20 ofertas y 5 cierres |

---

## 3. Fase 0 — Capa de datos

### 3.1 Migraciones (en `db.init_db`, patrón `PRAGMA table_info`)

Columnas nuevas en `ofertas`:

| Columna | Tipo | Valores | Origen |
|---|---|---|---|
| `salary_clp` | INTEGER NULL | mensual CLP | `_salary_to_clp_monthly(salary, description)` |
| `salary_clp_min` / `salary_clp_max` | INTEGER NULL | | = `salary_clp` salvo rango declarado |
| `salary_norm_src` | TEXT | `salary`·`description`·`''` | |
| `modality_norm` / `modality_source` | TEXT | §2.1 / `oficial`·`inferida`·`''` | §3.4 |
| `seniority_norm` | TEXT | §2.2 | `seniority_real` (+`seniority_oficial` si el anterior está vacío) |
| `rol_familia` | TEXT | §2.2 | `rol_categoria` |
| `employment_norm` | TEXT | `completa`·`parcial`·`contrato`·`practica`·`otro`·`''` | `employment_type` |
| `applicants_n` | INTEGER NULL | | `applicants_hint` |
| `region` / `comuna` | TEXT | | normalizador |
| `company_canon` | TEXT | | normalizador |
| `n_fuentes` | INTEGER | ≥1 | `sources` |
| `norm_version` | TEXT | `n1`… | subirla fuerza re-derivar |

Tablas nuevas:

```sql
CREATE TABLE IF NOT EXISTS oferta_techs (
  group_id TEXT NOT NULL, tech TEXT NOT NULL,           -- nombre canónico (Python, no Py)
  PRIMARY KEY (group_id, tech)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_ot_tech ON oferta_techs(tech);

CREATE TABLE IF NOT EXISTS oferta_tags (
  group_id TEXT NOT NULL,
  tipo TEXT NOT NULL,        -- beneficio | rojo | verde | idioma
  valor TEXT NOT NULL,       -- idioma: "ingles:avanzado:excluyente"
  PRIMARY KEY (group_id, tipo, valor)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS oferta_eventos (            -- SIN FK, sobrevive a purgas
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, scan_id INTEGER,
  group_id TEXT NOT NULL, title TEXT DEFAULT '', company TEXT DEFAULT '',
  rol_familia TEXT DEFAULT '', fuente TEXT DEFAULT '',  -- copia: permite análisis tras purga
  tipo TEXT NOT NULL,        -- aparecida|reaparecida|cerrada|sueldo|score|encaje|modalidad
  antes TEXT DEFAULT '', despues TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_ev_gid ON oferta_eventos(group_id, ts);
CREATE INDEX IF NOT EXISTS idx_ev_ts ON oferta_eventos(ts);

CREATE TABLE IF NOT EXISTS mercado_diario (
  fecha TEXT NOT NULL,
  rol_familia TEXT NOT NULL DEFAULT '*', seniority TEXT NOT NULL DEFAULT '*',
  fuente TEXT NOT NULL DEFAULT '*', modalidad TEXT NOT NULL DEFAULT '*',
  n_activas INTEGER, n_nuevas INTEGER, n_cerradas INTEGER,
  n_con_sueldo INTEGER, sueldo_p25 INTEGER, sueldo_p50 INTEGER, sueldo_p75 INTEGER,
  PRIMARY KEY (fecha, rol_familia, seniority, fuente, modalidad)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS mercado_tech_semanal (       -- tendencias de tecnologías
  semana TEXT NOT NULL,                                -- ISO 'YYYY-Www'
  tech TEXT NOT NULL, rol_familia TEXT NOT NULL DEFAULT '*',
  n_activas INTEGER, n_nuevas INTEGER, n_base INTEGER, -- n_base = activas con techs conocidas
  PRIMARY KEY (semana, tech, rol_familia)) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS estado_oferta (
  group_id TEXT PRIMARY KEY,
  estado TEXT NOT NULL,     -- guardada|postulada|entrevista|oferta|descartada
  nota TEXT DEFAULT '', actualizado TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS vistas_guardadas (
  id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL UNIQUE,
  tipo TEXT NOT NULL,       -- ofertas | analisis | explorador
  spec TEXT NOT NULL,       -- JSON: filtros (+ spec del Explorador, §9.4)
  creada TEXT NOT NULL);
```

- `mercado_diario` usa `'*'` = todas. Combinaciones por día: total, cada dimensión sola, y
  `rol_familia×seniority`. ~60 filas/día. Percentiles solo con `n_con_sueldo ≥ 5`, si no NULL.
- `mercado_tech_semanal`: top-40 techs × (total + 5 familias). ~240 filas/semana.
- Ambas son **fotos acumuladas**: se recalcula el día/semana en curso; los cerrados no se tocan. Son
  la única fuente de tendencias (el snapshot solo conoce el presente y lo que no se purgó).

### 3.2 `jobhunt/analytics/normalizar.py` (funciones puras)

```
norm_modalidad(texto) -> 'remoto'|'hibrido'|'presencial'|''
norm_seniority(real, oficial) -> enum §2.2
norm_rol_familia(rol_categoria) -> enum §2.2
norm_empleo(texto) -> enum
norm_applicants(texto) -> int|None        # "first 25" -> 25 ; "over 200 applicants" -> 200
norm_ubicacion(texto) -> (region, comuna)
norm_empresa(texto) -> str                # minúsculas, sin tildes, sin SpA/Ltda/S.A./Chile
sueldo_rango(salary, description) -> (min|None, max|None, src)
```

- `sueldo_rango` delega en `_salary_to_clp_monthly`. Detecta rango solo con patrón explícito
  (`N - M`, `N a M`, `entre N y M`); si un extremo falla ⇒ `(valor, valor, src)`. Fuera de banda no se
  descarta (principio 2): la exclusión es responsabilidad de la métrica (§2.3).
- `norm_ubicacion`: diccionario estático `domain/geo_cl.py` (16 regiones, comunas de la RM y capitales
  regionales). Quita `Chile`, `Región`, `Metropolitan`; gana la comuna conocida más larga;
  `Santiago Centro`→(`Metropolitana`,`Santiago`); `remoto|LatAm|Latin America` ⇒ `region='remoto'`;
  sin match ⇒ `('desconocida','')`.
- `norm_empresa` admite alias manuales en `analytics/empresa_alias.json` (v1 vacío).

### 3.3 `jobhunt/analytics/materializar.py`

```
materializar(conn, cfg, *, scan_id=None, full=False) -> dict
```

Pasos idempotentes, con una transacción por paso:
1. Derivar columnas en filas con `norm_version != VERSION` o `updated_at > ultima_materializacion`
   (todas con `full=True`), en lotes de 500.
2. `oferta_techs` (`NAME_BY_ABBR`; sin canónico se conserva la abreviatura). Se reemplaza solo si
   cambió el conjunto.
3. `oferta_tags` vía `json_each` de `ai_benefits`, `ai_red_flags`, `ai_green_flags` y `ai_idiomas`.
   Trim, ≤120 chars, minúsculas; si el JSON es inválido se registra en el log y se salta.
4. Eventos (§3.5).
5. `mercado_diario` del día y `mercado_tech_semanal` de la semana (UPSERT).
6. Guarda `{ts, filas, ms}` de la corrida en una tabla `analytics_meta` (clave/valor) para la
   observabilidad.

Hook: al final de `cmd_run`, tras `rescore_all` y antes del digest, en `try/except → log.warning`.
Comando manual: `python -m jobhunt materialize [--full]`. Debe tardar menos de 2 s con 5k filas.

### 3.4 Inferencia de modalidad (fallback)

Solo si `modality=''`. Reglas sobre título + primeros 1.500 chars de la descripción, normalizados sin
tildes:
1. `híbrido|hibrido|hybrid|(\d) ?días? (en|de) (la )?oficina` ⇒ `hibrido`
2. `100% remot|full remote|remoto total|trabajo remoto|work from home|teletrabajo` ⇒ `remoto`
3. `presencial|on-?site|en oficina` ⇒ `presencial`
4. `remote_official=1` ⇒ `remoto`
5. Si hay reglas contradictorias ⇒ `''`.

Se marca `modality_source='inferida'`. Nunca se sobrescribe `modality`, porque el score la usa. Los
gráficos de modalidad permiten separar oficial e inferida (trama en la parte inferida, §7.5). T0-5
mide la precisión sobre 50 filas etiquetadas a mano; con menos de 85% se apaga la inferencia vía
`ANALYTICS_INFER_MODALITY=false`.

### 3.5 Eventos y definición de "cerrada"

| Evento | Regla |
|---|---|
| `aparecida` | primera vez que se materializa la oferta (`antes=''`, `despues=first_seen`) |
| `reaparecida` | `active` pasa de 0 a 1, o `last_seen` es posterior a un `cerrada` |
| `cerrada` | `active=0`, **o** `last_seen` más antiguo que los últimos `ANALYTICS_CLOSE_AFTER_SWEEPS` (=6) barridos **en que su fuente principal rindió n>0** |
| `sueldo` | cambió `salary_clp` |
| `score` | \|Δscore\| ≥ 10 entre materializaciones |
| `encaje` / `modalidad` | cambió el valor no vacío |

El último valor conocido se lee con `ORDER BY id DESC LIMIT 1` por `(group_id, tipo)`. Una fuente
caída no genera cierres. El evento no cambia `active`: es solo analítico, y la UI dice "posiblemente
cerrada". En el backfill se escribe `aparecida` para todas las filas con `ts=first_seen`; la historia
anterior no se puede reconstruir, y se documenta así.

### 3.6 Diagnósticos de captura

- **T0-1** `years_official` y `staffing` en 0: encontrar la causa y corregirla, o darlas de baja.
- **T0-2** Informe de cobertura por fuente×campo. Es la base de V-71 y orienta qué extractor mejorar.

### 3.7 Config

`ANALYTICS_ENABLED=true`, `ANALYTICS_CLOSE_AFTER_SWEEPS=6`, `ANALYTICS_INFER_MODALITY=true`,
`ANALYTICS_BAND_MIN=300000`, `ANALYTICS_BAND_MAX=15000000`, `ANALYTICS_BOOTSTRAP_B=500`. Bloque
`AnalyticsCfg` con el patrón de `WebCfg`.

---

## 4. Fase 1 — API JSON

`jobhunt/web/api/` con `APIRouter(prefix="/api")`. Todas las rutas exigen sesión mediante la
dependencia `exigir_sesion` (401 JSON `{"error":"sin_sesion"}`). `Cache-Control: no-store`, salvo el
snapshot (ETag). Esquemas Pydantic v2. OpenAPI solo en desarrollo; se exporta a
`frontend/openapi.json` para generar los tipos.

| Método y ruta | Parámetros | Respuesta |
|---|---|---|
| `GET /api/snapshot` | `activas=1`, `desde` | §4.1 |
| `GET /api/semantica` | | dimensiones y métricas de §2 (el front lo usa en tiempo de ejecución; el JSON del build es solo un respaldo) |
| `GET /api/oferta/{gid:path}` | | §4.2 |
| `GET /api/historia/diaria` | `metricas`, `por` ∈ dims de `mercado_diario`, `desde`, `hasta` | `{fechas, series:[{clave, valores, n}], dias_historia}` |
| `GET /api/historia/techs` | `techs[]`, `rol_familia`, `desde` | series semanales de `demanda_tech` con `n_base` |
| `GET /api/historia/eventos` | `tipo`, `desde`, `hasta`, `limite≤500` | eventos (para V-61, V-62 y el feed de cambios) |
| `GET /api/historia/supervivencia` | filtros por dims de evento | curva KM `{t, s, en_riesgo, cierres}` + mediana con IC |
| `GET /api/fuentes` | `barridos=30` | salud por barrido + cobertura de campos + última materialización |
| `GET /api/perfil` | | `{techs(canónicas), roles, salary_min, salary_max, years_exp, min_fit}` |
| `PUT/DELETE /api/estado/{gid:path}` | `{estado, nota}` | Fase 6 |
| `GET/POST/DELETE /api/vistas` | | CRUD de vistas guardadas |

Las métricas del presente (conteos, percentiles, lift, prima) **las calcula el motor del navegador**
(§6) sobre el snapshot, porque deben reaccionar a los filtros al instante. El servidor solo calcula lo
que el snapshot no tiene: historia, supervivencia y eventos.

### 4.1 Snapshot columnar con diccionarios (`v=2`)

```json
{ "v": 2, "generado": "…", "n": 1093,
  "dicts": { "empresa": [...], "fuente": [...], "rol": [...], "tech": [...], "tag": [...], "region": [...], "comuna": [...] },
  "cols": {
    "id": [...], "titulo": [...], "url": [...],
    "empresa": [idx...], "fuente": [idx...], "fuentes": [[idx...]...], "rol": [idx...],
    "rol_familia": [...], "seniority": [...], "modalidad": [...], "modalidad_src": [...],
    "empleo": [...], "region": [idx...], "comuna": [idx...],
    "sueldo": [int|null...], "sueldo_min": [...], "sueldo_max": [...], "sueldo_valido": [0|1...],
    "techs": [[idx...]|null...], "beneficios": [[idx...]...], "alertas": [[idx...]...], "a_favor": [[idx...]...],
    "score": [...], "market_score": [...], "encaje": [...], "ingles": [...],
    "first_seen": ["YYYY-MM-DD"...], "fecha_pub": [...], "antiguedad": [...],
    "applicants": [...], "n_fuentes": [...], "active": [...], "posible_cerrada": [...],
    "resumen": ["≤240 chars"...], "estado": [...] } }
```

- Columnar con diccionarios: el cliente arma arreglos tipados sin transformar (§6.1).
- Sin `description` ni `ai_opinion`; esos van en el detalle.
- `null` = dato ausente, distinto de 0 o lista vacía.
- gzip, `ETag = sha1(max(updated_at), n, v, norm_version)`, 304 si no cambió.
- Tope de 20.000 filas; si hay más ⇒ `truncado:true` y el cliente filtra por `desde`.
- Presupuesto: ≤150 KB gzip con 1.100 filas (medido en T1-2: 123 KB gz / 607 KB crudo, 84 ms).

### 4.2 Detalle de oferta

Ficha actual completa + `desglose`/`mdesglose` como `[{clave, etiqueta, valor}]` (con `_etiqueta`),
`description`, tags completos, `eventos` (últimos 50) y
`fuentes_detalle` (cada fuente con su URL si existe). Los comparables **no** se calculan aquí: los
calcula el motor con el mismo snapshot, para que coincidan con lo que muestra Análisis (V-90…V-93).

### 4.3 Seguridad de la API

- Escrituras: `_misma_origen` + cabecera obligatoria `X-JH: 1` + `Content-Type: application/json`.
  Sin CORS.
- Límites: nota ≤2.000, nombre de vista ≤60, spec ≤4.000, ≤50 vistas. La `spec` se valida contra un
  esquema Pydantic (no se guarda JSON arbitrario).
- `por`, `metricas` y `tipo` contra whitelist sacada de `semantica.py`; nunca se interpolan en SQL.
- CSP de la SPA: `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:;
  connect-src 'self'; font-src 'self'; worker-src 'self'; form-action 'self'; frame-ancestors 'none';
  base-uri 'none'`. `blob:`/`data:` son para exportar PNG; `worker-src` es para el Web Worker del motor.
- `/legacy/*` conserva la CSP actual, sin JS.

### 4.4 Servido de la SPA

`/` y cualquier ruta no-API ⇒ `dist/index.html`, solo con sesión (sin sesión, `acceso.html` 401 como
hoy). `/assets/*` llevan hash y `immutable`. `/login` y `/salir` no cambian. Si falta `dist/`, se
sirve la vista Jinja (modo degradado + log).

---

## 5. Fase 2 — Esqueleto del front

```
frontend/src/
  main.ts  App.svelte  styles/{tokens.css, base.css, viz.css}
  lib/api.ts            # fetch tipado, 401 -> /login, X-JH en escrituras
  lib/tipos.ts          # generado de openapi (no editar)
  lib/semantica.ts      # registro de dims/métricas (§2) + respaldo JSON
  motor/                # §6: almacen.ts, filtros.ts, agregar.ts, estadistica.ts, worker.ts
  estado/               # stores: datos, filtros, seleccion, ui, historial (deshacer)
  viz/                  # §7: tema.ts, Grafico.svelte, Tooltip.svelte, Leyenda.svelte, TablaDatos.svelte,
                        #     Kpi.svelte, Sparkline.svelte, Estados.svelte, formas/*.ts (una por forma)
  catalogo/             # §8: un módulo por gráfico V-xx: {id, pregunta, requiere, datos(), opciones()}
  rutas/{Inicio,Ofertas,Oferta,Analisis,Explorador,Seguimiento,Fuentes}.svelte
  componentes/          # Chip, Facet, DataTable, Card, Drawer, Combobox, RangoFechas, Slider, EmptyState
```

- Router por History API; el estado completo vive en la URL.
- `vite build` ⇒ `jobhunt/web/dist`, sin `eval` ni código inline.
- `svelte-check`, ESLint (con regla que prohíbe `{@html}` e `innerHTML`), Prettier y Vitest.
- Presupuesto: JS inicial ≤120 KB gz; ECharts en un chunk aparte (≤250 KB gz) cargado con `import()`.

---

## 6. Motor de exploración en el navegador

Por qué uno propio: con 1–20k filas, arreglos tipados y bitsets filtran y agregan en milisegundos, sin
dependencias pesadas. DuckDB-WASM pesa varios MB; Arquero y crossfilter2 no manejan bien nuestras
dimensiones multivalor ni la semántica de "un gráfico no se filtra a sí mismo". El costo es ~600 líneas
con tests.

### 6.1 Almacén

- Columnas categóricas ⇒ `Uint16Array` de índices de diccionario. Numéricas ⇒ `Float64Array` con `NaN`
  como ausente. Multivalor ⇒ formato CSR (`offsets: Uint32Array`, `valores: Uint16Array`).
- Índice invertido por valor para las dimensiones de filtro frecuente (`tech`, `rol`, `fuente`), para
  armar un bitset sin recorrer todo.
- Construcción < 30 ms con 5k filas. Se rehace solo cuando cambia el ETag.

### 6.2 Filtros y filtro cruzado

- Cada filtro activo es un bitset (`Uint32Array`) por dimensión. Máscara global = AND de todos.
- **Regla de filtro cruzado:** al agregar por la dimensión D, se usa la máscara de **todos los filtros
  menos el de D**. Así un gráfico de fuentes con "LinkedIn" seleccionado sigue mostrando las demás
  fuentes (en gris) y se puede cambiar o ampliar la selección. Es el comportamiento estándar de
  crossfilter y evita gráficos que colapsan a una barra.
- Multivalor: `techs=[Python, AWS]` con modo `y` (oferta con ambas) u `o` (alguna). Por defecto `o`.
- Rangos (`sueldo`, `score`, `antiguedad`, fechas): bitset por comparación sobre la columna numérica.
- Texto libre `q`: busca en título, empresa y resumen del snapshot. La descripción completa se busca
  en el servidor solo si se marca "buscar también en la descripción" (endpoint `GET /api/buscar?q=`,
  que devuelve solo ids; se suma como un filtro más).

### 6.3 Agregación

`agregar({dim, desglose?, metrica, mascara}) -> {grupos:[{clave, valor, n, n_base, ic?, muestra}], cobertura}`

- Implementa exactamente las métricas de §2.3 y sus reglas de `n`, con la degradación de §2.4.
- `estadistica.ts`: `percentil`, `bootstrapMediana` (PRNG con semilla), `kaplanMeier`, `lift`,
  `primaEstratificada`. Cada función tiene un gemelo en `analytics/estadistica.py` y los dos pasan los
  mismos vectores de `tests/fixtures/estadistica.json`.
- Memoización por `(dim, metrica, hash de máscara)`; se invalida cuando cambian los filtros.
- Con más de 10k filas o un bootstrap con B×n > 200k, el cálculo se mueve a un Web Worker y el gráfico
  conserva su render anterior a opacidad reducida mientras tanto (sin parpadeo ni salto de layout).

### 6.4 Rendimiento

Con 5k filas: aplicar un filtro y recalcular **todos los gráficos visibles** < 50 ms en el hilo
principal (sin bootstrap); con bootstrap < 150 ms en el worker. Se mide con un benchmark en Vitest
(T2-6) y es criterio de aceptación.

---

## 7. Sistema visual

### 7.1 Librería y renderizado

- **Apache ECharts 5**, importación por módulos (`echarts/core` + solo los charts y componentes
  usados). Renderer **SVG** por defecto (nítido, accesible, se exporta bien); **canvas** para las
  dispersiones de más de 2.000 puntos.
- Un único tema (`viz/tema.ts`) generado desde los tokens CSS. Ningún gráfico define colores, fuentes
  ni grosores por su cuenta.
- **Tooltip propio**: el tooltip de ECharts se apaga (`tooltip: {show: false}`); la cruz vertical usa
  el componente `axisPointer` (`triggerTooltip: false`) y un componente Svelte `Tooltip` se alimenta de
  los eventos `mouseover`/`globalout`/`updateAxisPointer`. Todo texto se inserta con `textContent`. Esto elimina el riesgo de XSS de los
  `formatter` que devuelven HTML con títulos o empresas, y además deja el tooltip estilado con los
  tokens.
- Spike T2-1: confirmar que ECharts (SVG y canvas) funciona con `style-src 'self'` (solo usa CSSOM,
  que la CSP no bloquea). Plan B: `style-src-attr 'unsafe-inline'`, nunca `'unsafe-inline'` para
  `<style>`.

### 7.2 Paleta (validada contra las superficies reales de la app)

Se adopta la paleta categórica de referencia de 8 colores. Fue validada con el validador de la skill
de visualización contra los paneles de la app (`--panel` claro `#ffffff`, oscuro `#171e27`):

| Slot | Tono | Claro | Oscuro |
|---|---|---|---|
| 1 | azul | `#2a78d6` | `#3987e5` |
| 2 | naranja | `#eb6834` | `#d95926` |
| 3 | aqua | `#1baf7a` | `#199e70` |
| 4 | amarillo | `#eda100` | `#c98500` |
| 5 | magenta | `#e87ba4` | `#d55181` |
| 6 | verde | `#008300` | `#008300` |
| 7 | violeta | `#6250d6` | `#9085e9` |
| 8 | rojo | `#e34948` | `#e66767` |

Resultado (2026-10-10): **claro** pasa banda, croma, CVD entre adyacentes (peor 9,1) y piso de visión
normal (peor 19,6). Hay un aviso de contraste: aqua, amarillo y magenta quedan bajo 3:1 sobre blanco,
así que **son obligatorios rótulos directos o la vista de tabla** (§7.6). **Oscuro** pasa todo (CVD
peor 8,4; contraste ≥3:1). En todos-contra-todos (dispersión) **solo los 3 primeros slots** pasan; las
dispersiones colorean como máximo 3 grupos.

Otras escalas:
- **Secuencial** (magnitud): azul de un solo tono, pasos 100→700 (`#cde2fb` … `#0d366b`).
- **Ordinal** (seniority, encaje, inglés, estado): 4 pasos del azul `#86b6ef, #3987e5, #256abf, #104281`,
  validados con `--ordinal` sobre blanco (monótona, saltos ≥0,06, extremo claro 2,11:1). En oscuro el
  extremo oscuro no pasa de `#184f95`. Para 5 niveles se intercala `#5598e7`; T2-7 lo revalida.
- **Divergente** (prima, lift alrededor de 1, delta vs tu perfil): azul ↔ rojo con punto medio gris
  (`#f0efec` claro, `#383835` oscuro), mismos pasos por brazo.
- **Estado** (salud de fuentes, alertas): reservado y siempre con ícono + texto. Bueno `#0ca30c`, aviso
  `#fab219`, serio `#ec835a`, crítico `#d03b3b`. Reemplaza `--ok`, `--medio` y `--mal` dentro de los
  gráficos.
- **Neutros**: "Sin dato", "Otras" y lo deseleccionado van en gris (`#898781`), nunca en un slot.
- La paleta se valida en CI: `frontend/scripts/validar_paleta.mjs` (copia del validador) corre en
  `npm run check` para los dos modos. Si alguien cambia un hex y falla, el build falla.

### 7.3 Asignación fija entidad → color

El color sigue a la entidad, no al ranking: filtrar no repinta a los que quedan.

| Dimensión | Asignación |
|---|---|
| `rol_familia` | Desarrollo 1 · Datos e IA 2 · Infra y seguridad 3 · QA 4 · No desarrollo **gris** (contexto, no protagonista) |
| `fuente` | linkedin 1 · computrabajo 2 · laborum 3 · indeed 4 · jooble 5 · glassdoor 6 · aira 7 · accenture 8. Una fuente nueva ⇒ "Otras" hasta que se le asigne un slot a mano |
| `modalidad` | remoto 1 · híbrido 2 · presencial 3 · desconocida gris |
| ordinales | rampa ordinal, nunca slots categóricos |
| `tech`, `empresa`, `rol` | sin color de identidad: barras en slot 1 y **énfasis** cuando corresponde (§7.4) |

Con más de 4 series en un mismo gráfico, rótulos directos obligatorios. Más de 8 nunca: el resto se
pliega en "Otras".

### 7.4 Énfasis: el modo "tú vs el mercado"

Muchas preguntas de jobhunt son "¿dónde estoy yo?". Para eso se usa **énfasis** (un color y el resto
en gris), no un color por categoría:
- Techs de tu perfil (`/api/perfil`) en slot 1; el resto en gris. Aplica a V-20, V-21 y V-25.
- Tu rango salarial como banda sombreada (`salary_min`–`salary_max`) en V-10, V-11 y V-80.
- La oferta abierta en el detalle como punto destacado entre sus pares (V-90).
- Interruptor global "resaltar mi perfil", activo por defecto.

### 7.5 Marcas

- Barras finas con extremo redondeado de 4 px anclado a la línea base; separación de 2 px de superficie
  entre rellenos (barras adyacentes y segmentos apilados), sin bordes.
- Líneas de 2 px; marcadores ≥8 px con anillo de superficie de 2 px cuando se superponen.
- Rejilla y ejes en líneas sólidas finas (sin guiones), un tono sobre la superficie.
- **Nunca doble eje Y.** Dos medidas de distinta escala ⇒ dos gráficos o múltiplos pequeños.
- Rótulos selectivos: extremos, el último punto, la serie que importa. Nunca un número en cada punto.
- El texto usa tokens de texto, nunca el color de la serie.
- **Trama** (ECharts `aria.decal`, líneas a 45°/135°) solo para: el modo accesible, la impresión/
  exportación, `forced-colors`, celdas "sin datos" y la parte inferida de modalidad.
- Cifras tabulares (`tabular-nums`) en tablas y ejes; cifras proporcionales en KPI.
- Formato chileno: `$1,8 M`, `$850 mil`, `12,5%`, separador de miles con punto. Las fechas usan
  `Intl.DateTimeFormat('es-CL')`.

### 7.6 Anatomía e interacción comunes (componente `Grafico.svelte`)

Todo gráfico del catálogo se monta en el mismo contenedor, que aporta:
1. **Título como pregunta** ("¿Qué tecnologías se piden más?") y subtítulo con la métrica exacta.
2. **Pie de cobertura:** `n=412 de 1.093 · excluidas 681 sin techs conocidas`, con enlace "¿por qué?"
   que explica la definición de §2.3.
3. **Tooltip** por marca (barras, celdas, puntos) o con cruz vertical (líneas y áreas). El valor va
   primero y en negrita, la etiqueta después, con `n` siempre. Zona de toque ≥24 px; en dispersiones,
   el punto más cercano.
4. **Clic = seleccionar** (filtro cruzado, §9.1). **"Ver N ofertas"** en el tooltip y en el menú.
5. **Menú ⋯:** ver como tabla, descargar CSV, descargar PNG (SVG→PNG con el tema activo), copiar
   enlace, abrir en el Explorador (con la spec equivalente precargada).
6. **Vista de tabla** accesible en todos los gráficos (alternativa obligatoria, §7.2).
7. Leyenda siempre que haya ≥2 series; la marca de la leyenda imita la marca del gráfico.
8. **Estados:** cargando (render anterior atenuado), vacío (§2.4 con causa y acción), muestra
   insuficiente (datos individuales), error (mensaje y reintento).
9. **Teclado:** el gráfico es enfocable; las flechas recorren las marcas y muestran el tooltip; `Enter`
   selecciona; `Esc` limpia. ECharts `aria.enabled` genera la descripción para lectores de pantalla.
10. **Altura** que incluye la banda del eje X (sin scroll interno), responsive con `ResizeObserver`.

---

## 8. Catálogo de visualizaciones

Cada entrada es un módulo en `catalogo/` con su test de datos (§12). Columnas: **forma**, **datos**
(métrica sobre dimensión, desde el motor `[M]` o la historia del servidor `[H]`), **codificación**,
**`n` / degradación**. "Clic" se refiere siempre a §9.1.

### 8.1 Inicio — "¿Qué cambió y qué hago hoy?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-01 | Panorama | **Fila de KPI** (5 tiles): activas, nuevas 7d (delta vs 7d previos), mediana de sueldo de tu familia de rol, transparencia, encaje alto | [M] + [H] | valor grande, delta con flecha + texto, sparkline 14d | delta y sparkline ocultos con <14 días; la mediana muestra "n=…" o "sin datos suficientes" |
| V-02 | ¿Cuántas llegan cada día? | columnas por día (90 días) | [H] `nuevas` por `dia` | slot 1; la media de 7 días como línea en el mismo eje | ≥7 días |
| V-03 | ¿Qué es lo mejor para mí hoy? | **lista**, no gráfico: top 10 por score con encaje ≥ medio y no descartadas | [M] | tarjeta compacta con mini-glifo de sueldo (V-94) | — |
| V-04 | ¿Qué cambió? | feed de eventos: nuevas que encajan, cambios de sueldo, guardadas posiblemente cerradas, fuentes caídas | [H] eventos | íconos de estado + texto | — |

### 8.2 Sueldos — "¿Cuánto se paga y dónde quedo yo?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-10 | ¿Cómo se distribuyen los sueldos? | histograma (bins de $250 mil) con **pincel** de rango | [M] `sueldo` | slot 1; tu rango como banda de énfasis; p25/p50/p75 como líneas con rótulo | <30 ⇒ franja de puntos (uno por oferta) |
| V-11 | ¿Cuánto paga cada familia de rol? | **franja de puntos + caja** horizontal por `rol_familia`, ordenada por mediana | [M] `sueldo` × `rol_familia` | puntos en color de familia, caja gris fina, IC 90% de la mediana como bigote, `n` a la derecha | familia con n<10 ⇒ solo puntos, sin caja; familia con n=0 ⇒ fila "sin sueldos declarados" |
| V-12 | ¿Cuánto sube con la seniority? | **mancuerna** p25–p75 con punto en p50 por seniority (orden ordinal) | [M] | rampa ordinal; desglose opcional por familia como múltiplos pequeños | n<10 por nivel ⇒ puntos |
| V-13 | ¿Remoto paga distinto? | igual a V-11 por `modalidad` | [M] | slots de modalidad; la parte inferida con trama | igual a V-11 |
| V-14 | ¿Quién publica el sueldo? | barras horizontales de `pct_con_sueldo` por fuente / familia / top empresas (selector) | [M] | slot 1; línea de referencia con el promedio global | n<10 por grupo ⇒ barra vacía con trama |
| V-15 | ¿Mi rango es realista? | **bullet chart**: rango p25–p75 del mercado para tu familia y seniority, con tu `salary_min`/`salary_max` encima | [M] | banda gris (mercado), barra de énfasis (tú), texto "tu mínimo está en el percentil 62" | n<10 ⇒ "sin datos suficientes para tu segmento" + el segmento más cercano con datos |
| V-16 | ¿Cómo evolucionó la mediana? | línea de p50 con banda p25–p75 por semana | [H] `mercado_diario` | slot 1; banda clara del mismo tono | ≥4 semanas y n≥10 por punto; si falta, el punto se omite (la línea se corta, no se interpola) |

### 8.3 Tecnologías — "¿Qué se pide, qué paga y qué me falta?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-20 | ¿Qué techs se piden más? | barras horizontales top 25 de `demanda_tech` | [M] | **énfasis**: tus techs en slot 1, el resto gris; rótulo % en las 5 primeras | base <20 ⇒ conteos absolutos en vez de % y aviso |
| V-21 | ¿Qué me falta? | **brecha**: top 15 techs demandadas que no están en tu perfil, con % y mediana de sueldo de las ofertas que las piden | [M] | barras gris + texto; enlace "ver ofertas que piden X" | igual a V-20 |
| V-22 | ¿Qué techs van juntas? | **heatmap** de `lift` entre las 20 techs más pedidas, ordenado por clúster (orden jerárquico simple) | [M] | divergente azul↔rojo con gris en lift=1, escala logarítmica | celdas con n(A∧B)<5 con trama "sin datos" |
| V-23 | ¿Qué tech paga más? | barras divergentes de `prima_tech` con bigotes de IC, ordenadas | [M] | divergente; rótulo "indicativo · correlación, no causa" | Σn_con <10 ⇒ tech omitida, contada en el pie ("12 techs sin datos suficientes") |
| V-24 | ¿Qué techs suben o bajan? | **múltiplos pequeños** (sparklines) de `demanda_tech` semanal, top 16, ordenados por pendiente | [H] `mercado_tech_semanal` | una línea por panel, slot 1 para las tuyas y gris para el resto; eje Y compartido | ≥6 semanas |
| V-25 | ¿Qué stack pide cada familia? | heatmap tech × `rol_familia` con `demanda_tech` dentro de cada familia | [M] | secuencial azul; tus techs marcadas en el eje | familia con base <20 ⇒ columna con trama |

### 8.4 Roles y seniority — "¿Dónde está la demanda?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-30 | ¿Qué roles y niveles se buscan? | heatmap `rol` × `seniority` de conteo | [M] | secuencial, valor dentro de la celda solo si cabe | — |
| V-31 | ¿Cuánto paga cada combinación? | mismo heatmap con `sueldo_p50` | [M] | secuencial; n en el tooltip | celdas n<5 con trama; n 5–9 con marca "muestra chica" |
| V-32 | ¿Qué fuente trae qué tipo de rol? | barras 100% apiladas, `fuente` × `rol_familia` | [M] | slots de familia, gris para "No desarrollo" | — |
| V-33 | ¿Qué tan bien encajan conmigo? | barras 100% apiladas, `rol_familia` × `encaje` | [M] | rampa ordinal de encaje | — |

### 8.5 Empresas — "¿Quién contrata y cómo?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-40 | ¿Quién publica más? | barras top 20 por `ofertas` (activas) | [M] | slot 1; KPI `concentracion_top10` arriba | — |
| V-41 | **Tabla de empresas** | tabla ordenable: ofertas, familias (mini-barra apilada), transparencia, mediana de sueldo (si n≥5), encaje medio, alertas IA frecuentes, última publicación | [M] | sparkbars en celdas, `tabular-nums` | celdas bajo el mínimo como "—" con tooltip |
| V-42 | ¿Quién paga y lo dice? | dispersión: `ofertas` (x, log) vs `pct_con_sueldo` (y), empresas con ≥3 ofertas | [M] | puntos slot 1, rótulo directo en las 8 más grandes; hover por punto más cercano | <3 ofertas excluida y contada en el pie |

### 8.6 Condiciones — "¿Qué piden y qué ofrecen?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-50 | ¿Cuánta oferta remota hay? | barras 100% apiladas `rol_familia` × `modalidad` | [M] | slots de modalidad; inferida con trama; desconocida en gris al final | interruptor "ocultar desconocida" que recalcula y actualiza el pie |
| V-51 | ¿Cuánto pesa el inglés? | barras 100% apiladas `rol_familia` × `ingles` | [M] | rampa ordinal + gris para desconocido | idem |
| V-52 | ¿Qué beneficios se ofrecen? | barras top 15 de beneficios | [M] tags | slot 1 | base = ofertas con beneficios extraídos (13% hoy), visible en el pie |
| V-53 | ¿Qué alertas son comunes? | barras top 15 de alertas IA (`ai_red_flags`) | [M] tags | **color de estado "serio"** con ícono ⚠ (significa riesgo, no identidad) | idem |
| V-54 | ¿Cómo son las jornadas? | barras de `empleo` | [M] | slot 1 | — |
| V-55 | ¿Dónde están? | barras horizontales por región; drill-down a comuna para la RM | [M] | secuencial por conteo; "remoto" separado arriba | — (mapa coroplético fuera de alcance) |

### 8.7 Dinámica del mercado — "¿Se mueve rápido?" (todo requiere historia)

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-60 | ¿Crece o se achica? | áreas apiladas de `activas` por `rol_familia` y día | [H] | slots de familia; cruz con todas las series en el tooltip | ≥7 días |
| V-61 | ¿Cuánto dura una oferta? | **curva de supervivencia KM** (escalones) con banda de IC, opcionalmente por familia (≤3 curvas) | [H] supervivencia | slot 1 (o slots 1–3); mediana marcada con rótulo | <20 ofertas o <5 cierres ⇒ oculto con contador |
| V-62 | ¿Entran más de las que salen? | barras divergentes por semana: nuevas arriba, cerradas abajo, en el mismo eje | [H] | divergente (azul nuevas, rojo cerradas) | ≥4 semanas |
| V-63 | ¿Qué tan frescas son las activas? | histograma de `antiguedad` (bins 0–1, 2–3, 4–7, 8–14, 15–30, >30) | [M] | rampa ordinal | — |
| V-64 | ¿Cuándo publican? | **calendario** (heatmap día × semana) de `nuevas` | [H] | secuencial | ≥4 semanas |

### 8.8 Fuentes y calidad de datos — "¿Puedo confiar en estos números?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-70 | ¿Están vivas las fuentes? | **múltiplos pequeños** por fuente: columnas de ofertas por barrido (30), errores marcados | [H] `scan_log` | slot de la fuente; **ícono + texto de estado** (funcionando / a vigilar / caída) con colores de estado | — |
| V-71 | ¿Qué fuente trae qué datos? | heatmap `fuente` × campo (sueldo, techs, modalidad, ubicación, seniority, descripción, inglés…) con % de cobertura | [M] | secuencial; valor dentro de cada celda | — |
| V-72 | ¿Qué fuente aporta ofertas únicas? | **barras de intersección estilo UpSet**: ofertas solo en X, y combinaciones frecuentes de fuentes (top 10) | [M] `fuentes` | slot 1 + matriz de puntos debajo | — |
| V-73 | ¿Coinciden los dos puntajes? | dispersión `score` × `market_score` (hexbin si n>2.000) | [M] | puntos coloreados por `encaje` con la rampa ordinal; hover del punto más cercano | — |
| V-74 | ¿Está al día la materialización? | estado en texto: última corrida, filas, ms, versión de normalización y de score | [H] | texto + ícono de estado | — |

### 8.9 Tu posición — "¿Dónde están mis oportunidades?"

| ID | Pregunta | Forma | Datos | Codificación | `n` / degradación |
|---|---|---|---|---|---|
| V-80 | **Mapa de oportunidades** | dispersión: x=`score` (fit), y=`sueldo`; las ofertas **sin sueldo** van en un carril aparte bajo el eje (no se descartan ni se inventan) | [M] | color por `encaje` (rampa ordinal); líneas de referencia en `min_fit` y `salary_min` que forman cuadrantes con nombre ("encajan y pagan", …); clic en un punto abre el detalle; pincel 2D para seleccionar | — |
| V-81 | ¿Cuántas encajan conmigo y en qué roles? | barras 100% apiladas `rol` × `encaje` | [M] | rampa ordinal | — |
| V-82 | ¿Cómo va mi búsqueda? | **embudo** guardadas → postuladas → entrevista → oferta | `estado_oferta` | rampa ordinal; tasa de conversión entre pasos en texto | Fase 6 |

### 8.10 Micro-visualizaciones (detalle y listas)

| ID | Dónde | Forma | Codificación |
|---|---|---|---|
| V-90 | Detalle | **sueldo entre pares**: franja de puntos de la misma familia y seniority, con esta oferta destacada y el percentil en texto | énfasis; n<5 ⇒ "pocos pares con sueldo (n=3)" |
| V-91 | Detalle | **cascada** (waterfall) del desglose del score, de la base al total | positivos en azul y negativos en rojo (divergente), etiquetas con `_etiqueta` |
| V-92 | Detalle | **línea de tiempo** de eventos (apareció, cambios de sueldo/score, fuentes) | puntos sobre un eje temporal con texto |
| V-93 | Detalle | techs de la oferta como chips con su `demanda_tech` en mini-barra; las tuyas resaltadas | énfasis |
| V-94 | Listas y tarjetas | **glifo de sueldo**: barra p25–p75 del segmento con un punto para la oferta | gris + punto slot 1; vacío si no hay sueldo |
| V-95 | Listas | barra de score de 0–100 en la celda | secuencial |
| V-96 | Filtros | **mini-histograma** dentro del slider de sueldo, score y antigüedad, que se recalcula con los demás filtros | slot 1 en gris claro |

### 8.11 Organización de la página Análisis

- Pestañas: **Resumen · Sueldos · Tecnologías · Roles · Empresas · Condiciones · Dinámica · Calidad de
  datos · Tu posición**. La pestaña Resumen reúne V-01, V-11, V-20, V-50, V-63 y V-80 en miniatura,
  cada uno con enlace a su pestaña.
- **Una barra de filtros global**, fija arriba (§9.1): rango de fechas primero (preajustes 7 / 30 / 90
  días / todo), después familia, seniority, modalidad, fuente, techs y más filtros. Afecta a todas las
  pestañas, así que todos los números concuerdan.
- Grilla de 12 columnas; en móvil una sola columna, con los heatmaps y las tablas en desplazamiento
  horizontal **dentro de su tarjeta**.
- Cada pestaña empieza con 2–4 KPI de la pestaña y termina con "Ver estas N ofertas".

---

## 9. Exploración

### 9.1 Filtro cruzado y selección

- **Clic** en una marca ⇒ agrega `dim=valor` a los filtros como **chip con origen** ("Fuente: LinkedIn ·
  desde V-32"). **Mayús+clic** suma valores a la misma dimensión. **Pincel** en histogramas, líneas y
  dispersiones ⇒ filtro de rango.
- El gráfico que origina la selección **no se filtra a sí mismo** (§6.2): muestra lo no seleccionado en
  gris para que se pueda ampliar o cambiar.
- Los chips son la única verdad: cerrar un chip quita el filtro y su resaltado en todos lados.
- **Historial:** cada cambio de filtros es una entrada en la URL (atrás/adelante del navegador) más
  `Ctrl+Z`/`Ctrl+Mayús+Z` en la app. Un "Restablecer" con confirmación si hay más de 3 filtros.
- **Barra de resultado** siempre visible: "312 ofertas · 64 con sueldo · Ver ofertas →".

### 9.2 Drill-down

Rutas de profundización definidas en `semantica.py` (`familia → rol → oferta`, `región → comuna`,
`tech → ofertas que la piden`, `empresa → sus ofertas`). Doble clic o el menú "Profundizar" bajan un
nivel: se añade el filtro y se cambia la dimensión del gráfico. Una miga de pan ("Desarrollo › Backend")
permite volver. El último nivel siempre es la lista de ofertas.

### 9.3 Comparar segmentos A/B

Botón "Comparar" que fija el conjunto de filtros actual como **A** y abre un segundo conjunto **B**
(por defecto, el mismo sin el último filtro). Las pestañas pasan a modo comparación:
- KPI en paralelo con la diferencia y su `n`.
- Distribuciones superpuestas (A en slot 1, B en slot 2) o en múltiplos pequeños.
- Barras como **mancuernas** A↔B.
- Prima y diferencias con IC; sin significancia implícita: si los IC se solapan, el texto lo dice.

Casos de uso: "remoto vs presencial", "LinkedIn vs Computrabajo", "Backend senior vs Full Stack senior",
"este mes vs el anterior" (B con otro rango de fechas).

### 9.4 Explorador libre (gramática)

La configuración es una **spec JSON** validada por esquema, serializada en la URL y guardable como vista:

```json
{ "v": 1,
  "filtros": { … },
  "x":      { "dim": "tech", "top": 15, "orden": "valor" },
  "color":  { "dim": "rol_familia" },
  "facet":  { "dim": "seniority", "max": 6 },
  "y":      { "metrica": "demanda_tech" },
  "forma":  "barras",
  "mostrar": { "sin_dato": false, "rotulos": "auto", "tabla": false } }
```

- Dimensiones y métricas solo del registro de §2; la UI muestra a cada una con su descripción.
- **Recomendador de forma**, según los tipos:

  | x | color | métrica | Forma por defecto | Alternativas |
  |---|---|---|---|---|
  | nominal | — | conteo / % | barras horizontales ordenadas | tabla |
  | nominal | nominal | conteo | barras apiladas (100% opcional) | heatmap, múltiplos |
  | ordinal | — | cualquiera | columnas en orden natural | líneas |
  | temporal | — / nominal ≤8 | conteo | líneas (áreas apiladas si es parte del todo) | columnas |
  | nominal | — | percentil de sueldo | franja de puntos + caja | mancuerna |
  | cuantitativa | — | conteo | histograma | franja de puntos |
  | cuantitativa | cuantitativa | — | dispersión | hexbin |
  | nominal × nominal | — | métrica | heatmap | tabla |

- **Barandas** (se deshabilita la opción y se explica el motivo):
  - color con >8 valores ⇒ se ofrecen top-7 + "Otras" o pasar a facet;
  - dispersión con color de >3 grupos ⇒ se ofrece facet;
  - dos métricas con unidades distintas ⇒ dos gráficos, nunca dos ejes;
  - torta: no existe en el Explorador (barras al 100% cubren parte-del-todo);
  - facet con >12 paneles ⇒ top-12;
  - métricas de percentil con n bajo ⇒ degradación de §2.4 en cada panel;
  - una dimensión temporal sin historia ⇒ aviso y contador de días.
- Panel "**Cómo se calculó**": la definición de la métrica, el denominador, filtros activos, filas
  excluidas y por qué.
- Exportar: CSV de la tabla subyacente, PNG, y "copiar spec" para pegarla.
- **Preguntas de ejemplo** precargadas como vistas de solo lectura (sirven también de test e2e):
  1. "Demanda de techs en Desarrollo senior" (V-20 filtrado).
  2. "Mediana de sueldo por modalidad en Datos e IA".
  3. "Transparencia salarial por fuente".
  4. "Empresas con más ofertas remotas".
  5. "Techs más pedidas junto a Python" (filtro tech=Python, x=tech, métrica lift).

### 9.5 Vistas guardadas y enlaces

Todas las páginas (Ofertas, Análisis, Explorador) se pueden guardar con nombre. Una vista guarda
filtros + pestaña o spec. Los enlaces son estables entre versiones porque la spec lleva `v` y hay
migradores en el cliente.

---

## 10. Ofertas (contratos de pantalla)

(a) Cinco vistas conmutables sin recarga ni pérdida de filtros: **lista densa**, **tarjetas**,
**maestro-detalle**, **kanban** (requiere F6) y **comparador** (2–4 ofertas). (b) La misma barra de
filtros y el mismo motor que Análisis: un filtro hecho en Análisis abre Ofertas igual de filtrado, y
viceversa. (c) Facetas con conteos en vivo y mini-histogramas (V-96). (d) Lista virtualizada (<100
filas en el DOM con 5k ofertas), orden por varias columnas y columnas elegibles en `localStorage`
(try/catch). (e) Micro-viz V-94 y V-95 en filas y tarjetas. (f) Agrupar por empresa, rol o fuente con
subtotales. (g) Exportar CSV de lo filtrado, escapando fórmulas (prefijo `'` en celdas que empiezan por
`= + - @`). (h) Teclado: `/`, `j/k`, `Enter`, `Esc`, `s` (guardar), `x` (descartar). (i) Todo el
contenido de ofertas como texto; la descripción con `white-space: pre-wrap`; solo URLs `http(s)`.
(j) **Comparador**: atributos alineados por fila; las celdas que difieren se resaltan; V-90 y V-91 por
oferta; techs en común y distintas.

Detalle de oferta: la ficha actual + V-90…V-93 + "ofertas similares" (Jaccard de techs ≥0,4 en la misma
familia, top 5, calculado por el motor) + estado y notas.

---

## 11. Requisitos no funcionales

| Tema | Criterio medible |
|---|---|
| Backend | `/api/snapshot` <200 ms y `/api/historia/*` <300 ms con 5k filas |
| Motor | §6.4 |
| Render | Cambiar de pestaña de Análisis <300 ms hasta el primer gráfico pintado; sin tareas largas >100 ms en el hilo principal |
| Tamaño | §4.1 y §5 |
| SQLite | Conexiones de lectura cortas; la API no deja transacciones abiertas; la materialización comitea en lotes ≤500 |
| Accesibilidad | WCAG 2.1 AA; todo gráfico con tabla alternativa, foco y navegación por teclado; identidad nunca solo por color (leyenda + rótulos + trama opcional); `prefers-reduced-motion` desactiva animaciones |
| Tema | claro/oscuro/auto, cada uno con su paleta validada (§7.2), no una inversión automática |
| Responsive | Usable desde 360 px; sin scroll horizontal de página |
| Impresión | Hoja de estilos de impresión para Análisis (tema claro + trama) que reemplaza al PDF a mediano plazo |
| Navegadores | Últimas 2 versiones de Chrome, Firefox y Safari |
| Observabilidad | Logs `web:`/`analytics:`; V-74 muestra la última materialización |

---

## 12. Plan de pruebas

**Python (pytest):**
- `test_normalizar.py`: casos reales de §1 (cada valor visto de seniority, ubicación, jornada, sueldo y
  applicants), idempotencia, `rol_familia` completo para los 17 roles.
- `test_estadistica.py`: percentil, bootstrap con semilla, KM (con censura), lift y prima estratificada
  contra `tests/fixtures/estadistica.json` (los mismos vectores que usa Vitest).
- `test_materializar.py`: migración idempotente, techs canónicas, JSON inválido, eventos (aparecida,
  sueldo, cerrada, reaparecida), "fuente caída no cierra", **la purga no toca la historia**,
  re-ejecutar no duplica `mercado_*`.
- `test_api.py`: 401 JSON en **todas** las rutas (parametrizado), snapshot sin descripción y con
  diccionarios coherentes, ETag/304, whitelist (400 ante inyección), CSRF, límites, spec de vista
  inválida ⇒ 422.
- `test_web.py` sigue verde; tests nuevos de la CSP y del modo degradado sin `dist/`.

**Front (Vitest):**
- Motor: bitsets, regla "un gráfico no se filtra a sí mismo", multivalor `y`/`o`, rangos, memoización,
  benchmark de §6.4.
- **Cada módulo del catálogo** (`catalogo/V-xx`) tiene un test de **datos**: dado un snapshot de
  fixture, `datos()` devuelve los grupos, `n` y la degradación esperados, incluidos los casos `n=0` y
  `n` bajo el mínimo.
- Spec del Explorador: validación, recomendador (tabla de §9.4) y barandas.
- Serialización de filtros y specs a la URL, ida y vuelta, y migradores de versión.
- Paleta: `validar_paleta.mjs` en los dos modos (falla el build si falla).

**E2E (Playwright, disponible en el entorno):**
- Login con token de test → Inicio → clic en una barra de V-32 → los chips y todos los gráficos se
  actualizan → "Ver ofertas" abre la lista con los mismos filtros.
- Pincel en V-10 → filtro de rango; deshacer con `Ctrl+Z`.
- Las 5 preguntas de ejemplo del Explorador abren por URL y renderizan.
- Comparar A/B.
- Navegación por teclado en un gráfico (flechas + Enter).
- Móvil 390 px sin scroll horizontal de página.
- Sin peticiones fuera de `self` ni violaciones de CSP en consola.
- **Regresión visual**: capturas por pestaña en tema claro y oscuro (reemplazan `docs/img/web/*.png`).

**Seguridad:** el título `<script>` del fixture actual aparece como texto en lista, tarjeta, detalle,
**tooltip**, **leyenda**, **eje**, **tabla alternativa** y **CSV/PNG exportados**; `javascript:` en `url`;
ESLint prohíbe `{@html}`/`innerHTML`; `npm audit` sin altas.

---

## 13. Criterios de aceptación por fase

- **F0:** la migración corre sobre la DB real sin pérdida (`COUNT(*)` igual, `integrity_check` ok).
  Tras un barrido: `oferta_techs` cubre las ~447 ofertas con techs, `mercado_diario` y
  `mercado_tech_semanal` tienen la fila del período y `oferta_eventos` tiene `aparecida` para todas.
  Materializar <2 s. Ningún test previo roto. La inferencia de modalidad cumple ≥85% o queda apagada.
- **F1:** todas las rutas de §4 con tests verdes; snapshot ≤150 KB gz; legacy intacta en `/legacy`.
- **F2:** SPA con login, motor y benchmark de §6.4 cumplido; CSP sin violaciones; paleta validada en
  CI; un gráfico de prueba (V-20) cumple §7.6 completo (los 10 puntos).
- **F3 Ofertas:** §10 completo; paridad con la web actual.
- **F4 Análisis:** todo el catálogo §8.1–8.9 implementado y con su test de datos; filtro cruzado de
  §9.1; drill-down de §9.2; cada gráfico con tabla alternativa y estados de §7.6.
- **F5 Explorador:** §9.3–9.5; las 5 preguntas de ejemplo como e2e.
- **F6 Seguimiento:** estados, kanban, notas, V-82, avisos.
- **F7:** §11 completo, regresión visual verde, retiro de `/legacy` según §14.

---

## 14. Migración y corte

1. Backup previo obligatorio con `sqlite3 ".backup"` (README).
2. F0 puede salir sola (no cambia nada visible) y **conviene sacarla primero**: cada día sin
   materializar es historia perdida para V-02, V-16, V-24, V-60…V-64.
3. F1 → F2 con la SPA en `/v2` (la vieja sigue en `/`) → F3 paridad → intercambio (`/` = SPA,
   `/legacy` = vieja) → retiro de `/legacy` tras 2 semanas sin uso.
4. Paridad para retirar legacy: filtros actuales (q, min, mod, sueldo, encaje, fuente, inactivas,
   orden), detalle con desglose, estado de fuentes, login/logout, móvil.
5. Rollback: `WEB_UI=legacy|v2` (por defecto `legacy` hasta F3). Las tablas nuevas son inertes.

---

## 15. Tareas (IDs para PRs)

**Fase 0 — datos**
- T0-1 Diagnóstico de `years_official`/`staffing`.
- T0-2 Informe de cobertura fuente×campo (base de V-71).
- T0-3 `domain/geo_cl.py` + tests.
- T0-4 `analytics/normalizar.py` (incluye `seniority_norm` y `rol_familia`) + tests con todos los valores reales.
- T0-5 Muestra etiquetada de 50 filas y medición de la inferencia de modalidad.
- T0-6 `analytics/estadistica.py` + fixture de vectores compartidos.
- T0-7 Migraciones (§3.1) + test de idempotencia y de purga.
- T0-8 `analytics/materializar.py` (pasos 1–6) + hook en `cmd_run` + comando `materialize`.
- T0-9 Eventos y regla de cierre (§3.5) + backfill.
- T0-10 `mercado_diario` y `mercado_tech_semanal`.
- T0-11 `analytics/semantica.py` (registro §2) + `AnalyticsCfg`.

**Fase 1 — API**
- T1-1 Router, sesión JSON, CSRF `X-JH`, OpenAPI en desarrollo.
- T1-2 `/api/snapshot` v2 con diccionarios + ETag + gzip + medición del tamaño.
- T1-3 `/api/oferta/{gid}`.
- T1-4 `/api/historia/{diaria,techs,eventos,supervivencia}`.
- T1-5 `/api/fuentes`, `/api/perfil`, `/api/semantica`, `/api/buscar`.
- T1-6 CSP nueva + servido de la SPA + modo degradado + `/legacy`.
- T1-7 `scripts/export_openapi.py` + export de `semantica.json`.

**Fase 2 — esqueleto, motor y sistema visual**
- T2-1 Scaffold Vite+Svelte+TS+ESLint+Vitest; **spike ECharts + CSP**.
- T2-2 `lib/api.ts` + tipos generados + 401.
- T2-3 Tokens, tema claro/oscuro, layout, router por URL.
- T2-4 Motor: almacén, filtros con bitsets, agregación, memoización.
- T2-5 `motor/estadistica.ts` con paridad Python.
- T2-6 Worker + benchmark §6.4.
- T2-7 `viz/tema.ts` + paleta + `validar_paleta.mjs` en CI (incluye la rampa ordinal de 5 pasos).
- T2-8 `Grafico.svelte` (§7.6), `Tooltip`, `Leyenda`, `TablaDatos`, estados, export PNG/CSV.
- T2-9 Barra de filtros global + chips + historial/deshacer.
- T2-10 E2E base + chequeo de CSP.

**Fase 3 — ofertas:** T3-1 lista virtualizada · T3-2 tarjetas · T3-3 maestro-detalle · T3-4 comparador ·
T3-5 detalle con V-90…V-93 · T3-6 facetas + V-96 · T3-7 export CSV · T3-8 paridad.

**Fase 4 — análisis:** T4-1 Inicio (V-01…V-04) · T4-2 Sueldos (V-10…V-16) · T4-3 Tecnologías (V-20…V-25) ·
T4-4 Roles (V-30…V-33) · T4-5 Empresas (V-40…V-42) · T4-6 Condiciones (V-50…V-55) · T4-7 Dinámica
(V-60…V-64) · T4-8 Calidad (V-70…V-74) · T4-9 Tu posición (V-80, V-81) · T4-10 drill-down ·
T4-11 regresión visual.

**Fase 5 — exploración:** T5-1 spec + validación + migradores · T5-2 recomendador + barandas ·
T5-3 UI del Explorador · T5-4 comparar A/B · T5-5 vistas guardadas · T5-6 preguntas de ejemplo + e2e.

**Fase 6 — seguimiento:** T6-1 `estado_oferta` + API · T6-2 kanban accesible · T6-3 V-82 · T6-4 avisos
por Telegram.

**Fase 7 — pulido:** T7-1 accesibilidad · T7-2 rendimiento · T7-3 impresión · T7-4 retiro de legacy.

---

## 16. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| **Pocos sueldos por segmento** (hoy casi ningún rol dev llega a 10) | Familias de rol, puntos individuales bajo el mínimo, IC por bootstrap, aviso explícito; mejorar la captura de sueldo en las fuentes (T0-2) |
| Lecturas causales de la prima por tech | Estratificación, IC, rótulo "correlación, no causa", oculta bajo el mínimo |
| Heurísticas de normalización que agrupan mal | `norm_version`, alias editables, muestra etiquetada, interruptor para la inferencia |
| `cerrada` mal calculado | Regla "fuente con n>0", umbral configurable, solo analítico |
| Demasiados gráficos y poca señal | Cada gráfico responde una pregunta escrita en su título; Resumen con 6; el resto en pestañas |
| ECharts y CSP / XSS en tooltips | Tooltip propio con `textContent`; spike en T2-1; plan B documentado |
| El motor no escala | Tope de 20k, worker, y las métricas con equivalente en el servidor (`estadistica.py`) si hay que moverlas |
| Divergencia Python↔TS | Vectores compartidos en CI |
| La paleta se degrada con cambios futuros | Validador en CI para ambos modos |
| La escritura (estado/vistas) abre superficie | CSRF doble, esquemas, límites, tests |

## 17. Decisiones abiertas

1. **Stack** (Svelte vs React) — bloquea T2-1. Recomendado: Svelte 5.
2. **¿Web con escritura?** — si no, se omiten `estado_oferta`, F6, V-82 y los PUT (las vistas
   guardadas pueden vivir solo en la URL).
3. **`dist/` commiteado o construido en el servidor.**
4. **Umbral de cierre** (6 barridos ≈ 24 h con intervalo de 4 h).
5. **Banda de sueldo válido** (300 mil–15 M CLP) contra tu criterio actual de `salary_status`.
6. **Familias de rol** (§2.2): ¿te sirve la agrupación o quieres otra? Afecta los colores y casi todos
   los gráficos.
7. **¿Reemplazar el PDF de `/report`** por la impresión de Análisis, o mantener ambos?


---

## 18. Estado de implementación (2026-10-10)

Verificado con: `pytest` (425 tests, incl. 11 e2e con Chromium real), `vitest` (52), `svelte-check` (0 errores) y
`npm run check` (paleta validada en claro y oscuro). Medido sobre la DB real (1.095 ofertas).

### Hecho

| Área | Estado |
|---|---|
| **F0 datos** (§3) | Completa: columnas derivadas, `oferta_techs/tags`, `oferta_eventos`, `mercado_diario`, `mercado_tech_semanal`, `estado_oferta`, `vistas_guardadas`; materialización idempotente (~0,4 s con 1.095 filas) enganchada al barrido; `python -m jobhunt materialize [--full]`; la purga no toca la historia (test). |
| **T0-1** | `years_official` solo viene del JSON-LD (casi nunca): se deriva `exp_anios` del texto (307 ofertas). `staffing` nunca se escribía: ahora se deriva con la misma regla del score (32). |
| **F1 API** (§4) | Completa: snapshot columnar con diccionarios + ETag + gzip, detalle, historia (diaria/techs/eventos/supervivencia KM), fuentes con cobertura, perfil, semántica, buscar, estado y vistas (CSRF doble). |
| **F2 front y motor** (§5–7) | Completa: motor con filtros por máscara, filtro cruzado, agregación con degradación, paridad estadística Python↔TS (vectores compartidos, incl. PRNG y bootstrap), paleta validada en CI, `Grafico` con los 10 puntos de §7.6 salvo el teclado interno (ver pendientes). |
| **F3 ofertas** (§10) | Lista virtualizada, tarjetas, maestro-detalle, kanban (arrastrar/soltar y selector), comparador de 2–4, detalle con sueldo entre pares/cascada/historial, export CSV con escape de fórmulas, atajos `/ j k s x Esc`, mini-histogramas en los rangos (V-96). |
| **F4 análisis** (§8) | V-02, 10–16, 20–25, 30–33, 40–42, 50–55, 60–63, 70–73, 80–81, 24 (con gating de historia). |
| **F5 explorador** (§9.3–9.5) | Recomendador de forma con barandas, spec en la URL, vistas guardadas, 5 ejemplos, comparación A/B de segmentos con veredicto honesto. |
| **F6 seguimiento** | Estados, kanban, notas; avisos de Telegram opt-in (`ANALYTICS_AVISOS_ESTADO`, una vez por oferta). |
| **Corte** (§14) | SPA en `/v2`; `WEB_UI=v2` hace que `/` abra la nueva (la clásica queda en `/?clasica=1`). |

### Cambios respecto al texto de la spec (decisiones tomadas al implementar)

1. **Tooltip**: en vez de un componente Svelte aparte, se usa el tooltip de ECharts con `formatter` que devuelve **nodos DOM** armados con `textContent` (`viz/tip.ts`): mismo objetivo (sin `innerHTML`), menos código. Verificado en e2e con un título hostil.
2. **Eventos**: se agregó la tabla `oferta_prev` (último estado conocido por oferta, sin FK) en vez de leer el último evento por tipo: más simple y permite detectar cambios sin duplicar el baseline.
3. **Snapshot**: presupuesto real ≤150 KB gz (medido 123 KB / 607 KB crudo con 1.093 filas); el de 100 KB de la r1 no era alcanzable con título + URL + resumen de 240 caracteres.
4. **Paleta ordinal oscura**: `#184f95 → #256abf → #3987e5 → #6da7ec` (bajo→alto); la derivada de la clara no pasaba el salto mínimo de luminosidad.
5. **Barras de ranking**: no incluyen la barra «Otras (N)» (dominaba la escala); se informa «N más no se muestran» en el pie.
6. **Vite 8 / TypeScript 6**: `svelte-check` aún no soporta TS 7; se fijó `typescript@~6`.
7. `V-41` (tabla de empresas) se muestra como tabla directa sin gráfico; `V-82` (embudo) no se hizo (ver pendientes).

### Pendiente (no implementado)

- **Drill-down** con miga de pan (§9.2) y **deshacer/rehacer** global con `Ctrl+Z` (hoy: atrás/adelante del navegador).
- **Navegación con flechas dentro de un gráfico** (hoy: foco + Enter para ver la tabla, y la tabla alternativa en todos).
- **V-64** (calendario), **V-82** (embudo de postulaciones), mapa coroplético (fuera de alcance por spec).
- **Tipos TS generados desde OpenAPI** (hoy escritos a mano en `lib/tipos.ts`).
- **Hoja de impresión de Análisis** y retiro del PDF de `/report` (decisión abierta §17.7).
- **Retiro de `/legacy`**: se mantiene la web clásica hasta confirmar paridad en uso real.
- Las tendencias (V-02, V-16, V-24, V-60…V-62) están implementadas pero **ocultas por diseño** hasta acumular 7–42 días de historia (hoy: 1 día).
- Backfill de historia anterior al despliegue: imposible (documentado en §3.5).

### Hallazgos de datos que conviene atacar en las fuentes

- Solo **22 % de las ofertas declara sueldo** (227 con sueldo válido) y **55 % no informa empresa**; `modality` falta en 62 % (la inferencia por texto recupera ~11 ofertas: precisión no medida, ver T0-5 abierta).
- Las etiquetas de la IA mezclan categorías (p. ej. «contrato a plazo fijo» aparece como beneficio, alerta y a favor).
