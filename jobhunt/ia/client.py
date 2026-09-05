"""Cliente HTTP JSON de la IA (cloud y local).

Encapsula el requests.post + retries + clasificación de err_kind que estaba
duplicada en enrich._llm_local / ia_extract_detail / ia_extract_lote.
err_kind: '' ok · 'rate' 429/5xx · 'timeout' · 'other'.
"""
from __future__ import annotations

import json
import time

import requests

from ..config import Config
from ..logging_setup import get_logger

log = get_logger(__name__)


class IAClient:
    """Base: no se instancia sola (usa CloudClient/LocalClient)."""

    refused_inmediato = False   # LocalClient: connection refused → fallback sin retries

    def __init__(self, cfg: Config, tag: str):
        self.cfg = cfg
        self.tag = tag

    def _extra_body(self) -> dict:
        return {}

    def chat_json(self, messages: list[dict], response_format: dict | None = None,
                  model: str | None = None,
                  extra: dict | None = None) -> tuple[dict | list | None, str]:
        """POST /chat/completions y json.loads del content. Nunca lanza."""
        body: dict = {"model": model or self.model, "messages": messages, "temperature": 0}
        if response_format:
            body["response_format"] = response_format
        body.update(self._extra_body())
        if extra:
            body.update(extra)
        for attempt in range(self.retries + 1):
            try:
                req = requests.post(f"{self.base_url}/chat/completions",
                                    json=body, timeout=self.timeout, headers=self.headers)
                if req.status_code >= 400:
                    kind = "rate" if (req.status_code == 429 or req.status_code >= 500) else "other"
                    if attempt == self.retries:
                        log.warning("%s HTTP %d: %s", self.tag, req.status_code, req.text[:120])
                        return None, kind
                    time.sleep(2)
                    continue
                d = req.json()
                return json.loads(d["choices"][0]["message"]["content"]), ""
            except Exception as e:
                # orden = el de los 3 call sites originales: local corta en refused;
                # cloud clasifica ConnectTimeout (subclase de ambas) como timeout.
                if self.refused_inmediato and isinstance(e, requests.exceptions.ConnectionError):
                    log.warning("%s connection refused (fallback inmediato)", self.tag)
                    return None, "other"
                timeout = isinstance(e, requests.exceptions.Timeout)
                if attempt == self.retries:
                    if timeout:
                        log.warning("%s timeout", self.tag)
                        return None, "timeout"
                    log.warning("%s falló: %s", self.tag, e)
                    return None, "other"
                time.sleep(2)
        return None, "other"


class CloudClient(IAClient):
    def __init__(self, cfg: Config, tag: str = "IA"):
        super().__init__(cfg, tag)
        self.base_url = cfg.ia.base_url
        self.timeout = cfg.ia.timeout
        self.retries = cfg.ia.retries
        self.model = cfg.ia.model
        self.headers = {"Authorization": f"Bearer {cfg.ia.api_key}",
                        "Content-Type": "application/json"}

    def _extra_body(self) -> dict:
        # knob opcional (default: off — flash ya responde rápido)
        return {"reasoning_effort": self.cfg.ia.reasoning_effort} if self.cfg.ia.reasoning_effort else {}


class LocalClient(IAClient):
    refused_inmediato = True

    def __init__(self, cfg: Config, tag: str = "IA local"):
        super().__init__(cfg, tag)
        self.base_url = cfg.ia.local_base_url
        self.timeout = cfg.ia.local_timeout
        self.retries = cfg.ia.local_retries
        self.model = cfg.ia.local_model
        self.headers = {"Authorization": "Bearer local", "Content-Type": "application/json"}
