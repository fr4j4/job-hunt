"""Calidad de datos comparativa: ornith (anteriores) vs qwen2.5:7b (actual)."""
import json
import sqlite3

conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row

# Separar por modelo
models = {}
rows = conn.execute("""
    SELECT group_id, title, ia_model, ctx_version, ai_opinion, ai_resumen, ai_fit_reason,
           techs, modality, seniority_real, rol_categoria, ai_ingles, ai_idiomas,
           salary, salary_status, ai_red_flags, ai_green_flags, ai_benefits,
           length(description) as desc_len
    FROM ofertas
    WHERE ia_model != ''
""").fetchall()

VAL_MOD = {"R", "H", "P", "?"}
VAL_SEN = {"junior", "semi", "senior", "lead", ""}
VAL_ROL = {"Full Stack", "Backend", "Frontend", "Data", "Mobile", "AI/ML", "Tech Lead",
           "DevOps/Cloud", "QA", "Software", "Seguridad", "Ingeniería no-software",
           "Analista/Empresa", "Profesor/Formación", "Soporte/TI", "No-tech", "Otro"}
VAL_ING = {"no", "deseable", "requerido", "desconocido"}

def parse_list(v):
    if not v:
        return []
    try:
        d = json.loads(v)
        return d if isinstance(d, list) else [d]
    except Exception:
        return [x.strip() for x in str(v).split(";") if x.strip()]

for r in rows:
    m = r["ia_model"]
    if "ornith" in m:
        m = "ornith-IQ2M"
    elif "qwen" in m:
        m = "qwen2.5:7b"
    else:
        m = "cloud"
    models.setdefault(m, []).append(r)

for m, rs in models.items():
    n = len(rs)
    print(f"\n{'='*70}\nMODELO: {m} — {n} registros\n{'='*70}")
    # Cobertura de campos
    campos = {
        "opinion": lambda r: bool(r["ai_opinion"]),
        "resumen": lambda r: bool(r["ai_resumen"]),
        "fit_reason": lambda r: bool(r["ai_fit_reason"]),
        "techs": lambda r: bool(r["techs"]),
        "modalidad (válida)": lambda r: r["modality"] in VAL_MOD,
        "seniority (válida)": lambda r: r["seniority_real"] in VAL_SEN,
        "rol (válido)": lambda r: r["rol_categoria"] in VAL_ROL,
        "ingles (válido)": lambda r: r["ai_ingles"] in VAL_ING,
        "red_flags": lambda r: bool(parse_list(r["ai_red_flags"])),
        "green_flags": lambda r: bool(parse_list(r["ai_green_flags"])),
        "benefits": lambda r: bool(parse_list(r["ai_benefits"])),
        "ctx_version": lambda r: bool(r["ctx_version"]),
    }
    for c, fn in campos.items():
        ok = sum(1 for r in rs if fn(r))
        print(f"  {c}: {ok}/{n} ({100*ok/n:.0f}%)")

    # Calidad de techs
    techs_totales = set()
    for r in rs:
        techs_totales.update(parse_list(r["techs"]))
    print(f"  techs únicas: {len(techs_totales)} → {sorted(techs_totales)[:15]}")

    # Opiniones vacías/genéricas
    cortas = sum(1 for r in rs if len((r["ai_opinion"] or "")) < 40)
    print(f"  opinions <40 chars: {cortas}/{n}")

    # Salario (anomalías)
    anom = sum(1 for r in rs if r["salary_status"] in ("implausible", "suspect"))
    print(f"  salarios anómalos: {anom}/{n}")

# Muestras representativas del modelo nuevo
print(f"\n{'='*70}\nMUESTRAS qwen2.5:7b (3 mejores por score)\n{'='*70}")
q = sorted([r for r in rows if "qwen" in r["ia_model"]],
           key=lambda r: r["desc_len"] or 0, reverse=True)
for r in q[:3]:
    print(f"\n--- {r['group_id'][:45]}")
    print(f"    título: {(r['title'] or '')[:55]}")
    print(f"    techs: {r['techs']}")
    print(f"    modality: {r['modality']} | seniority: {r['seniority_real']} | rol: {r['rol_categoria']} | inglés: {r['ai_ingles']}")
    print(f"    idiomas: {r['ai_idiomas']}")
    print(f"    opinion: {(r['ai_opinion'] or '')[:130]}")
    print(f"    resumen: {(r['ai_resumen'] or '')[:110]}")
    print(f"    fit: {(r['ai_fit_reason'] or '')[:110]}")
    print(f"    red: {r['ai_red_flags']} | green: {r['ai_green_flags']} | ben: {r['ai_benefits']}")

conn.close()
