"""Tests del paquete jobhunt/fetch (refactor de estructura, PASO 4)."""
from __future__ import annotations

import pathlib
import re

from jobhunt.fetch.page import parse_jobposting


def test_parse_jobposting_jsonld_minimo():
    html = (
        '<html><script type="application/ld+json">'
        '{"@type":"JobPosting","title":"Dev Python",'
        '"description":"<p>Se busca Dev</p>",'
        '"baseSalary":{"currencyCode":"CLP","value":{"value":2000000,"unitText":"MONTH"}}}'
        '</script></html>'
    )
    info = parse_jobposting(html, "http://x.cl/oferta")
    assert info["salary"] == "CLP 2000000/mes"
    assert "Se busca Dev" in info["description"]


def test_arquitectura_fetch_no_importa_de_modulos_viejos():
    prohibido = re.compile(
        r"from\s+\.\.(enrich|channel|bot|cli|scoring|db|stats)\s+import"
        r"|from\s+jobhunt\.(enrich|channel|bot|cli|scoring|db|stats)\b")
    d = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "fetch"
    ofensores = [f.name for f in d.glob("*.py") if prohibido.search(f.read_text(encoding="utf-8"))]
    assert not ofensores, f"import prohibido en fetch/: {ofensores}"


def test_enrich_viejo_sigue_reexportando():
    from jobhunt import enrich as en
    assert en.fetch_page is not None
    assert callable(en.parse_jobposting)
    assert callable(en._extract_techs)
