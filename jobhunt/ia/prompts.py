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
             '"resumen": "max 200 chars", "fit_reason": "max 200 chars por qué conviene o no al perfil", '
             '"opinion": "max 300 chars — comentario editorial sobre la oferta: contexto de mercado, señal notable (empresa conocida, staffing, nicho escaso), comparación con la mediana salarial o red flag relevante. PROHIBIDO consejos al candidato (nada de destaca/pregunta/no apliques). PROHIBIDO frases genéricas de relleno ("oferta fuera de rango", "falta sueldo para valoración"): di QUÉ falta y QUÉ se infiere de lo disponible. No repitas el resumen"}')


_PROMPT_EXTRACT_LOCAL = (
    "Eres un extractor de datos de ofertas de empleo chilenas.\n"
    "RESPONDE SOLO JSON VALIDO - sin texto, sin markdown, sin comillas de bloque.\n\n"
    "FORMATO EXACTO DE SALIDA:\n"
    "{{\"techs\": [], \"modalidad\": \"\", \"seniority_real\": \"\", \"rol_categoria\": \"\", "
    "\"ingles\": \"\", \"idiomas\": [], \"red_flags\": [], \"green_flags\": [], "
    "\"benefits\": [], \"salario_clp_mensual\": 0}}\n\n"
    "REGLAS:\n"
    "- techs: SOLO tecnologias escritas literalmente en el titulo o la descripcion de la oferta. "
    "NUNCA agregues otras, aunque sean obvias o tipicas del rol. Maximo 8 elementos. "
    "Usa la abreviatura corta estandar si existe (ej: Py, K8s, TS, JS, TF); si no, usa el nombre "
    "tal como aparece en el texto.\n"
    "- modalidad: solo \"R\" (remoto), \"H\" (hibrido), \"P\" (presencial), \"?\" (no determinable).\n"
    "- seniority_real: solo \"junior\", \"semi\", \"senior\", \"lead\" o \"\" (vacio si no se "
    "puede inferir).\n"
    "- rol_categoria: solo uno de: \"Full Stack\", \"Backend\", \"Frontend\", \"Data\", \"Mobile\", "
    "\"AI/ML\", \"Tech Lead\", \"DevOps/Cloud\", \"QA\", \"Software\", \"Seguridad\", "
    "\"Ingenieria no-software\", \"Analista/Empresa\", \"Profesor/Formacion\", \"Soporte/TI\", "
    "\"No-tech\", \"Otro\".\n"
    "- ingles: solo \"no\", \"deseable\", \"requerido\", \"desconocido\".\n"
    "- salario_clp_mensual: numero entero, 0 si no se declara.\n"
    "- idiomas, red_flags, green_flags, benefits: arrays de strings; vacios si no aplica.\n"
    "- Dato ausente: nunca inventes.\n\n"
    "EJEMPLOS (imita el FORMATO, no el contenido):\n"
    "Oferta 1:\nTitulo: Senior Backend Developer Java\nEmpresa: Acme\nUbicacion: Santiago\n"
    "Sueldo declarado: 3000000\nModalidad declarada: remoto\n"
    "Descripcion: Buscamos Java 17 y Spring Boot, PostgreSQL, microservicios con Kafka y "
    "despliegue en AWS EKS.\n"
    "Respuesta:\n"
    "{{\"techs\": [\"Java\", \"Spring\", \"Postgres\", \"Kafka\", \"AWS\", \"K8s\"], "
    "\"modalidad\": \"R\", \"seniority_real\": \"senior\", \"rol_categoria\": \"Backend\", "
    "\"ingles\": \"desconocido\", \"idiomas\": [], \"red_flags\": [], \"green_flags\": [], "
    "\"benefits\": [], \"salario_clp_mensual\": 3000000}}\n\n"
    "Oferta 2:\nTitulo: Ayudante de cocina\nEmpresa: Rest\nUbicacion: Valparaiso\n"
    "Sueldo declarado: (no declarado)\nModalidad declarada: (no declarada)\n"
    "Descripcion: Sin descripcion disponible.\n"
    "Respuesta:\n"
    "{{\"techs\": [], \"modalidad\": \"?\", \"seniority_real\": \"\", \"rol_categoria\": "
    "\"No-tech\", \"ingles\": \"desconocido\", \"idiomas\": [], \"red_flags\": [], "
    "\"green_flags\": [], \"benefits\": [], \"salario_clp_mensual\": 0}}\n\n"
    "=====\n"
    "Oferta actual:\n"
    "Titulo: {title}\nEmpresa: {company}\nUbicacion: {location}\n"
    "Sueldo declarado: {salary}\nModalidad declarada: {modality}\n"
    "Descripcion: {description}\n\n"
    "Responde SOLO el JSON."
)

