import sqlite3
import sys
import time

sys.path.insert(0, "/mnt/data2/projects/jobhunt")
from jobhunt.config import load_config
from jobhunt.enrich import ia_extract_local, compute_market_context, profile_description

cfg = load_config()
cfg.ia.local_enabled = True
cfg.ia.local_base_url = "http://localhost:11434/v1"
cfg.ia.local_model = cfg.ia.local_model  # del .env = qwen2.5:7b

conn = sqlite3.connect("file:data/ofertas.sqlite?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
row = dict(conn.execute("""
    SELECT group_id, title, company, location, description, salary,
           modality, salary_raw, salary_status, salary_note
    FROM ofertas
    WHERE active=1 AND description != '' AND length(description) > 200
    ORDER BY score DESC LIMIT 1
""").fetchone())
print(f"OFERTA: {row['title'][:60]} | desc: {len(row['description'])} chars")
print(f"modelo: {cfg.ia.local_model}")

mercado = compute_market_context(conn)
perfil = profile_description(cfg)
t0 = time.time()
d, err = ia_extract_local(cfg, row, perfil, mercado)
dt = time.time() - t0
print(f"\nTIEMPO TOTAL: {dt:.1f}s | err: {err!r}")
if d:
    print("EXTRACT:")
    for k in ("techs", "modalidad", "seniority_real", "rol_categoria", "ingles", "salario_clp_mensual"):
        print(f"  {k}: {d.get(k)}")
    print("OPINION:")
    print(f"  opinion: {d.get('opinion', '')[:130]}")
    print(f"  resumen: {d.get('resumen', '')[:100]}")
    print(f"  fit: {d.get('fit_reason', '')[:100]}")
else:
    print(f"FALLÓ: {err}")
conn.close()
