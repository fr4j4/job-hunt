"""Sistema de logs centralizado.

Uso en cualquier módulo:
    from .logging_setup import get_logger
    log = logging_setup.get_logger(__name__)
    log.info("barrido iniciado: %d ofertas", n)
    log.warning("glassdoor falló: %s", e)

Output: consola (INFO+) + archivo data/jobhunt.log (rotativo 5MB x3).
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_LOGGERS: dict[str, "logging.Logger"] = {}
_initialized = False


def setup(log_dir: Path | None = None, level: str = "INFO") -> None:
    """Inicializa el logging raíz una sola vez (idempotente)."""
    global _initialized
    if _initialized:
        return
    log_file = (log_dir or Path("data")) / "jobhunt.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    root = logging.getLogger("jobhunt")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # silenciar verbosidad de librerías
    for noisy in ("httpx", "httpcore", "urllib3", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root.debug("logging inicializado (file=%s, level=%s)", log_file, level)
    _initialized = True


def get_logger(name: str) -> "logging.Logger":
    """Logger del módulo: hereda del root 'jobhunt' (siempre inicializado antes de usar)."""
    if not _initialized:
        setup()
    short = name.replace("jobhunt.", "") if name.startswith("jobhunt") else name
    if short not in _LOGGERS:
        _LOGGERS[short] = logging.getLogger(f"jobhunt.{short}")
    return _LOGGERS[short]
