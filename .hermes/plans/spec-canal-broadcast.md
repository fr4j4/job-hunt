# Spec: Modo canal (broadcast) distinto al modo grupo (interactivo)

## Problema

El bot opera un solo canal de salida (TELEGRAM_CHAT_ID) con mensajes paginados
editables. Un canal de Telegram (broadcast) no es un grupo:

- posts de usuarios normales no generan `message` (no hay comandos)
- editar un post destruye el feed cronológico (la gracia del canal es el histórico)
- paginación multiusuario sobre 1 mensaje = colisiones (último edit gana)

## Decisión de producto

Dos modos de comunicación en paralelo, cada uno con su contrato:

| | MODO GRUPO (actual) | MODO CANAL (nuevo) |
|---|---|---|
| Destino | TELEGRAM_CHAT_ID | TELEGRAM_CHANNEL_ID |
| Uso | interactivo: comandos, ancla paginada, /search vivo | broadcast: post por oferta nueva que pasa el filtro |
| Paginación | sí (edit del mensaje) | no — cada oferta es su propio post |
| Multiusuario | los clics editan el mensaje compartido | nadie modifica el feed, cada post individual |
| Trigger | comandos del usuario + cron | automático: cada oferta nueva que pasa umbral |
| Dedup | no aplica | solo ofertas realmente nuevas (group_id no notificado antes) |

## Flujo del modo canal

```
barrido → upsert → ofertas nuevas
    → filtro: score >= CHANNEL_MIN_SCORE (default 70, .env)
    → para cada una (orden score DESC, sleep 2s anti-flood):
        post al canal: título, score, empresa, salario, modalidad,
        location, techs, idioma, link de postulación
    → marca group_id como notificado (tabla canal_log o columna notified_channel_at)
```

## Formato del post (1 oferta)

```
🎯 [92] Data Engineer (Python + AWS + DBT)
🏢 FullStack · 📍 Chile · 💻 remoto
💰 $2.8M líquido (no declarado → omitir línea)
🧰 Py · AWS · DBT · SQL
🗣 EN · 📅 hace 2 días
🔗 postular
```

Sin botones inline (nada que editar). Link plano al final.

## Cambios de código

1. `config.py`: `ChannelCfg(enabled, chat_id, min_score, max_posts_per_sweep=10)`
   - vars: `TELEGRAM_CHANNEL_ID`, `CHANNEL_MIN_SCORE`, `CHANNEL_MAX_POSTS`
2. `notify.py`: `build_offer_post(offer) -> str` (formato del post de canal)
3. `bot.py` (o `notify.py`): `publish_channel(cfg, offers) -> int` — filtra por score,
   dedup contra canal_log, publica con sleep, registra
4. `cli.py`: después del rescore del barrido, `publish_channel(cfg, new_offers)`
   y también en el sweep del cron
5. Dedup: tabla nueva `canal_log(group_id, posted_at)` o columna
   `notified_channel_at` en ofertas (preferida: columna, menos migración)

## Config (.env)

```
TELEGRAM_CHANNEL_ID=-100xxxxxxxxxx      # chat_id del canal
CHANNEL_MIN_SCORE=70                    # umbral de score para postear
CHANNEL_MAX_POSTS=10                    # tope por barrido (anti-flood)
```

## Comportamiento

- CHANNEL sin ID o disabled → todo el modo canal es no-op (cero errores en grupo)
- El grupo NO cambia: ancla paginada, /search vivo, /enrich, /report siguen igual
- Los posts de canal NO notifican al grupo ni viceversa
- Primer barrido con canal activado: solo ofertas nuevas de ese barrido (no backfill histórico)

## Edge cases

- Ofertas nuevas < umbral: no se postean (pero quedan en el pool y en el grupo)
- Más de CHANNEL_MAX_POSTS nuevas sobre umbral: se postean las top N por score
- Canal borrado o bot degradado a no-admin: log warning, no tumba el barrido
- Rate limit Telegram (~20 msg/min): sleep 2s entre posts, tope 10/barrido = seguro

## Fuera de alcance v1

- Reenvíos/postulación directa desde el canal
- Backfill histórico del pool
- Canal con paginación (explícitamente rechazado por el usuario)