# compat: re-export — eliminar en v6 cuando los imports apunten al paquete nuevo
"""Alias de jobhunt.salarios.stats (los tests parchean nombres AQUÍ)."""
from .salarios.stats import *  # noqa: F401,F403
from .salarios.stats import _median  # noqa: F401 (privado usado por channel.py)
