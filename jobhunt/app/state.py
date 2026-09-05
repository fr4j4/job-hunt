"""Estado global del bot como clases (paso 6 del refactor).

Los módulos consumidores (bot.py) siguen accediendo por clave — `st["running"]`,
`st.update(running=True)` — porque los tests monkeypatchean el nombre a nivel de
módulo con un dict plano. Por eso la base implementa el protocolo de mapping
mínimo y las subclases añaden propiedades tipadas encima del mismo dict interno.
"""
from __future__ import annotations

import threading

StopEvent = threading.Event   # /stop: corte limpio en el próximo punto seguro


class _DictState:
    """Estado con acceso por clave (compat dict) sobre un dict interno."""

    _DEFAULTS: dict = {}

    def __init__(self, **over):
        self._d = {**self._DEFAULTS, **over}

    # ---- protocolo de mapping (compat con el dict que reemplaza) ----
    def __getitem__(self, key): return self._d[key]
    def __setitem__(self, key, value): self._d[key] = value
    def __contains__(self, key): return key in self._d
    def __iter__(self): return iter(self._d)
    def __len__(self): return len(self._d)
    def __eq__(self, other):
        return self._d == (other._d if isinstance(other, _DictState) else other)
    def __repr__(self): return f"{type(self).__name__}({self._d!r})"

    def get(self, key, default=None): return self._d.get(key, default)
    def keys(self): return self._d.keys()
    def values(self): return self._d.values()
    def items(self): return self._d.items()
    def update(self, *args, **kw): self._d.update(*args, **kw)

    def reset(self) -> None:
        """Vuelve TODOS los campos a su default."""
        self._d = dict(self._DEFAULTS)


class IAState(_DictState):
    """Estado del batch IA (antes bot._IA_STATE)."""

    _DEFAULTS = {"running": False, "done": 0, "total": 0, "current": "", "t0": 0.0}

    @property
    def running(self) -> bool: return self._d["running"]
    @running.setter
    def running(self, v: bool): self._d["running"] = v

    @property
    def done(self) -> int: return self._d["done"]
    @done.setter
    def done(self, v: int): self._d["done"] = v

    @property
    def total(self) -> int: return self._d["total"]
    @total.setter
    def total(self, v: int): self._d["total"] = v

    @property
    def current(self) -> str: return self._d["current"]
    @current.setter
    def current(self, v: str): self._d["current"] = v

    @property
    def t0(self) -> float: return self._d["t0"]
    @t0.setter
    def t0(self, v: float): self._d["t0"] = v


class SearchState(_DictState):
    """Estado del barrido (antes bot._SEARCH_STATE)."""

    _DEFAULTS = {"running": False, "t0": 0.0}

    @property
    def running(self) -> bool: return self._d["running"]
    @running.setter
    def running(self, v: bool): self._d["running"] = v

    @property
    def t0(self) -> float: return self._d["t0"]
    @t0.setter
    def t0(self, v: float): self._d["t0"] = v
