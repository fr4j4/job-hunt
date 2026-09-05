import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT group_id, title, company, rol_categoria, techs, ia_model
    FROM ofertas WHERE techs LIKE '%NiFi%' AND active=1
""").fetchall()
for r in rows:
    print(f"--- {r['group_id'][:45]}")
    print(f"    título: {r['title']}")
    print(f"    empresa: {r['company'] or '—'} | rol: {r['rol_categoria']} | ia: {r['ia_model']}")
    print(f"    techs: {r['techs']}")
conn.close()
