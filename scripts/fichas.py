import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT group_id, title, company, location, salary, salary_status, modality,
           seniority_real, rol_categoria, techs, ai_ingles, ai_idiomas,
           ai_opinion, ai_resumen, ai_fit_reason, ai_red_flags, ai_green_flags,
           ai_benefits, score, market_score, source, date_posted, ia_model, ctx_version
    FROM ofertas
    WHERE ia_model LIKE '%qwen%'
    ORDER BY rowid DESC
    LIMIT 8
""").fetchall()

for i, r in enumerate(rows, 1):
    print(f"{'='*72}")
    print(f"FICHA {i} — {r['group_id'][:50]}")
    print(f"{'='*72}")
    print(f"  Título:     {r['title']}")
    print(f"  Empresa:    {r['company'] or '—'} | Ubicación: {r['location'] or '—'}")
    print(f"  Fuente:     {r['source']} | Publicada: {r['date_posted'] or '—'}")
    print(f"  Salario:    {r['salary'] or '—'} ({r['salary_status'] or 'sin clasificar'})")
    print(f"  Modalidad:  {r['modality'] or '—'}")
    print(f"  Seniority:  {r['seniority_real'] or '—'} | Rol: {r['rol_categoria'] or '—'} | Inglés: {r['ai_ingles'] or '—'}")
    print(f"  Techs:      {r['techs'] or '—'}")
    print(f"  Idiomas:    {r['ai_idiomas'] or '—'}")
    print(f"  Score:      {r['score']} | Market: {r['market_score']}")
    print(f"  Modelo:     {r['ia_model']} | ctx: {r['ctx_version']}")
    print(f"  Opinion:    {(r['ai_opinion'] or '—')[:160]}")
    print(f"  Resumen:    {(r['ai_resumen'] or '—')[:140]}")
    print(f"  Fit:        {(r['ai_fit_reason'] or '—')[:140]}")
    print(f"  Red flags:  {r['ai_red_flags'] or '—'}")
    print(f"  Green flags:{r['ai_green_flags'] or '—'}")
    print(f"  Benefits:   {r['ai_benefits'] or '—'}")
    print()

conn.close()
