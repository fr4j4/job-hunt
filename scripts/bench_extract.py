import json
import time
import urllib.request

PROMPT_EXTRACT = """Eres un extractor de datos de ofertas de empleo chilenas. REGLAS OBLIGATORIAS (violarlas invalida la respuesta):
1. Respondes EXCLUSIVAMENTE un objeto JSON válido. Nada más, sin texto, sin markdown.
2. modalidad SOLO puede ser uno de estos 4 valores exactos: "R" (remoto), "H" (híbrido), "P" (presencial), "?" (no se puede determinar). PROHIBIDO escribir la palabra completa.
3. ingles SOLO puede ser: "no", "deseable", "requerido", "desconocido". PROHIBIDO true/false/null.
4. seniority_real SOLO puede ser: "junior", "semi", "senior", "lead", o "" (vacío si no se puede inferir).
5. salario_clp_mensual es un NÚMERO entero. 0 si no se declara. PROHIBIDO strings.
6. Dato ausente → null o "" según el campo. Nunca inventes.

Oferta:
Título: Senior Data Engineer - Plataformas Cloud
Empresa: Ripley
Sueldo declarado: CLP 4200000
Descripción: Buscamos un Data Engineer senior con sólida experiencia en AWS (S3, Glue, EMR), Python, SQL y Spark. Diseñarás pipelines ETL/ELT para el área de retail, trabajarás con equipos de datos y negocio, y liderarás la migración de datos on-premise a la nube. Experiencia con Terraform, Airflow y Kafka es deseable. Trabajo híbrido (2 días presencial en Santiago). Inglés nivel intermedio.

Responde SOLO JSON con EXACTAMENTE estas claves: techs (array de máx 8 abreviaturas: Py, Java, AWS, React, Angular, K8s, Docker, SQL, Node, TS, NiFi, Spring, GCP, Azure, Scala, Go, .NET, FastAPI, Kafka, Terraform, Postgres, Mongo, Redis, Vue, Jenkins, CI/CD), modalidad ("R"|"H"|"P"|"?"), seniority_real ("junior"|"semi"|"senior"|"lead"|""), rol_categoria ("Full Stack"|"Backend"|"Frontend"|"Data"|"Mobile"|"AI/ML"|"Tech Lead"|"DevOps/Cloud"|"QA"|"Software"|"Seguridad"|"Ingeniería no-software"|"Analista/Empresa"|"Profesor/Formación"|"Soporte/TI"|"No-tech"|"Otro"), ingles ("no"|"deseable"|"requerido"|"desconocido"), salario_clp_mensual (entero, 0 si no se declara).

Ejemplo de respuesta VÁLIDA:
{"techs": ["Java", "Spring"], "modalidad": "R", "seniority_real": "senior", "rol_categoria": "Backend", "ingles": "deseable", "salario_clp_mensual": 2500000}"""

def benchmark(modelo, extra=None):
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content": PROMPT_EXTRACT, **({"think": False} if extra else {})}],
        "temperature": 0,
    }
    body.update(extra or {})
    req = urllib.request.Request("http://localhost:11434/v1/chat/completions",
                                 json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
        dt = time.time() - t0
        content = d["choices"][0]["message"]["content"]
        usage = d.get("usage", {})
        # validar JSON
        valido = False
        try:
            json.loads(content)
            valido = True
        except Exception:
            pass
        print(f"  {modelo}: {dt:.1f}s | {usage.get('completion_tokens','?')} tok | JSON {'✅' if valido else '❌'}")
        if not valido:
            print(f"    resp: {content[:150]!r}")
        else:
            print(f"    {content[:120]}")
    except Exception as e:
        print(f"  {modelo}: ERROR {e}")

benchmark("qwen3:4b", {"think": False})
