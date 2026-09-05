import json
import sys
import time
import urllib.request

def llamar(modelo, prompt, think=False, timeout=240):
    body = json.dumps({
        "model": modelo,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
        "think": think,
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/generate", body,
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    dt = time.time() - t0
    ev = d.get("eval_count", 0)
    ed = d.get("eval_duration", 0) / 1e9
    tok_s = ev / ed if ed else 0
    print(f"  {dt:.1f}s | {ev} tok salida | {tok_s:.1f} tok/s | resp: {d.get('response','')[:120]!r}")
    return d.get("response", "")

print("=== qwen3:4b think=false (JSON trivial) ===")
llamar("qwen3:4b", 'Responde SOLO JSON: {"ok": true}', think=False)

print("\n=== qwen3:4b think=false (extract real) ===")
llamar("qwen3:4b",
       'Eres extractor de ofertas. REGLAS: responde SOLO JSON. Oferta: Desarrollador Backend Java '
       'Spring, Banco de Chile, sueldo CLP 2500000, remoto, inglés deseable. '
       'Responde SOLO JSON con: techs (abreviaturas max 8), modalidad ("R"|"H"|"P"|"?"), '
       'seniority_real, rol_categoria, ingles, salario_clp_mensual (0 si no se declara).',
       think=False)
