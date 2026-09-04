# Filtro de relevancia para fuentes tipo-feed (AIRA) — spec v2 (iterada con datos reales)

## Cambios vs v1 (tras 5 iteraciones con los feeds reales)

1. **El match por keywords NO es substring de palabra**: "full" matcheaba "Guardia
   Full Time" y "nifi" matcheaba "Planificación". → phrases + word boundaries `\b`.
2. **El título solo no basta**: de 263 ofertas AIRA, solo 1 matchea frases tech en
   el título. La señal más fuerte es el campo `area`/`subarea` estructurado que
   trae el feed (ej. `data_y_business_intelligence`, `producto_digital`,
   `procesos_estrategia_y_tranformacion_digital`).
3. **La IA para ambiguos es cara e improductiva aquí**: 105 ambiguos → 1 tech
   (los "Encargado de Sala/Encargado AP" de Walmart dominan). → Las áreas
   estructuradas resuelven lo que la IA iba a resolver, gratis.
4. **Los feeds AIRA tienen 2 formatos**: tipo A (`offers[]` directo: cencosud,
   walmart, itaú, bancoestado) y tipo B (`data.offers[]` o `data[]`: entel,
   cencosud genérico, tottus con campos distintos `area_text`/`owner`). El
   parser debe manejar ambos.
5. **El rechazo por título obvio** (guardia/cajera/repositor/vendedor) elimina
   67/263 sin IA.

## Resultados validados (11 feeds, 263 ofertas)

| Capa | Clasifica | Ejemplos |
|---|---|---|
| 0. Área tech estructurada | 12 | "Engineer Manager III, Gerencia Tecnología", "Analista Open Loyalty (data_y_bi)" |
| 0b. Área NO-tech conocida (venta_b2c, seguridad, logística…) | rechazo directo | 100 Guardias, Encargados de Sala |
| 1. Frases tech en título (con `\b`) | 1 | "Business Developer" |
| 1b. Título obvio no-tech | 67 rechazo | Guardia, Cajera, Repositor |
| 2. IA (resto) | ~10-20 de 183 | los "Analista/Ingeniero" sin área clara |
| — | | |

**Cobertura final estimada: 12-25 ofertas indexadas de 263 (5-10%)** — las tech
reales de AIRA. Con área-tech la IA solo procesa el residuo.

## Arquitectura final

```
oferta de feed
      │
      ▼
[0] area/subarea estructurada (si la fuente la trae)
      ├─ área tech conocida (config .env: RELEVANCE_AREAS_TECH) → RELEVANTE
      └─ área no-tech conocida → RECHAZO (sin IA)
      │
      ▼
[1] frases tech en título (\b word boundaries, de QUERIES_*/PROFILE_*)
      ├─ match → RELEVANTE
      └─ no
      │
      ▼
[1b] título obvio no-tech (guardia/cajera/repositor/…) → RECHAZO
      │
      ▼
[2] IA lote (30/call, solo lo ambiguo) → RELEVANTE | RECHAZO
```

## Módulos

### 1. `jobhunt/relevance.py` (NUEVO)
- `build_keywords(cfg) -> list[str]`: frases de QUERIES_* + PROFILE_TECHS + ROLES
  (frases completas, no split por palabra)
- `area_is_tech(area: str, cfg) -> bool|None`: matchea RELEVANCE_AREAS_TECH
  (regex configurable) — None si el área no está ni en tech ni en no-tech
- `title_is_relevant(title, frases) -> bool`: word-boundary match
- `title_is_obvious_nontech(title) -> bool`: regex de cargos obvios (parametrizable)
- `batch_relevance(items, cfg) -> dict[gid, bool]`: lote IA 30/call
- `es_relevante(oferta, cfg) -> (bool, capa)`: orquesta 0→1→1b→2

### 2. `jobhunt/sources/aira.py` (NUEVO)
- `FEEDS`: 11 nombres validados (walmart, cencosud_scotiabank, tottus, entel,
  ripley, bancoestado, itaú, wom, codelco, copec, cencosud) + extra por .env
- Parser dual: tipo A (`offers[]`) y tipo B (`data.offers[]`)
- Normaliza a job estándar: title=name, company=owner/companies o owner_text,
  location=city (limpia "chile##region##ciudad"), modality=remote_work,
  url=link, extra: hire_mode/contract_type/area en _desc para el enrich
- **Aplica relevance.es_relevante inline** — devuelve solo lo relevante

### 3. `jobhunt/cli.py`
- AIRA en el barrido después de Laborum (es barata: 11 GETs)
- on_phase("aira") durante el fetch

## Config (.env)

```
ENABLE_AIRA=true
AIRA_FEEDS=walmart,cencosud_scotiabank,tottus,entel,ripley,itau,bancoestado,wom,codelco,copec
# relevance
RELEVANCE_MODE=hybrid            # keywords | ia | hybrid | off
RELEVANCE_AREAS_TECH=data|digital|tecnolog|software|informatic|sistemas
RELEVANCE_NOUNTECH=guardia|cajera?|repositor|recepcionista|vendedor|operari|bodeguero|conductor|mozo|auxiliar
RELEVANCE_IA_BATCH=30
```

## Edge cases

- Feed vacío o 403: skip con warning (los feeds AIRA se activan/desactivan por empresa)
- Formato B vs A: parser maneja ambos
- IA caída: solo capa keywords+área (modo degradado, sin crashear)
- Ofertas del mismo employer en 2 feeds (jumbo en walmart y cencosud): dedup por id+link en el upsert estándar
- Sector distinto: RELEVANCE_AREAS_TECH es regex en .env — cambiar el .env cambia el gate

## Fuera de alcance v1

- No re-clasifica ofertas ya indexadas (solo gate de entrada)
- No aplica a portales con queries (redundante)