_PROMPT_OPINION_LOCAL = (
    "Eres un comentarista editorial de ofertas de empleo tech chilenas. REGLAS "
    "OBLIGATORIAS (violarlas invalida la respuesta):\n"
    "1. Respondes EXCLUSIVAMENTE un objeto JSON válido. Nada más, sin texto, sin markdown.\n"
    "2. opinion: max 300 chars. resumen: max 200 chars. fit_reason: max 200 chars.\n"
    "3. opinion = comentario editorial: contexto de mercado, señal notable, comparación "
    "salarial. NUNCA consejos al candidato (prohibido 'destaca', 'pregunta', "
    "'no apliques', 'practica').\n"
    "4. ANTI-ALUCINACIÓN: los únicos números de mercado que puedes citar son los del "
    "CONTEXTO DE MERCADO provisto. Si la muestra es insuficiente, describe solo la oferta.\n"
    "5. Si el sueldo está declarado, DEBE comentarlo (comparar contra la mediana del contexto).\n"
    "6. Si la oferta tiene nota de anomalía: cita el valor tal cual, señala la anomalía "
    "con la hipótesis provista, compara contra la mediana. NUNCA corrijas ni omitas.\n"
    "7. fit_reason: por qué conviene o no al PERFIL del candidato provisto.\n"
    "8. Si la descripción es corta o ausente: comenta lo que puedas inferir del título, "
    "empresa, sueldo y datos extraídos. NUNCA inventes detalles de la oferta ni números "
    "fuera del contexto provisto.\n"
    "9. PROHIBIDO frases genéricas de relleno ('oferta fuera de rango', 'falta sueldo para "
    "valoración', 'sin información suficiente'): di QUÉ falta y QUÉ se puede inferir de lo "
    "disponible.\n"
    "10. resumen: texto en lenguaje natural (máx 200 chars). PROHIBIDO volcar el JSON de "
    "entrada ni listar campos con '=' o 'techs=['.\n"
    "11. PROHIBIDO citar nombres de campos internos del JSON (salario_clp_mensual, "
    "seniority_real, techs, etc.) — refiérete a los datos en lenguaje natural "
    "('el sueldo', 'la seniority', 'las tecnologías').\n"
    "12. NUNCA repitas los ejemplos de respuesta de abajo ni sus frases literales. Los "
    "ejemplos solo muestran el FORMATO; el contenido de tu opinion/resumen/fit_reason debe "
    "describir ESTA oferta concreta (empresa, título, sueldo, stack, señales de este "
    "anuncio). Si no tienes datos específicos, di exactamente qué falta.\n\n"
    "Perfil del candidato: {perfil}\n\n"
    "Contexto de mercado (los ÚNICOS números que puedes citar):\n{mercado}\n\n"
    "Oferta:\nTítulo: {title}\nEmpresa: {company}\nSueldo declarado: {salary}\n"
    "Datos extraídos: {extraidos}\n{nota}\n\n"
    "Responde SOLO JSON con EXACTAMENTE estas claves: opinion, resumen, fit_reason.\n\n"
    "Ejemplo de respuesta VÁLIDA (empresa y cifras inventadas — nunca las repitas):\n"
    "{{\"opinion\": \"GlobalLogic Chile publica un rol Go/AWS sin rango salarial; el contexto "
    "muestra una mediana de 4.2M pero sin sueldo declarado no hay comparación posible. "
    "Señal: empresa de staffing con equipo local.\", "
    "\"resumen\": \"Rol Go/AWS en GlobalLogic, sin sueldo declarado.\", "
    "\"fit_reason\": \"Stack Go/AWS coincide; falta el salario para evaluar.\"}}\n\n"
    "Ejemplo para oferta SIN descripción (imita este patrón, no frases genéricas):\n"
    "{{\"opinion\": \"Rol de ayudante de sondaje (minería) publicado en jooble; sin descripción \"\n"
    "\"ni sueldo disponibles. El título indica operación minera, no desarrollo de software — \"\n"
    "\"no aplica al perfil tech. No hay datos para comparar salario.\", "
    "\"resumen\": \"Ayudante de sondaje, minería; sin descripción ni sueldo.\", "
    "\"fit_reason\": \"Rol no-tech, sin stack ni modalidad; no coincide con el perfil.\"}}\n\n"
    "Ejemplo para oferta CON descripción pero SIN sueldo (imita este patrón):\n"
    "{{\"opinion\": \"Backend Java/Spring en empresa de retail, sin rango salarial declarado. \"\n"
    "\"El stack coincide con el perfil; la modalidad híbrida es aceptable. Sin sueldo no hay \"\n"
    "\"comparación contra la mediana del mercado.\", "
    "\"resumen\": \"Backend Java/Spring, retail, híbrido; sin sueldo declarado.\", "
    "\"fit_reason\": \"Stack y seniority coinciden; falta el dato salarial para evaluar la oferta.\"}}"
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
    "omitas. Prohibido comentar anomalías de OTRAS ofertas. "
    "Si la descripción es corta o ausente: comenta lo que puedas inferir "
    "del título, empresa, sueldo y datos extraídos; NUNCA inventes detalles "
    "ni números fuera del contexto provisto. PROHIBIDO frases genéricas de "
    "relleno ('oferta fuera de rango', 'falta sueldo para valoración'): di "
    "QUÉ falta y QUÉ se infiere de lo disponible. opinion max 300 chars, "
    "resumen max 200 chars, fit_reason max 200 chars.")


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
