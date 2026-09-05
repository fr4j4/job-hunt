"""Benchmark comparativo ornith IQ2M vs Q2K — misma oferta real, 2 pasadas cada uno."""
import json
import sys
import time

sys.path.insert(0, "/mnt/data2/projects/jobhunt")
from jobhunt.config import load_config

OFFERTA_TEST = {
    "title": "Senior Fraud & Security Ops Engineer",
    "company": "",
    "location": "",
    "salary": "",
    "modality": "",
    "salary_status": "",
    "salary_note": "",
    "description": ("Deseables (suman puntos) Experiencia en fintech, banca o medios de pago con "
                    "pruebas de fraude lógico a escala de producción. Machine Learning aplicado a "
                    "evasión de modelos de detección de fraude (adversarial ML). Frameworks de "
                    "explotación: Metasploit, extensiones de automatización. Indispensable: "
                    "conocimiento de SQL, Python y arquitecturas de streaming (Kafka). "
                    "Valoramos inglés avanzado."),
}

def probar(modelo: str, veces: int = 2):
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
    print(f"\n{'='*60}\nMODELO: {modelo}\n{'='*60}")
    for i in range(veces):
        t0 = time.time()
        d, err = ia_extract_local(cfg, OFFERTA_TEST, perfil, mercado)
        dt = time.time() - t0
        ok = "OK" if (d and err == "") else f"FALLÓ ({err})"
        print(f"  pasada {i+1}: {dt:.1f}s → {ok}")
        if d:
            print(f"    techs: {d.get('techs')}")
            print(f"    modalidad: {d.get('modalidad')} | seniority: {d.get('seniority_real')} | rol: {d.get('rol_categoria')}")
            print(f"    opinion: {(d.get('opinion') or '')[:100]}")

if __name__ == "__main__":
    modelo = sys.argv[1] if len(sys.argv) > 1 else "madkoding/ornith-1.5-9b-q2k:latest"
    veces = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    probar(modelo, veces)
