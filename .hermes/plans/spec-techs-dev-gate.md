# Spec — Detección de techs y dev-gate: regex → IA + título/descripción con word boundaries

**Estado**: v2 — APROBADA CON CAMBIOS (auditoría 2 subagentes: 7 P1 + 10 P2 corregidos) + refinamiento del usuario: regex SOLO en modo degradado (IA apagada)
**Fecha**: 2026-09-05
**Rama**: `feature/canal-market-score`
**Relacionadas**: spec-ia-local v2.1 (IA local), spec-enrich-lotes v2 (lotes), spec-salarios-robustos v2 (árbitro)

## §0 Principios

1. **La IA es la autoridad** para contenido semántico (techs, rol, inglés, seniority) — SIEMPRE que esté activa.
2. **La regex es modo degradado**: solo se ejecuta si la IA está apagada/desactivada (`cfg.ia.enabled=false`). Nunca compite con la IA cuando está activa.
3. **Cero falsos positivos por substring**: toda regex de techs usa word boundaries (`\b`).
4. **El feed es sagrado**: la capa 1 (abreviaturas canónicas del feed machine-readable) NO se toca — es estructurada y confiable.
5. **La IA no puede contradecirse**: rol no-software → techs vacío (guard anti-alucinación).
6. **Los datos ya escritos se limpian** (one-shot) — no esperar a que se regeneren solos.

## §1 Problema

### §1.1 El bug NiFi (raíz)

`enrich.py:212-225` extrae techs de la descripción con **substring sin word boundaries**:

```python
for pat, ab in [("python", "Py"), ..., ("nifi", "NiFi"), ...]:
    if pat in dl:          # ← "nifi" in "plaNIFIcación" → True
        found.append(ab)
```

- `"nifi"` matchea **"plaNIFIcación"** → toda oferta minera de J.E.J Ingeniería ("planificación, control e integración...") recibe `techs: NiFi`
- `"go"` matchea **"cargo"**, **"gobierno"**, **"gestionar"**, **"largo"**, **"obligatorio"** → falsos Go masivos
- `"java"` matchea "javascript" (casi correcto pero impreciso), `"node"` matchea "nodo", `"sql"` matchea "sqlserver" (aceptable), `"aws"` matchea "laws" (raro pero posible)

**Evidencia**: 6 ofertas con `techs=NiFi` — 2 con `ia_model=''` (nunca pasaron por IA → el NiFi vino de la regex), 4 con IA (la regla REFRESCA preservó el falso positivo porque la IA devolvió `[]`).

### §1.2 La IA también alucina

En las fichas 3 y 4 (mineras), la IA **sí** devolvió `NiFi` — no lo heredó de la regex. Un 7B con prompt agresivo puede inventar techs en ofertas no-tech.

### §1.3 Dev-gate por título solo

`channel.py:183-188`: el fallback `is_dev` (sin `rol_categoria`) usa regex SOLO sobre el título. Una oferta "ingeniero de proyectos" cuya descripción diga "desarrollar software de control" no se clasifica dev.

## §2 Diseño

### §2.1 Techs: solo IA + feed; regex SOLO en modo degradado (IA apagada)

**Con IA activa** (`cfg.ia.enabled=true` — el caso normal):
- La regex de la ficha (enrich.py:212-225) **NO se ejecuta** — la IA es la única fuente de techs (regla REFRESCA)
- El feed estructurado se preserva si la IA devuelve `[]` (regla REFRESCA intacta)

**Con IA apagada** (`cfg.ia.enabled=false` — modo degradado):
- La regex corregida con word boundaries se ejecuta como respaldo (para que el canal/reportes no queden vacíos)
- Patrones ampliados (hallazgo P1-3 de auditoría — sin esto se pierde cobertura):

