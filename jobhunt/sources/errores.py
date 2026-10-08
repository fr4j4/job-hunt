"""Contador de errores de red/API por fuente dentro de un barrido.

Las fuentes capturan sus propias excepciones (para no tumbar el barrido) y devuelven
[] — sin esto cmd_run no distingue "no hay ofertas" de "me bloquearon". Cada fuente
llama registrar() en sus except; cmd_run lee y limpia con tomar() por fuente.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_cuenta: dict[str, int] = {}


def registrar(fuente: str) -> None:
    with _lock:
        _cuenta[fuente] = _cuenta.get(fuente, 0) + 1


def tomar(fuente: str) -> int:
    """Devuelve y reinicia los errores acumulados de `fuente`."""
    with _lock:
        return _cuenta.pop(fuente, 0)


def reset() -> None:
    with _lock:
        _cuenta.clear()
