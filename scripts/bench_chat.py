import json
import time
import urllib.request

def chat(modelo, prompt, timeout=180):
    body = json.dumps({
        "model": modelo,
        "messages": [{"role": "user", "content": prompt, "think": False}],
        "stream": False,
        "options": {"temperature": 0},
    }).encode()
    req = urllib.request.Request("http://localhost:11434/api/chat", body,
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    dt = time.time() - t0
    m = d.get("message", {})
    ev = m.get("eval_count", 0)
    ed = m.get("eval_duration", 0) / 1e9
    # en chat, eval_count puede venir en usage
    print(f"  {dt:.1f}s | resp: {m.get('content','')[:150]!r}")
    print(f"  [usage: {json.dumps(d.get('prompt_eval_count'), default=str)} in]")
    # medir tok/s desde timing del server si viene
    ed2 = d.get("eval_duration", 0) / 1e9
    ec = d.get("eval_count", 0)
    if ec:
        print(f"  {ec} tok out | {ec/ed2:.1f} tok/s" if ed2 else "")

print("=== qwen3:4b chat think=false ===")
chat("qwen3:4b", "Responde SOLO JSON: {\"ok\": true}")
