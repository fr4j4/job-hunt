import sqlite3
conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

# 1. La oferta en cuestión — con notified_channel_at
r = conn.execute("""
    SELECT group_id, title, salary, salary_source, salary_status, ia_model,
           ai_opinion, ctx_version, notified_channel_at, first_seen, last_seen,
           fetch_fails, last_fetch_ok
    FROM ofertas WHERE group_id LIKE 'data engineer aws proyecto sector%'
""").fetchone()
if r:
    print("=== OFERTA ACTUAL ===")
    print(f"group_id: {r['group_id']}")
    print(f"salary: {r['salary']!r} ({r['salary_source']}/{r['salary_status']})")
    print(f"ia_model: {r['ia_model']} | ctx: {r['ctx_version']}")
    print(f"opinion ACTUAL: {r['ai_opinion']!r}")
    print(f"notified_channel_at: {r['notified_channel_at']!r}")
    print(f"first_seen: {r['first_seen']} | last_seen: {r['last_seen']}")
    print(f"fetch_fails: {r['fetch_fails']} | last_fetch_ok: {r['last_fetch_ok']!r}")

# 2. Historial de posts del canal para esta oferta
print("\n=== CHANNEL_POSTS (historial) ===")
posts = conn.execute("""
    SELECT * FROM channel_posts WHERE group_id LIKE 'data engineer aws proyecto sector%'
    ORDER BY rowid DESC LIMIT 5
""").fetchall()
if posts:
    for p in posts:
        print(dict(p))
else:
    print("(sin posts registrados para esta oferta)")

# 3. ¿Cuántas ofertas publicadas tienen opinion que NO menciona su salario?
print("\n=== PUBLICADAS con salario pero opinion sin mencionarlo ===")
pub = conn.execute("""
    SELECT group_id, title, salary, ai_opinion, notified_channel_at
    FROM ofertas
    WHERE notified_channel_at IS NOT NULL AND salary != '' AND ai_opinion != ''
      AND (ai_opinion LIKE '%sin sueldo%' OR ai_opinion LIKE '%sin salario%'
           OR ai_opinion LIKE '%no declara%' OR ai_opinion LIKE '%no se declara%')
    ORDER BY notified_channel_at DESC
""").fetchall()
print(f"total: {len(pub)}")
for p in pub:
    print(f"  {p['group_id'][:40]} | {p['salary'][:25]} | {(p['ai_opinion'] or '')[:80]} | {p['notified_channel_at']}")

conn.close()
