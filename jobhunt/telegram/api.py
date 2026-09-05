"""Cliente HTTP delgado para la Bot API de Telegram (movido desde jobhunt.bot)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("jobhunt.telegram")

# ponytail: warning "una sola vez" es un flag de proceso, no por-instancia —
# igual que el comportamiento original en jobhunt.bot._chat_allowed_empty_warned.
_empty_allowlist_warned = False


class TelegramClient:
    """token + allowlist de chats. call(method, **params) hace el POST con retries/backoff."""

    def __init__(self, token: str, allowed: set[int] | None = None):
        self.token = token
        self.allowed: set[int] = allowed if allowed is not None else set()

    def chat_allowed(self, chat_id) -> bool:
        global _empty_allowlist_warned
        if not self.allowed:
            if not _empty_allowlist_warned:
                log.warning("allowlist de chats vacía — modo dev, todos los chats permitidos")
                _empty_allowlist_warned = True
            return True
        try:
            ok = int(chat_id) in self.allowed
        except (TypeError, ValueError):
            ok = False
        if not ok:
            # El rechazo era silencioso (PermissionError sin rastro): con la
            # allowlist efectiva (unión con chat_id/channel.chat_id) un chat que
            # antes pasaba puede quedar fuera, y el operador necesita verlo.
            log.warning("chat %s fuera de la allowlist efectiva %s — rechazado",
                        chat_id, sorted(self.allowed))
        return ok

    def call(self, method: str, retries: int = 2, **params) -> dict:
        cid = params.get("chat_id")
        if cid is not None and not self.chat_allowed(cid):
            raise PermissionError(f"chat {cid} no está en TELEGRAM_ALLOWED_CHATS")
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(retries + 1):
            resp = requests.post(url, json=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 429) and attempt < retries:
                wait = 1.5 * (attempt + 1)
                if resp.status_code == 429:
                    try:
                        retry_after = resp.json().get("parameters", {}).get("retry_after")
                        if retry_after:
                            wait = min(float(retry_after), 30)
                    except Exception:
                        pass
                time.sleep(wait)
                continue
            # incluir el body de Telegram en el error: la causa real vive ahí
            # ("message is not modified", "message ... not found", etc.)
            try:
                detail = resp.text[:200]
            except Exception:
                detail = ""
            raise RuntimeError(f"HTTP {resp.status_code} {detail}".strip())
        raise RuntimeError("unreachable")
