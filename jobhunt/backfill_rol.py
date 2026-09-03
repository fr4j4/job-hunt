"""Backfill one-shot: clasificar rol_categoria con IA para ofertas sin categoría.

Para ofertas cuyo título no matchea las reglas de market.py (las que caen en
"Otro" en el reporte). Usa lote de 30 títulos por llamada IA.

Uso: xvfb-run no necesario. .venv/bin/python -m jobhunt.backfill_rol
"""
from __future__ import annotations

import json
import re
import time

from .config import load_config
from .logging_setup import get_logger
from . import db as database
from .market import ROL_CATEGORIAS

log = get_logger("jobhunt.backfill_rol")

CATEGORIAS = ("Full Stack, Backend, Frontend, Data, Mobile, AI/ML, Tech Lead, "
              "DevOps/Cloud, QA, Software, Seguridad, Ingeniería no-software, "
              "Analista/Empresa, Profesor/Formación, Soporte/TI, No-tech, Otro")

BATCH_SCHEMA = ('{"resultados": [{"idx": numero, "categoria": "'
                + "|".join(cat for cat, _ in ROL_CATEGORIAS)
                + '|Software|Seguridad|Ingeniería no-software|Analista/Empresa|Profesor/Formación|Soporte/TI|No-tech|Otro"}]}')


def _titulo_sin_match(title: str) -> bool:
    t = (title or "").lower()
    return not any(re.search(pat, t) for _, pat in ROL_CATEGORIAS)


def main() -> None:
    cfg = load_config()
    import sqlite3
    from .market import _ia_call

    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    database.init_db(conn)   # asegura la migración de rol_categoria
    rows = [dict(r) for r in conn.execute(
        "SELECT group_id, title, source FROM ofertas "
        "WHERE active=1 AND (rol_categoria='' OR rol_categoria IS NULL)").fetchall()]
    pend = [r for r in rows if _titulo_sin_match(r["title"])]
    print(f"backfill rol_categoria: {len(pend)} de {len(rows)} ofertas sin match regex")

    resultados: dict[str, str] = {}
    for start in range(0, len(pend), 30):
        batch = pend[start:start + 30]
        lista = "\n".join(f"[{i}] {o['title'][:90]}" for i, o in enumerate(batch))
        out = _ia_call(cfg,
                       f"Clasifica cada título de oferta chilena en UNA categoría de rol:\n"
                       f"{CATEGORIAS}.\n"
                       f"Si el título menciona un lenguaje/stack (python, java, react…), "
                       f"usa la categoría dev que corresponde (Backend/Frontend/Full Stack/Data…). "
                       f"'Ingeniero/a' sin dominio claro → Ingeniería no-software. "
                       f"RRHH/ventas/contabilidad → No-tech.\n\n"
                       f"Títulos:\n{lista}\n\n"
                       f'Responde SOLO JSON: {BATCH_SCHEMA}',
                       temperature=0.0)
        for ritem in (out or {}).get("resultados", []):
            try:
                gid = batch[int(ritem["idx"])]["group_id"]
                cat = str(ritem.get("categoria", "Otro"))[:40]
                resultados[gid] = cat
                conn.execute("UPDATE ofertas SET rol_categoria=? WHERE group_id=?", (cat, gid))
            except (KeyError, TypeError, IndexError, ValueError):
                continue
        conn.commit()
        print(f"  lote {start // 30 + 1}: {len(batch)} títulos → {len(resultados)} clasificados")
        time.sleep(2)
    conn.close()
    print(f"listo: {len(resultados)} ofertas clasificadas con IA")


if __name__ == "__main__":
    main()