```python
_TECH_PATTERNS = [
    (r"\bpython3?\b", "Py"), (r"\bjava\b", "Java"), (r"\baws\b", "AWS"),
    (r"\bangular(?:js)?\b", "Angular"), (r"\breact(?:js|native)?\b", "React"),
    (r"\bkubernetes\b|\bk8s\b", "K8s"), (r"\bdocker\b", "Docker"),
    (r"\bgolang\b|\bgo\b(?=\s*(?:lang|developer|dev\b|engineer))", "Go"),  # P1-4: no "go live"
    (r"\bnode(?:js|\.js)?\b", "Node"), (r"\btypescript\b|\bts\b", "TS"),
    (r"\bvue(?:js)?\b", "Vue"), (r"\.net\b|\bdotnet\b", ".NET"),
    (r"\bsql\b|\bmysql\b|\bsqlserver\b", "SQL"), (r"\bfastapi\b", "FastAPI"),
    (r"\bdjango\b", "Django"), (r"\bkafka\b", "Kafka"), (r"\bgcp\b", "GCP"),
    (r"\bazure\b", "Azure"), (r"\bscala\b", "Scala"),
    (r"\bspring(?: ?boot)?\b", "Spring"), (r"\bnifi\b", "NiFi"),
    (r"\bterraform\b", "TF"), (r"\bjenkins\b", "Jenkins"),
    (r"\bci[-/]?cd\b", "CI/CD"), (r"\bredis\b", "Redis"),
    (r"\bpostgres(?:ql)?\b", "Postgres"), (r"\bmongo(?:db)?\b", "Mongo"),
    (r"\bjavascript\b|\bjs\b", "JS"),  # P1-3: JS ubicuo en ofertas chilenas
]
```

- **Fuente**: `título + " " + descripción` (título primero — más señal por carácter)
- **Dedupe**: mantener orden de aparición, máx 10 (hoy no deduplica: "kubernetes y k8s" → `K8s;K8s`)
- **Punto de integración** (P1-2): función compartida `_extract_techs(title, desc)` llamada desde `enrich_pending` (donde `r["title"]` existe) y desde `fetch_detail` (laborum.py:138-146) — `extract_structured(url)` no recibe el título
- **`\b` mata el bug de raíz**: "planificación" ya no produce NiFi, "cargo" ya no produce Go

### §2.2 Guard anti-alucinación en `apply_ia_result` (enrich.py:1033-1046)

```python
# Si la IA clasifica el rol como no-software, NO puede haber techs (contradicción)
if parsed.get("rol_categoria") in _NONDEV_CATEGORIES:
    sets.append("techs=?")
    params.append("")
```

- Se aplica ANTES del bloque REFRESCA (línea 1036) — si rol no-software, techs se limpia siempre
- No contradice la regla REFRESCA (aplica a ofertas tech donde la IA devuelve `[]` → preserva)
- `_NONDEV_CATEGORIES` se importa de channel.py (o se define en enrich.py — decidir en implementación para evitar ciclo de imports)

### §2.3 Dev-gate: IA como autoridad; regex SOLO en modo degradado (IA apagada)

**Con IA activa** (`cfg.ia.enabled=true` — el caso normal):
- `rol_categoria` (IA) es la ÚNICA fuente — `_NONDEV_CATEGORIES`/`_DEV_CATEGORIES` deciden (channel.py:175-182 intactos)
- La regex de título (channel.py:183-188) **NO se ejecuta** — sin `rol_categoria` → no dev (espera a que la IA procese)

**Con IA apagada** (`cfg.ia.enabled=false` — modo degradado):
- La regex corregida se ejecuta sobre `título + " " + descripción` (más señal que solo título)
- Word boundaries + lookahead (hallazgo P1-2 de auditoría — sin esto "devengo/devolución" y "desarrollo de proyectos mineros" serían falsos dev):

```python
t = (title or "").lower()
d = (description or "").lower()
if re.search(cfg.relevance.nontech_titles, t, re.I):
    return False
return bool(re.search(
    r"\bdev(?:eloper|ops)?\b|\bdesarroll\w*\b(?=\s+(?:de\s+)?(?:software|aplicaciones|web|backend|frontend|api|sistemas|app))|"
    r"\bsoftware\b|\bbackend\b|\bfrontend\b|\bfull.?stack\b|\bdata\b|\bpython\b|\bjava\b|\bqa\b|\bdevops\b|\bsistemas\b|"
    r"\binformátic\w*\b|\binformatic\w*\b", t + " " + d, re.I))
```

