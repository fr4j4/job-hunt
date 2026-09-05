import json
import sys
import time

sys.path.insert(0, "/mnt/data2/projects/jobhunt")
from jobhunt.config import load_config

OFFERTA_CORTA = {
    "title": "Desarrollador Backend Java Spring",
    "company": "Banco de Chile",
    "location": "Santiago",
    "salary": "CLP 2500000",
    "modality": "",
    "salary_status": "",
    "salary_note": "",
    "description": "Buscamos un desarrollador backend con Java, Spring Boot, AWS y PostgreSQL. "
                   "Trabajo remoto, inglés deseable. Equipo ágil, 3+ años de experiencia.",
}

def probar(modelo: str):
    import sqlite3
    from jobhunt.enrich import ia_extract_local, compute_market_context, profile_description
    cfg = load_config()
    cfg.ia.local_enabled = True
    cfg.ia.local_base_url = "http://localhost:11434/v1"
    cfg.ia.local_model = modelo
    cfg.ia.local_timeout = 600
    conn = sqlite3.connect("file:data/ofertas.sqlite?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    mercado = compute_market_context(conn)
    conn.close()
    perfil = profile_description(cfg)
    print(f"MODELO: {modelo}")
    t0 = time.time()
    d, err = ia_extract_local(cfg, OFFERTA_CORTA, perfil, mercado)
    print(f"  {time.time()-t0:.1f}s → {'OK' if d and err=='' else f'FALLÓ ({err})'}")
    if d:
        print(f"  techs: {d.get('techs')}")
        print(f"  modalidad: {d.get('modalidad')} | seniority: {d.get('seniority_real')} | rol: {d.get('rol_categoria')}")
        print(f"  ingles: {d.get('ingles')} | salario: {d.get('salario_clp_mensual')}")
        print(f"  opinion: {(d.get('opinion') or '')[:100]}")

if __name__ == "__main__":
    probar(sys.argv[1] if len(sys.argv) > 1 else "madkoding/ornith-1.5-9b-q2k:latest")
