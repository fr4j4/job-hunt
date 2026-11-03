"""Tabla nativa de Telegram (mensajes enriquecidos, Bot API 10.x) — EXPERIMENTAL.

Estructura de la tabla según la doc oficial (InputRichBlockTable / RichBlockTableCell):
  {"type": "table", "cells": [[{"text": ..., "is_header": true, "align": "left"}, ...], ...],
   "is_bordered": true, "is_striped": true, "is_compact": true}

Aún NO verificado contra Telegram real: la forma de RichText, de InputRichMessage y el
método de envío (la doc que se tuvo a mano no los incluía). Por eso enviar_tabla prueba
variantes en orden, recuerda la que funcione y, si todas fallan, devuelve los errores
EXACTOS de Telegram para corregir el formato (el llamador cae a las tarjetas HTML).
"""
from __future__ import annotations

import re

from .render import _MODALIDAD_TXT, _age_short, _mod_short, _titulo_corto, salary_tag

_COLUMNAS = [("%", "center"), ("Cargo", "left"), ("Empresa", "left"),
             ("Modalidad", "left"), ("Sueldo", "right"), ("Edad", "center")]

_variante_ok: int | None = None   # índice de la variante que Telegram aceptó (por proceso)


def _plano(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def filas_tabla(offers: list[dict]) -> list[list[str]]:
    """Datos de la tabla como texto plano: [cabecera, fila1, fila2, ...]."""
    filas = [[c for c, _ in _COLUMNAS]]
    for j in offers:
        sal = salary_tag(j).replace("💵", "").replace("USD", "US$").strip()
        age = _age_short(j.get("date_posted") or "")
        filas.append([
            f"{int(j.get('score') or 0)}%",
            _titulo_corto(j.get("title") or "", 45),
            _plano(j.get("company") or "")[:24] or "—",
            _MODALIDAD_TXT.get(_mod_short(j), "—"),
            sal or "—",
            "—" if (not age or "?" in age) else age,
        ])
    return filas


def bloque_tabla(offers: list[dict], texto=lambda s: s) -> dict:
    """InputRichBlockTable. `texto` adapta cada string de celda a la forma de RichText."""
    cells = []
    for i, fila in enumerate(filas_tabla(offers)):
        row = []
        for (_, align), val in zip(_COLUMNAS, fila):
            c = {"text": texto(val), "align": align}
            if i == 0:
                c["is_header"] = True
            row.append(c)
        cells.append(row)
    return {"type": "table", "cells": cells, "is_bordered": True, "is_striped": True,
            "is_compact": True}


def _html_tabla(offers: list[dict]) -> str:
    from html import escape
    filas = filas_tabla(offers)
    th = "".join(f"<th>{escape(c)}</th>" for c in filas[0])
    tr = "".join("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in f) + "</tr>" for f in filas[1:])
    return f"<table><tr>{th}</tr>{tr}</table>"


# (descripción, constructor del payload). El orden es el de probabilidad.
def _variantes(chat_id, offers):
    return [
        ("blocks+texto-string", lambda: {"chat_id": chat_id, "rich_message": {
            "blocks": [bloque_tabla(offers)]}}),
        ("blocks+texto-objeto", lambda: {"chat_id": chat_id, "rich_message": {
            "blocks": [bloque_tabla(offers, lambda s: {"type": "plain", "text": s})]}}),
        ("html", lambda: {"chat_id": chat_id, "rich_message": {
            "text": _html_tabla(offers), "parse_mode": "HTML"}}),
    ]


def probar_variantes(tg_call, chat_id, offers: list[dict]) -> list[tuple[str, bool, str]]:
    """Prueba TODAS las variantes (sin cortar en la primera que funcione) y devuelve
    [(variante, ok, detalle)]. Para diagnóstico: `python -m jobhunt tabla`."""
    out = []
    for nombre, build in _variantes(chat_id, offers):
        try:
            resp = tg_call("sendRichMessage", build())
            ok = isinstance(resp, dict) and bool(resp.get("ok", True))
            out.append((nombre, ok, "enviada" if ok else str(resp)[:200]))
        except Exception as e:
            out.append((nombre, False, str(e)[:200]))
    return out


def enviar_tabla(tg_call, chat_id, offers: list[dict]) -> tuple[bool, list[str]]:
    """Intenta enviar la tabla nativa. tg_call(method, payload) puede lanzar (error HTTP).
    Retorna (ok, errores) — errores = ["variante: detalle de Telegram", ...]."""
    global _variante_ok
    variantes = _variantes(chat_id, offers)
    orden = list(range(len(variantes)))
    if _variante_ok is not None:                  # la que ya funcionó, primero
        orden.remove(_variante_ok)
        orden.insert(0, _variante_ok)
    errores: list[str] = []
    for i in orden:
        nombre, build = variantes[i]
        try:
            resp = tg_call("sendRichMessage", build())
            if isinstance(resp, dict) and resp.get("ok", True):
                _variante_ok = i
                return True, errores
            errores.append(f"{nombre}: {str((resp or {}).get('description') or resp)[:160]}")
        except Exception as e:
            errores.append(f"{nombre}: {str(e)[:160]}")
    _variante_ok = None
    return False, errores