- "desarrollo de proyectos de infraestructura minera" → NO dev (el lookahead exige "software/aplicaciones/web/...")
- "sueldo devengado" → NO dev (`\bdev\b` ya no matchea "devengo")

### §2.4 One-shot de limpieza (falsos positivos ya escritos)

**Corregido por auditoría (P1-5)**: `IN (no-dev)` borraba techs legítimos de 'Seguridad' (categoría DEV en channel.py:123) y NO cubría roles no-software de texto libre (Ingeniería Mecánica, Prevención de Riesgos, Relaciones Laborales — del modo lote). Se invierte: `NOT IN (categorías dev)` — cubre enum + texto libre, preserva dev.

```sql
UPDATE ofertas SET techs=''
WHERE techs != '' AND rol_categoria != ''
  AND rol_categoria NOT IN ('Full Stack','Backend','Frontend','Data','Mobile',
    'AI/ML','Tech Lead','DevOps/Cloud','QA','Software','Seguridad');
```

- Limpia los NiFi mineros, Go de "cargo", etc. (21 ofertas con NiFi verificadas en DB — 5 sin IA)
- Backup previo: `data/backups/ofertas.sqlite.bak-techs-clean`
- NO toca ofertas sin rol_categoria (pueden ser dev legítimas sin IA aún)
- Tradeoff documentado (P2-10): Soporte/TI y Analista/Empresa pierden techs aunque a veces sean dev reales ("ingeniero de soporte en producción" con Py;Django) — decisión deliberada de §0.5

### §2.5 Opinions desactualizadas: salario llega después de la IA (auto-curativo)

**Problema (hallazgo en vivo 2026-09-05)**: "Data Engineer AWS Proyecto Sector Financiero Remoto"
se publicó con `💰 $2.400.000` (feed/trusted) pero la opinion decía "sin salario declarado".
Secuencia: scan inicial sin salario → IA opina "sin sueldo" → `ia_model` se llena → scan
posterior trae el salario del feed → `salary` se actualiza pero la opinion NO se regenera
(`ia_model != ''` la excluye de la cola) → el post muestra el dato crudo + opinion vieja.

