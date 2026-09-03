"""Backfill de idiomas V2: regex primero (gratis) + IA en lote solo para lo ambiguo.

Capa 1 — regex sobre descripción: clasifica inequívocos sin IA
         (excluyente/deseable/no-requerido + otros idiomas con nivel)
Capa 2 — lote IA: hasta 20 ofertas ambiguas por llamada, con ventana de
         contexto ±300 chars alrededor de la mención (no la desc completa)
Capa 3 — sin idioma detectado por regex → queda sin ai_idiomas (vacío = nada pedido)

Uso: .venv/bin/python -m jobhunt.backfill_idiomas
"""
from __future__ import annotations

import json
import re
import time

from .config import load_config
from .logging_setup import get_logger

log = get_logger("jobhunt.backfill_idiomas")

# ---------------------------------------------------------------- regex layer

RE_MENCION = re.compile(
    r"(ingl[eé]s|english|alem[aá]n|german|deutsch|franc[eé]s|french|fran[cç]ais|"
    r"portugu[eé]s|portuguese|chino|mandar[ií]n|japon[eé]s|italiano)", re.I)

RE_NIVEL = {
    "nativo": r"nativo|native",
    "fluido": r"fluido|fluent|biling[uü]e|bilingual",
    "avanzado": r"avanzado|advanced|c1|c2|full professional",
    "intermedio": r"intermedio|intermediate|\bb2\b",
    "básico": r"b[aá]sico|basic|\ba2\b|\bb1\b",
}
RE_EXCLUYENTE = re.compile(
    r"(excluyente|obligatorio|required|requirement|imprescindible|"
    r"dominio (?:de|del) (?:el )?idioma|se requiere)", re.I)
RE_DESEABLE = re.compile(r"(deseable|nice to have|plus|deseable no excluyente|valorable|se valora)", re.I)
RE_NO_REQ = re.compile(r"no (?:se )?requiere ingl|english not required|no es necesario", re.I)

OTRO_IDIOMA = {"alemán": "AL", "aleman": "AL", "german": "AL", "deutsch": "AL",
               "francés": "FR", "frances": "FR", "french": "FR",
               "portugués": "PT", "portugues": "PT", "portuguese": "PT",
               "chino": "CN", "mandarín": "CN", "mandarin": "CN",
               "japonés": "JP", "japones": "JP", "italiano": "IT"}


def _nivel_de(texto: str) -> str:
    for nivel, pat in (("nativo", "nativo"), ("fluido", "fluido"), ("avanzado", "avanzado"),
                       ("intermedio", "intermedio"), ("básico", "básico")):
        if re.search(RE_NIVEL[nivel], texto, re.I):
            return nivel
    return ""


def clasifica_regex(desc: str) -> tuple[list[dict], bool]:
    """Retorna (idiomas_detectados, ambiguo).

    ambiguo=True → la mención existe pero el regex no alcanza a decidir
    excluyente/deseable → mandar al lote IA.
    """
    if not desc:
        return [], False
    d = re.sub(r"\s+", " ", desc)
    idiomas: list[dict] = []
    ambigua = False
    for m in RE_MENCION.finditer(d):
        lang_raw = m.group(1).lower()
        # ventana de contexto ±300 chars
        ctx = d[max(0, m.start() - 300): m.end() + 300]
        # idioma en minúscula canónica
        lang = {"english": "inglés", "german": "alemán", "deutsch": "alemán",
                "french": "francés", "portuguese": "portugués",
                "mandarin": "chino", "mandarín": "mandarín"}.get(lang_raw.lower(), lang_raw.lower())
        # descartar menciones dentro de "no se requiere"
        ventana = d[max(0, m.start() - 80): m.end() + 120]
        if re.search(r"no (?:se )?requiere", ventana, re.I):
            continue
        if lang == "inglés":
            if re.search(RE_NIVEL["fluido"], ventana, re.I):
                nivel = "fluido"
            elif re.search(RE_NIVEL["avanzado"], ventana, re.I):
                nivel = "avanzado"
            elif re.search(RE_NIVEL["intermedio"], ventana, re.I):
                nivel = "intermedio"
            elif re.search(RE_NIVEL["básico"], ventana, re.I):
                nivel = "básico"
            else:
                nivel = ""
            if RE_EXCLUYENTE.search(ventana):
                excl = True
            elif RE_DESEABLE.search(ventana):
                excl = False
            elif nivel:
                excl = nivel in ("avanzado", "fluido", "nativo")
            else:
                # mención sin nivel ni excluyente → ambigua
                ambigua = True
                continue
            idiomas.append({"idioma": "inglés", "nivel": nivel or "intermedio", "excluyente": excl})
        else:
            # otros idiomas: presencia basta, excluyente si la ventana lo dice
            excl = bool(RE_EXCLUYENTE.search(ventana))
            nivel = ""
            for n_niv, pat in (("nativo", "nativo"), ("fluido", "fluido"), ("avanzado", "avanzado"),
                               ("intermedio", "intermedio"), ("básico", "básico")):
                if re.search(RE_NIVEL[n_niv], ventana, re.I):
                    nivel = n_niv
                    break
            else:
                nivel = ""
            idiomas.append({"idioma": lang, "nivel": nivel, "excluyente": excl})
    # dedup por idioma (queda el de mayor excluyencia)
    visto: dict[str, dict] = {}
    for i in idiomas:
        prev = visto.get(i["idioma"])
        if not prev or (i["excluyente"] and not prev["excluyente"]):
            visto[i["idioma"]] = i
    return list(visto.values()), ambigua


