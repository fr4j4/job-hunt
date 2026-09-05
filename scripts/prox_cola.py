import sqlite3
conn = sqlite3.connect('data/ofertas.sqlite')
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT group_id, title, length(description) as desc_len, description_source
    FROM ofertas
    WHERE active=1 AND ia_model='' AND
    (length(description)>200 OR description_source!='') AND
    (modality='' OR salary='' OR description IS NULL OR
     salary_status IN ('implausible','suspect'))
    ORDER BY score DESC LIMIT 3
""").fetchall()
for r in rows:
    print(f"{r['group_id'][:45]} | desc: {r['desc_len']} chars | src: {r['description_source']}")
conn.close()
