"""Cruce: archivo cola_enrich_ids.txt vs estado actual de la DB."""
import sqlite3

ids = [l.strip() for l in open("data/cola_enrich_ids.txt") if l.strip()]
print(f"IDs en archivo: {len(ids)}")

conn = sqlite3.connect("data/ofertas.sqlite")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT group_id, ia_model, active FROM ofertas
    WHERE group_id IN ({})
""".format(",".join("?" * len(ids))), ids).fetchall()
por_id = {r["group_id"]: r for r in rows}

procesado_local = 0
procesado_cloud = 0
en_cola = 0
inactiva = 0
no_encontrada = 0
procesados_ids = []

for gid in ids:
    r = por_id.get(gid)
    if r is None:
        no_encontrada += 1
    elif r["active"] == 0:
        inactiva += 1
    elif "qwen" in r["ia_model"]:
        procesado_local += 1
        procesados_ids.append(gid)
    elif "ornith" in r["ia_model"]:
        procesado_local += 1
        procesados_ids.append(gid)
    elif r["ia_model"]:
        procesado_cloud += 1
    else:
        en_cola += 1

print(f"Procesados con IA local (qwen/ornith): {procesado_local}")
print(f"Procesados con cloud: {procesado_cloud}")
print(f"Siguen en cola: {en_cola}")
print(f"Inactivas: {inactiva}")
print(f"Ya no existen en DB: {no_encontrada}")

# Escribir pendientes al archivo (los que siguen sin IA)
pendientes = [gid for gid in ids if not por_id.get(gid) or
              (por_id[gid]["active"] == 1 and not por_id[gid]["ia_model"])]
with open("data/cola_enrich_ids.txt", "w") as f:
    for gid in pendientes:
        f.write(gid + "\n")
print(f"\nArchivo actualizado: quedan {len(pendientes)} pendientes")

# Ejemplo de procesados
print(f"\nEjemplos procesados con local: {procesados_ids[:5]}")

conn.close()
