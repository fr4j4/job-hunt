import sqlite3
conn = sqlite3.connect('data/ofertas.sqlite')
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT group_id, title, ia_model, ctx_version,
           ai_opinion, ai_resumen, ai_fit_reason, techs,
           modality, seniority_real, rol_categoria, ai_ingles,
           salary, ai_red_flags, ai_green_flags, ai_benefits
    FROM ofertas
    WHERE ia_model LIKE '%ornith%' OR ia_model LIKE '%madkoding%'
    ORDER BY rowid DESC
""").fetchall()

print(f"TOTAL registros con IA local: {len(rows)}\n")
for r in rows:
    print(f"--- {r['group_id'][:45]}")
    print(f"    título: {(r['title'] or '')[:60]}")
    print(f"    ia_model: {r['ia_model']} | ctx: {r['ctx_version']}")
    print(f"    techs: {r['techs']}")
    print(f"    modality: {r['modality']} | seniority: {r['seniority_real']} | rol: {r['rol_categoria']} | inglés: {r['ai_ingles']}")
    print(f"    salary: {r['salary']}")
    print(f"    opinion: {(r['ai_opinion'] or '')[:110]}")
    print(f"    resumen: {(r['ai_resumen'] or '')[:90]}")
    print(f"    fit: {(r['ai_fit_reason'] or '')[:90]}")
    print(f"    red_flags: {r['ai_red_flags']} | green_flags: {r['ai_green_flags']} | benefits: {r['ai_benefits']}")
    print()

conn.close()
