# jobhunt — Ideas / Trabajos pendientes

## Filtro de antigüedad en /jobs (diseñado, por retomar)

**Estado**: diseño cerrado, sin implementar. Conversación 2026-09-14.

### Investigación previa (hecha)
La antigüedad vive en 3 campos:

| Campo | Formato | Qué es | Vacías (activas) |
|---|---|---|---|
| `date_posted` | `YYYY-MM-DD` o `''` | Fecha de la fuente, normalizada por `normalize_date()` (8 formatos: ISO LinkedIn, DD-MM-YYYY Laborum, "Hace X días" Computrabajo, publication_days AIRA, epoch, "hoy", "ayer") | 157/1160 (13.5%) |
| `first_seen` | ISO datetime | Cuándo el bot la vio por primera vez — nunca vacía | 0 |
| `date_canonical` | `YYYY-MM-DD` | Antigüedad canónica: `min(date_posted, first_seen)` con clamp | 0 |

- `date_canonical` clamp: si `date_posted` es más fresca que `first_seen` → clamp a `first_seen` (anti repost-fresh). Sin `date_posted` → usa `first_seen`.
- Se recalcula en cada `rescore_all` (`domain/fechas.py canonical_date()`) + backfill idempotente en `init_db` (db.py ~100).
- Es el MISMO campo que usa el gate del canal (`date_canonical >= date('now','-14 days')`) → consistencia.
- NO usar `date_posted` crudo (13.5% vacío + reposts mienten) ni `last_seen` (se toca en cada barrido).

### Diseño acordado
- Filtro sobre `date_canonical` (consistente con el canal).
- **Default: últimos 7 días** cuando no se especifica nada.
- Parámetro explícito: `d1`, `d3`, `d7`, `d14`, `d30` (días) · `dall` = sin límite (búsquedas históricas, ej `q"cobol" dall`).
- Aparece en la línea `🔍 Filtros:` (ej: `últimos 7d`) y viaja en callback_data (`d7` — corto, cabe en los 64 bytes).
- Score/orden no cambian — solo el recorte.

### Implementación pendiente
1. `_parse_filters`: parsear `d<N>` / `dall` → `f["max_age_days"]` (default 7 si el comando no trae `d*`; `dall` → None).
2. `_filter_offers`: comparar `date_canonical >= date('now','-N days')` (o en Python sobre el dict, como el resto de los filtros).
3. `_enc_filters`/`_dec_filters`: serializar `d<N>` (ya es corto). OJO: default 7 vs None — codificar el valor RESUELTO para que la paginación no cambie el recorte.
4. `_describe_filters`: agregar `últimos {N}d` (u "sin límite" con `dall`).
5. `/help`: línea nueva.
6. Tests: parseo (default 7, d1/d30/dall), roundtrip callback, y que `dall` no recorte.
7. Commit + push dev + `systemctl --user restart jobhunt`.

### Notas del contexto al retomar
- HEAD dev en ese momento: `60b15a1` (línea Filtros siempre visible en /jobs).
- 220/220 tests.
- Bot corre como `jobhunt.service` (systemd user) desde `/mnt/data2/projects/jobhunt`, rama `dev`.
- Los filtros viajan codificados en callback_data con límite 64 bytes de Telegram — texto de búsqueda va en base64 (máx ~16 chars) con prefijo `t`.
- Bug ya corregido que conviene recordar: regex del callback debe aceptar mayúsculas (base64 las produce) — commit `6e3ab54`.
- `/help` se envía con parse_mode HTML: NO poner `<` crudo en el texto (Telegram 400) — usar `≤` (commit `f80ad25`).
