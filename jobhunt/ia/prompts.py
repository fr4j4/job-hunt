"""Prompts y schema textual de la IA (hoja: sin estado, sin DB)."""
from __future__ import annotations

from ..salarios.stats import annual_likely, parse_salary_clp


IA_SCHEMA = ('{"modalidad": "R"|"H"|"P"|"?", "salario_clp_mensual": numero|null, '
             '"ingles": "no"|"deseable"|"requerido"|"desconocido", "years_exp": numero|null, '
             '"seniority_real": "junior"|"semi"|"senior"|"lead", '
             '"techs": ["Py","Java","AWS","React","Angular","K8s","Docker","SQL","Node","TS","NiFi","Spring"], '
             '"red_flags": ["..."], "green_flags": ["..."], "benefits": ["..."], '
             '"idiomas": [{"idioma": "inglés|alemán|francés|portugués|chino|japonés|italiano|otro", "nivel": "básico|intermedio|avanzado|nativo|fluido", "excluyente": true|false}], '
             '"rol_categoria": "Full Stack"|"Backend"|"Frontend"|"Data"|"Mobile"|"AI/ML"|"Tech Lead"|"DevOps/Cloud"|"QA"|"Software"|"Seguridad"|"Ingeniería no-software"|"Analista/Empresa"|"Profesor/Formación"|"Soporte/TI"|"No-tech"|"Otro", '
             '"resumen": "max 120 chars", "fit_reason": "max 140 chars por qué conviene o no al perfil", '
             '"opinion": "max 160 chars — comentario editorial sobre la oferta: contexto de mercado, señal notable (empresa conocida, staffing, nicho escaso), comparación con la mediana salarial o red flag relevante. PROHIBIDO consejos al candidato (nada de destaca/pregunta/no apliques). No repitas el resumen"}')


_PROMPT_EXTRACT_LOCAL = (
    "Eres un extractor de datos de ofertas de empleo chilenas. REGLAS OBLIGATORIAS "
    "(violarlas invalida la respuesta):\n"
    "1. Respondes EXCLUSIVAMENTE un objeto JSON válido. Nada más, sin texto, sin markdown.\n"
    "2. modalidad SOLO puede ser uno de estos 4 valores exactos: \"R\" (remoto), "
    "\"H\" (híbrido), \"P\" (presencial), \"?\" (no se puede determinar). "
    "PROHIBIDO escribir la palabra completa.\n"
    "3. ingles SOLO puede ser: \"no\", \"deseable\", \"requerido\", \"desconocido\". "
    "PROHIBIDO true/false/null.\n"
    "4. seniority_real SOLO puede ser: \"junior\", \"semi\", \"senior\", \"lead\", o \"\" "
    "(vacío si no se puede inferir).\n"
    "5. salario_clp_mensual es un NÚMERO entero. 0 si no se declara. PROHIBIDO strings.\n"
    "6. Dato ausente → null o \"\" según el campo. Nunca inventes.\n\n"
    "Oferta:\nTítulo: {title}\nEmpresa: {company}\nUbicación: {location}\n"
    "Sueldo declarado: {salary}\nModalidad declarada: {modality}\n"
    "Descripción: {description}\n\n"
    "Responde SOLO JSON con EXACTAMENTE estas claves: techs (array de máx 8 "
    "abreviaturas: Py, Java, TS, JS, React, Angular, Vue, Node, K8s, Docker, AWS, "
    "GCP, Azure, TF, Scala, Go, .NET, C#, SQL, Postgres, Mongo, Redis, Kafka, "
    "FastAPI, Django, Spring, NiFi, Jenkins, CI/CD), modalidad (\"R\"|\"H\"|\"P\"|\"?\"), "
    "seniority_real (\"junior\"|\"semi\"|\"senior\"|\"lead\"|\"\"), rol_categoria (\"Full Stack\"|"
    "\"Backend\"|\"Frontend\"|\"Data\"|\"Mobile\"|\"AI/ML\"|\"Tech Lead\"|\"DevOps/Cloud\"|\"QA\"|"
    "\"Software\"|\"Seguridad\"|\"Ingeniería no-software\"|\"Analista/Empresa\"|"
    "\"Profesor/Formación\"|\"Soporte/TI\"|\"No-tech\"|\"Otro\"), ingles (\"no\"|\"deseable\"|"
    "\"requerido\"|\"desconocido\"), idiomas (array de {{idioma, nivel, excluyente}}), "
    "red_flags (array), green_flags (array), benefits (array), salario_clp_mensual "
    "(entero, 0 si no se declara).\n\n"
    "Ejemplo de respuesta VÁLIDA:\n"
    "{{\"techs\": [\"Java\", \"Spring\"], \"modalidad\": \"R\", \"seniority_real\": \"senior\", "
    "\"rol_categoria\": \"Backend\", \"ingles\": \"deseable\", \"idiomas\": [], "
    "\"red_flags\": [], \"green_flags\": [], \"benefits\": [], "
    "\"salario_clp_mensual\": 2500000}}"
)

