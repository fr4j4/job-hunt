import json
import subprocess
import time

# 1. Descargar TODOS los modelos de VRAM
subprocess.run(["curl", "-s", "-X", "POST", "http://localhost:11434/api/generate",
                "-d", '{"model": "qwen3:4b", "keep_alive": 0}'], capture_output=True)
subprocess.run(["curl", "-s", "-X", "POST", "http://localhost:11434/api/generate",
                "-d", '{"model": "qwen2.5:7b", "keep_alive": 0}'], capture_output=True)
subprocess.run(["curl", "-s", "-X", "POST", "http://localhost:11434/api/generate",
                "-d", '{"model": "madkoding/ornith-1.5-9b-q2k:latest", "keep_alive": 0}'], capture_output=True)
subprocess.run(["curl", "-s", "-X", "POST", "http://localhost:11434/api/generate",
                "-d", '{"model": "madkoding/ornith-1.5-9b-iq2m:latest", "keep_alive": 0}'], capture_output=True)
time.sleep(8)

# 2. Estado GPU
out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free",
                      "--format=csv,noheader"], capture_output=True, text=True)
print("GPU:", out.stdout.strip())
out = subprocess.run(["ollama", "ps"], capture_output=True, text=True)
print("ollama ps:", out.stdout.strip() or "(vacío)")
