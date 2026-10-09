"""Audita sueldos ya guardados: lista ofertas cuyo sueldo NO figura en el texto.

Uso (en el equipo con la DB real):  python scripts/auditar_sueldos.py [ruta.db] [--limpiar]
--limpiar borra salary de las que tienen salary_source='ia' sin respaldo en el texto.
"""
import sqlite3
import sys

from jobhunt.salarios.texto import sueldo_respaldado
from jobhunt.salarios.stats import parse_salary_clp

args = [a for a in sys.argv[1:] if not a.startswith("--")]
db = args[0] if args else "jobhunt.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT group_id, title, salary, salary_source, description FROM ofertas "
                    "WHERE salary != '' AND salary IS NOT NULL").fetchall()
mal = [r for r in rows if r["salary_source"] == "ia"
       and not sueldo_respaldado(parse_salary_clp(r["salary"]), r["title"], r["description"] or "")]
por_fuente = {}
for r in rows:
    por_fuente[r["salary_source"] or "(vacío)"] = por_fuente.get(r["salary_source"] or "(vacío)", 0) + 1
print("sueldos por procedencia:", por_fuente)
print(f"sueldos IA sin respaldo en el texto: {len(mal)} de {por_fuente.get('ia', 0)}")
for r in mal[:30]:
    print(f"  {r['group_id']}  {r['salary']:<16} {r['title'][:60]}")
if "--limpiar" in sys.argv and mal:
    conn.executemany("UPDATE ofertas SET salary='', salary_source='', salary_status='', salary_note='' "
                     "WHERE group_id=?", [(r["group_id"],) for r in mal])
    conn.commit()
    print("limpiados:", len(mal))
