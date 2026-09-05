import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT group_id, title, company, salary, salary_raw, salary_source, salary_status,
           salary_note, modality, techs, ai_opinion, ia_model, ctx_version,
           first_seen, last_seen, last_fetch_ok, fetch_fails, score, market_score,
           description_source, length(description) as desc_len
    FROM ofertas
    WHERE title LIKE '%Data Engineer AWS%' OR title LIKE '%Sector Financiero%'
    ORDER BY rowid DESC LIMIT 5
""").fetchall()

for r in rows:
    print(f"{'='*70}")
    print(f"group_id: {r['group_id']}")
    print(f"título:   {r['title']}")
    print(f"empresa:  {r['company'] or '—'}")
    print(f"salary:   {r['salary']!r} | raw: {r['salary_raw']!r} | source: {r['salary_source']!r} | status: {r['salary_status']!r}")
    print(f"note:     {r['salary_note']!r}")
    print(f"modality: {r['modality']!r} | techs: {r['techs']!r}")
    print(f"ia_model: {r['ia_model']!r} | ctx: {r['ctx_version']!r}")
    print(f"first_seen: {r['first_seen']} | last_seen: {r['last_seen']} | last_fetch_ok: {r['last_fetch_ok']!r} | fetch_fails: {r['fetch_fails']}")
    print(f"desc_src: {r['description_source']!r} | desc_len: {r['desc_len']}")
    print(f"opinion:  {(r['ai_opinion'] or '—')[:200]}")
    print()

conn.close()
