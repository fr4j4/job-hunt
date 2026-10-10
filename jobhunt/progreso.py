"""Estado de progreso de un barrido y su render para el mensaje vivo de Telegram.

Hoja pura (sin red ni DB). Varias fuentes corren en paralelo, así que el estado es un
diccionario por fuente protegido con lock, y el render lo resume en pocas líneas.
"""
from __future__ import annotations

import html
import threading
import time

_ICONO = {"pend": "⏳", "run": "🔄", "ok": "✅", "err": "⚠️"}


def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=False)


class Progreso:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.t0 = time.time()
        self.fuentes: dict[str, dict] = {}
        self.etapa = "fuentes"                       # fuentes | guardando | ia | canal
        self.ia = {"hechas": 0, "total": 0, "lote": 0, "lotes": 0, "t0": 0.0}

    # --- eventos -------------------------------------------------------------
    def fuente(self, nombre: str, estado: str, n: int = 0, segs: float = 0.0,
               query: str = "", page: int = 0) -> None:
        with self._lock:
            f = self.fuentes.setdefault(nombre, {"estado": "pend", "n": 0, "segs": 0.0, "query": "", "page": 0})
            f["estado"] = estado
            if estado in ("ok", "err"):
                f["n"], f["segs"], f["query"], f["page"] = n, segs, "", 0
            else:
                f["query"], f["page"] = query or f["query"], page or f["page"]

    def fase(self, etapa: str, **kw) -> None:
        with self._lock:
            self.etapa = etapa
            if etapa == "ia":
                if not self.ia["t0"]:
                    self.ia["t0"] = time.time()
                self.ia.update({k: v for k, v in kw.items() if k in self.ia})

    # --- render --------------------------------------------------------------
    def eta_ia(self) -> int | None:
        ia = self.ia
        if ia["hechas"] < 5 or not ia["t0"] or ia["total"] <= ia["hechas"]:
            return None
        ritmo = ia["hechas"] / max(1.0, time.time() - ia["t0"])
        return int((ia["total"] - ia["hechas"]) / ritmo / 60) + 1

    def render(self) -> str:
        with self._lock:
            mins, segs = divmod(int(time.time() - self.t0), 60)
            lineas = [f"🔍 <b>Búsqueda en curso</b> ({mins}m{segs:02d}s)"]
            for nombre, f in self.fuentes.items():
                txt = f"{_ICONO.get(f['estado'], '•')} <b>{_esc(nombre)}</b>"
                if f["estado"] in ("ok", "err"):
                    txt += f" {f['n']}" + (f" · {int(f['segs'])}s" if f["segs"] else "")
                elif f["query"]:
                    txt += f" \"{_esc(f['query'][:28])}\"" + (f" p{f['page']}" if f["page"] else "")
                lineas.append(txt)
            if self.etapa == "guardando":
                lineas.append("💾 guardando ofertas…")
            elif self.etapa in ("ia", "canal") and self.ia["total"]:
                ia = self.ia
                eta = self.eta_ia()
                lineas.append(f"🧠 IA <code>{ia['hechas']}/{ia['total']}</code>"
                              f" · lote {ia['lote']}/{ia['lotes']}" + (f" · ~{eta} min" if eta else ""))
            return "\n".join(lineas)


class Limitador:
    """Deja pasar como máximo una acción cada `intervalo` segundos (edición de mensaje)."""

    def __init__(self, intervalo: float = 4.0, reloj=time.time) -> None:
        self.intervalo, self._ultimo, self._lock, self._reloj = intervalo, 0.0, threading.Lock(), reloj

    def listo(self) -> bool:
        with self._lock:
            ahora = self._reloj()
            if ahora - self._ultimo < self.intervalo:
                return False
            self._ultimo = ahora
            return True

    def esperar_hasta(self, segs: float) -> None:
        """Telegram pidió esperar (429 retry_after): bloquea ediciones ese tiempo."""
        with self._lock:
            self._ultimo = max(self._ultimo, self._reloj() + segs - self.intervalo)
