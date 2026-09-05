import sqlite3
conn = sqlite3.connect('data/ofertas.sqlite')
conn.row_factory = sqlite3.Row
r = conn.execute("""
    SELECT group_id, title, length(description) as desc_len, description,
           salary, modality, techs, source
    FROM ofertas WHERE group_id LIKE 'senior fraud%'
""").fetchone()
if r:
    print(f"título: {r['title']}")
    print(f"desc_len: {r['desc_len']} chars")
    print(f"salary: {r['salary']!r} | modality: {r['modality']!r} | source: {r['source']}")
    print(f"techs: {r['techs']!r}")
    print(f"\n--- DESCRIPCIÓN (primeros 600 chars) ---")
    print((r['description'] or '')[:600])
else:
    print("no encontrada")
conn.close()
