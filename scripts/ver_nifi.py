import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

# 1. Ofertas con techs=NiFi
rows = conn.execute("""
    SELECT group_id, title, company, rol_categoria, techs, ia_model,
           length(description) as desc_len, description
    FROM ofertas
    WHERE techs LIKE '%NiFi%' AND active=1
    ORDER BY rowid DESC LIMIT 6
""").fetchall()
print(f"=== OFERTAS CON NiFi ({len(rows)}) ===")
for r in rows:
    print(f"\n--- {r['group_id'][:45]}")
    print(f"    título: {r['title']}")
    print(f"    empresa: {r['company'] or '—'} | rol: {r['rol_categoria'] or '—'} | ia_model: {r['ia_model'] or '—'}")
    print(f"    techs: {r['techs']}")
    print(f"    desc_len: {r['desc_len']}")
    desc = (r['description'] or '')[:400]
    print(f"    desc: {desc!r}")

conn.close()
