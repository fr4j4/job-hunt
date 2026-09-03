"""Backfill one-shot: extraer idiomas (ai_idiomas) de las ofertas ya procesadas por IA.

No toca ia_model ni el resto de campos — solo llena ai_idiomas donde está vacío.
Uso: .venv/bin/python -m jobhunt.backfill_idiomas
"""
from __future__ import annotations

import json
import re
import time

from .config import load_config
from .logging_setup import get_logger
from . import db as database

log = get_logger("jobhunt.backfill_idiomas")

PROMPT = (
    'Oferta de empleo chilena:\nTítulo: {title}\n'
    'Descripción: {desc}\n\n'
    'Lista los IDIOMAS que la oferta pide o menciona como requisito o deseable '
    '(inglés, alemán, francés, portugués, chino, japonés, italiano…). '
    'Si no se menciona NINGÚN idioma, responde lista vacía []. '
    'Responde SOLO JSON: {{"idiomas": [{{"idioma": "inglés", "nivel": "básico|intermedio|avanzado|nativo|fluido", '
    '"excluyente": true|false}}]}}'
)


def main() -> None:
    cfg = load_config()
    import sqlite3
    from .market import _ia_call
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT group_id, title, description FROM ofertas "
        "WHERE active=1 AND ia_model != '' AND length(description)>100 "
        "AND (ai_idiomas IS NULL OR ai_idiomas='') "
        "ORDER BY score DESC").fetchall()
    print(f"re-pase idiomas: {len(rows)} ofertas")
    done = 0
    for n, r in enumerate(rows, 1):
        prompt = PROMPT.format(title=r["title"], desc=(r["description"] or "")[:1400])
        out = _ia_call(cfg, prompt, temperature=0.0)
        idiomas = (out or {}).get("idiomas") or []
        idiomas = [i for i in idiomas if isinstance(i, dict) and i.get("idioma")]
        if idiomas:
            conn.execute("UPDATE ofertas SET ai_idiomas=? WHERE group_id=?",
                         (json.dumps(idiomas, ensure_ascii=False)[:400], r["group_id"]))
            conn.commit()
            done += 1
            print(f"  [{n}/{len(rows)}] {r['title'][:40]} → {json.dumps(idiomas, ensure_ascii=False)[:90]}")
        if n % 10 == 0:
            print(f"  ...{n}/{len(rows)}")
        time.sleep(2.5)
    print(f"listo: {done} ofertas con idiomas registrados")


if __name__ == "__main__":
    main()