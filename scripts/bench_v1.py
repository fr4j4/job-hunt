import json
import time
import urllib.request

body = json.dumps({
    "model": "qwen3:4b",
    "messages": [{"role": "user", "content": "Responde SOLO JSON: {\"ok\": true}"}],
    "temperature": 0,
    "think": False,
}).encode()
req = urllib.request.Request("http://localhost:11434/v1/chat/completions", body,
                             {"Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    dt = time.time() - t0
    content = d["choices"][0]["message"]["content"]
    usage = d.get("usage", {})
    print(f"OK {dt:.1f}s | {usage.get('completion_tokens', '?')} tokens out")
    print(f"resp: {content[:200]!r}")
except Exception as e:
    print(f"ERROR: {e}")
