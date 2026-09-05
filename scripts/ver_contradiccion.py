import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

# Ofertas con salario registrado PERO opinion que dice "sin salario/sueldo"
rows = conn.execute("""
    SELECT group_id, title, salary, salary_source, salary_status, ia_model,
           ai_opinion, ctx_version, notified_channel_at
    FROM ofertas
    WHERE active=1 AND salary != '' AND ai_opinion != ''
      AND (ai_opinion LIKE '%sin sueldo%' OR ai_opinion LIKE '%sin salario%'
           OR ai_opinion LIKE '%no declara%' OR ai_opinion LIKE '%no se declara%'
           OR ai_opinion LIKE '%carece de datos monetarios%')
    ORDER BY rowid DESC
""").fetchall()

print(f"CONTRADICCIÓN (salario presente + opinion dice 'sin salario'): {len(rows)}\n")
for r in rows:
    print(f"--- {r['group_id'][:45]}")
    print(f"    salary: {r['salary']!r} ({r['salary_source']}/{r['salary_status']})")
    print(f"    ia_model: {r['ia_model']} | ctx: {r['ctx_version']}")
    print(f"    opinion: {(r['ai_opinion'] or '')[:130]}")
    print(f"    publicado: {r['notified_channel_at'] or 'no'}")
    print()

conn.close()