**Alcance verificado**: 2 ofertas publicadas con la contradicción (la del ejemplo +
"desarrollador full stack .net|SIIGSA" con $2.000.000 y opinion "Oferta sin salario ni
descripción").

**Regla (en el árbitro, enrich.py:824-847 — corregido por auditoría P1-7)**:
el upsert del scan (db.py:262) solo rellena `salary` si está vacío (la opinion "sin salario"
sería correcta ahí); el ÚNICO lugar donde `salary` pasa de '' a valor real es el árbitro.
Condición de cambio real (sin loop: el salario ya escrito no se reescribe):

```python
# En el árbitro, tras decidir arb_salary: si la oferta tenía salary='' y ahora
# tiene valor real, y la opinion dice "sin salario" → desmarcar para re-enriquecer.
if (not r.get("salary") and arb_salary and r.get("ia_model")
        and re.search(r"sin sueldo|sin salario|no declara|no se declara|carece de datos monetarios",
                      r.get("ai_opinion") or "", re.I)):
    conn.execute("UPDATE ofertas SET ia_model='' WHERE group_id=?", (r["group_id"],))
```

- Mismo patrón que C9 ampliado (spec-enrich-lotes): el dato nuevo re-encola la oferta
- Sin loop: la condición exige `not r.get("salary")` (transición '' → valor) — el salario ya escrito no re-dispara
- Regex ampliada (P2-5): "no informa salario", "sin información salarial", "no menciona el sueldo"

**C9 extendido (corregido por auditoría P1-6)**: la oferta desmarcada con salary+modality
completos NO califica C9 (exige `modality='' OR salary='' OR ...`). Sin esto, el batch
nocturno no la regeneraría — solo `/enrich_all` manual. Extender C9 (enrich.py:1110-1116):

```sql
OR (salary != '' AND (ai_opinion LIKE '%sin salario%' OR ai_opinion LIKE '%sin sueldo%'
     OR ai_opinion LIKE '%no declara%' OR ai_opinion LIKE '%carece de datos monetarios%'))
```

**One-shot (corregido por auditoría P2-3)**: la oferta ejemplo ya tiene opinion regenerada
("Sueldo alineado pero rol no es ideal." — qwen2.5:7b); solo "desarrollador full stack
.net|SIIGSA" conserva la contradicción y ya está con `ia_model=''` (fósil). El one-shot
real es solo `/enrich` (o `/enrich_all`) para SIIGSA — no "desmarcar 2 ofertas".

## §3 Consumidores afectados

| Consumidor | Impacto |
|---|---|
| `channel.py` render (🧰 techs) | Menos falsos positivos → posts más honestos |
| `charts.py` weekly-techs | Datos más limpios → tendencias reales (NiFi minero desaparece) |
| `scoring.py` `_tech_match` (línea 156) | Menos ruido → score más justo |
| `market.py` | Sin impacto directo (usa título) |
| `notify.py` | Sin impacto |

## §4 Riesgos

1. **Pérdida de cobertura**: ofertas sin IA quedan solo con techs del feed (~15%) hasta que la IA las procese. Mitigación: enrich_all + qwen local a 3.5s/oferta → cobertura sube rápido.
2. **`\bgo\b` puede perder Go legítimo** en "golang" (cubierto por `\bgolang\b` primero) o en títulos tipo "Go Developer" (cubierto). Riesgo bajo.
3. **`\bts\b` matchea "ts" en "tsunami"** (raro en ofertas tech). Aceptado — el contexto tech lo hace improbable.
4. **Ciclo de imports** channel↔enrich para `_NONDEV_CATEGORIES`. Mitigación: definir la lista en un módulo compartido (o duplicar la tupla — 7 strings, bajo riesgo de drift).

## §5 Tests (12 nuevos → suite 124)

1. `test_techs_planificacion_no_nifi` — desc con "planificación" → NO NiFi
2. `test_techs_cargo_no_go` — desc con "cargo/gobierno/gestionar" → NO Go
3. `test_techs_go_live_no_go` — "go live"/"go to market" → NO Go (P1-4)
4. `test_techs_javascript_js` — "JavaScript Developer"/"JS" → JS (P1-3)
5. `test_techs_concatenadas` — "nodejs", "reactjs", "mongodb", "springboot", "dotnet", "python3", "ci-cd" → detectadas (P1-3)
6. `test_techs_titulo_java` — título "Senior Java Developer" → Java (título primero)
7. `test_techs_feed_preservado` — feed con techs + IA devuelve [] → feed intacto
8. `test_guard_rol_no_software_limpia_techs` — IA devuelve techs + rol no-software → techs='' (DESPUÉS del REFRESCA — P1-1)
9. `test_is_dev_devengado_rechazado` — "sueldo devengado" → no dev (P2-1)
10. `test_is_dev_desarrollo_minero_rechazado` — "desarrollo de proyectos de infraestructura minera" → no dev (P1-2)
11. `test_is_dev_titulo_descripcion` — título "ingeniero de proyectos" + desc "desarrollar software" → dev
12. `test_salario_llega_desmarca_ia_model` — salary ''→valor + opinion "sin salario" → ia_model='' (P2-8)

## §6 Rollout

1. Implementar §2.1 (`_extract_techs` compartida + patrones `\b` ampliados — solo modo degradado)
2. Implementar §2.2 (guard anti-alucinación DESPUÉS del REFRESCA — P1-1)
3. Implementar §2.3 (dev-gate IA autoridad + regex corregida solo modo degradado)
4. Implementar §2.5 (regla en el árbitro con condición de cambio real + C9 extendido)
5. Tests §5 → suite verde (124)
6. Backup + one-shot §2.4 (SQL invertido — P1-5)
7. Commit + push + restart
8. Validación: `SELECT techs, rol_categoria FROM ofertas WHERE techs LIKE '%NiFi%'` → 0 no-dev (quedan solo dev legítimos)

## §7 Fuera de alcance

- Migrar inglés/seniority del score a IA (deuda documentada — la regex sigue como fallback en modo degradado)
- Eliminar el dev-gate por regex (queda como red de seguridad SOLO en modo degradado — IA apagada)
- Techs del título para el reporte weekly (scoring.py:243) — se puede migrar a la columna `techs` cuando la cobertura IA sea ~100%