_PROMPT_OPINION_LOCAL = (
    "Eres un comentarista editorial de ofertas de empleo tech chilenas. REGLAS "
    "OBLIGATORIAS (violarlas invalida la respuesta):\n"
    "1. Respondes EXCLUSIVAMENTE un objeto JSON válido. Nada más, sin texto, sin markdown.\n"
    "2. opinion: max 160 chars. resumen: max 120 chars. fit_reason: max 140 chars.\n"
    "3. opinion = comentario editorial: contexto de mercado, señal notable, comparación "
    "salarial. NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', "
    "'no apliques', 'practica').\n"
    "4. ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar son los del "
    "CONTEXTO DE MERCADO provisto. Si la muestra es insuficiente, describe solo la oferta.\n"
    "5. Si el sueldo está declarado, DEBE comentarlo (comparar contra la mediana del contexto).\n"
    "6. Si la oferta tiene nota de anomalía: cita el valor tal cual, señala la anomalía "
    "con la hipótesis provista, compara contra la mediana. NUNCA corrijas ni omitas.\n"
    "7. fit_reason: por qué conviene o no al PERFIL del candidato provisto.\n\n"
    "Perfil del candidato: {perfil}\n\n"
    "Contexto de mercado (los ÚNICOS números que puedes citar):\n{mercado}\n\n"
    "Oferta:\nTítulo: {title}\nEmpresa: {company}\nSueldo declarado: {salary}\n"
    "Datos extraídos: {extraidos}\n{nota}\n\n"
    "Responde SOLO JSON con EXACTAMENTE estas claves: opinion, resumen, fit_reason.\n\n"
    "Ejemplo de respuesta VÁLIDA:\n"
    "{{\"opinion\": \"Sueldo sobre la mediana del mercado; empresa reconocida.\", "
    "\"resumen\": \"Backend Java con AWS, remoto.\", "
    "\"fit_reason\": \"Stack coincide con el perfil; seniority adecuada.\"}}"
)

def _prompt_extract_local(job: dict) -> str:
    return _PROMPT_EXTRACT_LOCAL.format(
        title=job.get("title", ""), company=job.get("company", ""),
        location=job.get("location", ""),
        salary=job.get("salary") or "(no declarado)",
        modality=job.get("modality") or "(no declarada)",
        description=(job.get("description") or "")[:2000])


def _prompt_opinion_local(job: dict, perfil: str, mercado: str,
                          extraidos: dict, nota: str = "") -> str:
    extra = ", ".join(f"{k}={v}" for k, v in extraidos.items() if v not in ("", None, [], {}))
    return _PROMPT_OPINION_LOCAL.format(
        perfil=perfil, mercado=mercado, title=job.get("title", ""),
        company=job.get("company", ""), salary=job.get("salary") or "(no declarado)",
        extraidos=extra or "sin datos", nota=nota)