# ---------------------------------------------------------------- lote IA

BATCH_SCHEMA = ('{"resultados": [{"idx": numero, "idiomas": '
                '[{"idioma": "inglés|alemán|francés|portugués|…", "nivel": "básico|intermedio|avanzado|fluido|nativo", '
                '"excluyente": true|false}]}]}')


def _batch_ia(cfg, items: list[dict]) -> dict[int, list[dict]]:
    """items: [{idx, title, snippet}] → {idx: idiomas}. 1 llamada por lote de ≤20."""
    import requests
    if not items:
        return {}
    lista = "\n".join(f"[{it['idx']}] {it['title'][:70]}\n    contexto idioma: {it['ctx'][:400]}"
                      for it in items)
    prompt = (f"Clasifica el/los idiomas que cada oferta pide (segmento de texto que menciona idiomas).\n"
              f"{lista}\n\n"
              f"excluyente=true solo si el texto indica que es requisito obligatorio. "
              f"Si la mención es ambigua o solo 'se valora', excluyente=false. "
              f"Si no se pide idioma, idiomas=[]. "
              f"Responde SOLO JSON: {BATCH_SCHEMA}")
    try:
        req = requests.post(
            f"{cfg.ia.base_url}/chat/completions",
            json={"model": cfg.ia.model,
                  "messages": [
                      {"role": "system",
                       "content": "Eres un extractor de requisitos de idiomas de ofertas de empleo chilenas. "
                                  "Respondes SOLO JSON válido. No inventes idiomas."},
                      {"role": "user", "content": prompt}],
                  "temperature": 0.0, "format": "json"},
            timeout=cfg.ia.timeout,
            headers={"Authorization": f"Bearer {cfg.ia.api_key}", "Content-Type": "application/json"})
        out = json.loads(req.json()["choices"][0]["message"]["content"])
        res = {}
        for ritem in (out or {}).get("resultados") or []:
            try:
                res[int(ritem["idx"])] = [i for i in (ritem.get("idiomas") or [])
                                          if isinstance(i, dict) and i.get("idioma")]
            except (KeyError, TypeError, ValueError):
                continue
        return res
    except Exception as e:
        log.warning("batch idiomas falló: %s", e)
        return {}


def main() -> None:
    cfg = load_config()
    import sqlite3
    conn = sqlite3.connect(cfg.db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT group_id, title, description FROM ofertas "
        "WHERE active=1 AND length(description)>80 "
        "AND (ai_idiomas IS NULL OR ai_idiomas='') "
        "ORDER BY score DESC").fetchall()]
    print(f"backfill idiomas v2: {len(rows)} ofertas a procesar")

    # ---- CAPA 1: regex
    ambiguous: list[dict] = []
    regex_done = 0
    for r in rows:
        idiomas, ambigua = clasifica_regex(r["description"] or "")
        if idiomas and not ambigua:
            conn.execute("UPDATE ofertas SET ai_idiomas=? WHERE group_id=?",
                         (json.dumps(idiomas, ensure_ascii=False)[:400], r["group_id"]))
            regex_done += 1
        elif idiomas or ambigua:
            # hay mención pero el regex no bastó → lote IA
            d = re.sub(r"\s+", " ", r["description"] or "")
            m = RE_MENCION.search(d)
            ctx = d[max(0, m.start() - 300): m.end() + 300] if m else d[:600]
            ambiguous.append({"group_id": r["group_id"], "title": r["title"],
                              "ctx": ctx})
    conn.commit()
    print(f"capa regex: {regex_done} clasificadas sin IA · {len(ambiguous)} ambiguas → lote IA")

    # ---- CAPA 2: IA en lotes de 20
    ia_done = 0
    for start in range(0, len(ambiguous), 20):
        batch = ambiguous[start:start + 20]
        resultados = _batch_ia(cfg, [{"idx": i, "title": b["title"], "ctx": b["ctx"]}
                                     for i, b in enumerate(batch)])
        for idx, idiomas in resultados.items():
            gid = batch[idx]["group_id"]
            if idiomas:
                conn.execute("UPDATE ofertas SET ai_idiomas=? WHERE group_id=?",
                             (json.dumps(idiomas, ensure_ascii=False)[:400], gid))
                ia_done += 1
        conn.commit()
        print(f"  lote {start // 20 + 1}: +{len(resultados)} clasificadas")
        time.sleep(2)
    conn.close()
    print(f"listo: regex={regex_done} · IA={ia_done}")


if __name__ == "__main__":
    main()