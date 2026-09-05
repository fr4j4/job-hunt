import sqlite3
conn = sqlite3.connect('data/ofertas.sqlite')
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT group_id, title FROM ofertas
    WHERE active=1 AND ia_model='' AND
    (length(description)>200 OR description_source!='') AND
    (modality='' OR salary='' OR description IS NULL OR
     salary_status IN ('implausible','suspect'))
    ORDER BY score DESC
""").fetchall()
print(f"TOTAL en cola: {len(rows)}")
with open('data/cola_enrich_ids.txt', 'w') as f:
    for r in rows:
        f.write(f"{r['group_id']}\n")
print("Guardados en data/cola_enrich_ids.txt")
for r in rows[:8]:
    print(f"  {r['group_id'][:40]} — {(r['title'] or '')[:50]}")
conn.close()