LOTE_SYSTEM = (
    "Eres un extractor de datos de ofertas de empleo chilenas. "
    "Respondes SOLO JSON válido. Dato ausente → null. No inventes. "
    "El campo 'opinion' es un comentario editorial sobre la oferta "
    "(contexto de mercado, señal notable, comparación salarial) — "
    "NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', "
    "'no apliques', 'practica'). "
    "Si el sueldo está declarado, la opinion DEBE comentarlo "
    "(comparar contra la mediana del CONTEXTO provisto); no digas 'sin salario' "
    "si el campo Sueldo declarado trae un valor. "
    "ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar en "
    "opinion son los del CONTEXTO DE MERCADO provisto arriba — prohibido "
    "citar medianas, percentiles o estadísticas de tu conocimiento propio "
    "o de otras fuentes. Si el contexto dice que la muestra es insuficiente, "
    "no compares salarios: describe solo la oferta. "
    "Si el sueldo de UNA oferta viene marcado como anómalo en su Nota, la "
    "opinion DEBE: (1) citar el valor declarado tal cual, (2) señalar la "
    "anomalía con la hipótesis provista (probable anual/error de fuente), "
    "(3) comparar contra la mediana provista. NUNCA corrijas el valor ni lo "
    "omitas. Prohibido comentar anomalías de OTRAS ofertas.")


def _lote_prompt(rows: list[dict], profile_desc: str, mercado: str) -> str:
    """Prompt de lote: N bloques --- OFERTA {i} --- con idx 1..N (spec §2.1)."""
    bloques = []
    for i, r in enumerate(rows, 1):
        nota = ""
        sal_esta = parse_salary_clp(r.get("salary") or "")
        if r.get("salary_status") in ("suspect", "implausible") and sal_esta > 0:
            if annual_likely(sal_esta, parse_salary_clp("CLP 2150000") or 2150000):
                hip = f"probable cifra anual (≈ ${sal_esta // 12:,}/mes)"
            else:
                hip = "error de la fuente"
            raw_mostrar = (r.get('salary_raw') or r.get('salary') or "").strip()
            monto_mostrar = raw_mostrar if raw_mostrar else f"{sal_esta:,}"
            nota = (f"\nNota: el sueldo declarado de esta oferta (${monto_mostrar}) "
                    f"fue clasificado anómalo (motivo: {r.get('salary_note') or 'estadística'}; "
                    f"hipótesis: {hip}) — coméntalo en opinion según las reglas.")
        bloques.append(
            f"--- OFERTA {i} ---\n"
            f"Título: {r.get('title', '')}\nEmpresa: {r.get('company', '')}\n"
            f"Ubicación: {r.get('location', '')}\n"
            f"Sueldo declarado: {r.get('salary') or '(no declarado — infiere rango de mercado solo si el texto lo permite)'}\n"
            f"Modalidad declarada: {r.get('modality') or '(no declarada)'}\n"
            f"Descripción: {(r.get('description') or '')[:2400]}{nota}")
    return (f"Perfil del candidato: {profile_desc}\n\n"
            f"Contexto de mercado (para el campo opinion): {mercado}\n\n"
            + "\n\n".join(bloques) +
            "\n\nResponde un JSON array con UN objeto por oferta (idx 1..N), cada uno con: "
            "idx, opinion, resumen, fit_reason, seniority_real, rol_categoria, ingles, idiomas, "
            "modalidad, salario_clp_mensual, techs, red_flags, green_flags, benefits. "
            "techs: lista de tecnologías detectadas en la descripción (máx 8, "
            "abreviadas: Py, Java, AWS, React, Angular, K8s, Docker, SQL, Node, TS, "
            "NiFi, Spring, GCP, Azure, Scala, Go, .NET, FastAPI, Kafka, Terraform, "
            "Postgres, Mongo, Redis, Vue, Jenkins, CI/CD). "
            "Si una oferta no declara salario, salario_clp_mensual = 0 (cero, nunca inventes un monto).")
