"""Tests del paquete jobhunt/ia (refactor de estructura, PASO 3)."""
from __future__ import annotations

import json
import pathlib
import re

import requests

from jobhunt.ia.client import CloudClient, LocalClient


def _cfg():
    from jobhunt.config import load_config
    c = load_config()
    c.ia.api_key = "fake"
    c.ia.retries = 2
    c.ia.local_retries = 2
    return c


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_cloud_200_devuelve_dict(monkeypatch):
    payload = {"choices": [{"message": {"content": json.dumps({"opinion": "ok"})}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, payload))
    d, err = CloudClient(_cfg()).chat_json([{"role": "user", "content": "x"}])
    assert (d, err) == ({"opinion": "ok"}, "")


def test_cloud_429_reintenta_y_devuelve_rate(monkeypatch):
    cfg = _cfg()
    n = {"c": 0}

    def post(*a, **k):
        n["c"] += 1
        return _Resp(429, text="rate limited")

    monkeypatch.setattr(requests, "post", post)
    d, err = CloudClient(cfg).chat_json([{"role": "user", "content": "x"}])
    assert (d, err) == (None, "rate")
    assert n["c"] == cfg.ia.retries + 1


def test_cloud_timeout(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.Timeout("t")

    monkeypatch.setattr(requests, "post", boom)
    assert CloudClient(_cfg()).chat_json([]) == (None, "timeout")


def test_local_connection_refused_sin_retries(monkeypatch):
    n = {"c": 0}

    def boom(*a, **k):
        n["c"] += 1
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", boom)
    assert LocalClient(_cfg()).chat_json([]) == (None, "other")
    assert n["c"] == 1


def test_local_usa_endpoint_y_modelo_local(monkeypatch):
    cfg = _cfg()
    visto = {}

    def post(url, json=None, timeout=None, headers=None):
        visto.update(url=url, body=json, timeout=timeout, headers=headers)
        return _Resp(200, {"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(requests, "post", post)
    LocalClient(cfg).chat_json([{"role": "user", "content": "p"}],
                               response_format={"type": "json_object"})
    assert visto["url"] == f"{cfg.ia.local_base_url}/chat/completions"
    assert visto["body"]["model"] == cfg.ia.local_model
    assert visto["body"]["response_format"] == {"type": "json_object"}
    assert visto["timeout"] == cfg.ia.local_timeout


def test_local_extra_body_apaga_thinking(monkeypatch):
    """B1: LocalClient manda think:false + enable_thinking:false (Qwen3 con
    thinking devuelve content vacío — JSON fail). CloudClient NO los manda."""
    cfg = _cfg()
    visto = {}

    def post(url, json=None, timeout=None, headers=None):
        visto.update(body=json)
        return _Resp(200, {"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(requests, "post", post)
    LocalClient(cfg).chat_json([{"role": "user", "content": "p"}])
    assert visto["body"]["think"] is False
    assert visto["body"]["chat_template_kwargs"] == {"enable_thinking": False}

    visto.clear()
    CloudClient(cfg).chat_json([{"role": "user", "content": "p"}])
    assert "think" not in visto["body"]
    assert "chat_template_kwargs" not in visto["body"]


def test_arquitectura_ia_no_importa_de_modulos_viejos():
    prohibido = re.compile(
        r"from\s+\.\.(enrich|channel|bot|cli|scoring|db|stats)\s+import"
        r"|from\s+jobhunt\.(enrich|channel|bot|cli|scoring|db|stats)\b")
    d = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "ia"
    ofensores = [f.name for f in d.glob("*.py") if prohibido.search(f.read_text(encoding="utf-8"))]
    assert not ofensores, f"import prohibido en ia/: {ofensores}"


def test_enrich_viejo_sigue_reexportando():
    from jobhunt import enrich as en
    assert "modalidad" in en.IA_SCHEMA
    assert en._LOTE_SCHEMA["properties"]["ofertas"]["type"] == "array"
    assert en._coerce_salario("2.500.000") == 2_500_000
    assert en._normalizar_extract_local({"modalidad": "remoto"})["modalidad"] == "R"
    assert "FORMATO EXACTO DE SALIDA" in en._PROMPT_EXTRACT_LOCAL
    assert "NUNCA agregues otras" in en._PROMPT_EXTRACT_LOCAL
    assert "ANTI-ALUCINACIÓN" in en._PROMPT_OPINION_LOCAL
    assert "--- OFERTA 1 ---" in en._lote_prompt([{"title": "A"}], "p", "m")


def _body_enviado(monkeypatch, base_url):
    cfg = _cfg()
    cfg.ia.base_url = base_url
    seen = {}
    payload = {"choices": [{"message": {"content": "{}"}}]}

    def post(url, json=None, **k):
        seen.update(json)
        return _Resp(200, payload)

    monkeypatch.setattr(requests, "post", post)
    CloudClient(cfg).chat_json([{"role": "user", "content": "x"}])
    return seen


def test_temperature_se_omite_para_anthropic(monkeypatch):
    assert "temperature" not in _body_enviado(monkeypatch, "https://api.anthropic.com/v1")


def test_temperature_cero_para_otros_proveedores(monkeypatch):
    assert _body_enviado(monkeypatch, "https://ollama.com/v1")["temperature"] == 0


# ---- formato de Claude: JSON dentro de vallas ``` ----
import pytest

from jobhunt.ia.client import ajustar_temperature, parse_json_content


@pytest.mark.parametrize("texto,esperado", [
    ('{"a": 1}', {"a": 1}),                                   # ya válido: igual que json.loads
    ('[1, 2]', [1, 2]),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('```\n{"a": 1}\n```', {"a": 1}),
    ('```JSON\n{"a": [1, {"b": "x```y"}]}\n```', {"a": [1, {"b": "x```y"}]}),
    ('  ```json\n{"a": 1}\n```  \n', {"a": 1}),
    ('Aquí va:\n```json\n{"a": 1}\n```\nSaludos', {"a": 1}),  # prosa alrededor
    ('Claro. {"a": {"b": 2}} listo.', {"a": {"b": 2}}),
])
def test_parse_json_content_acepta(texto, esperado):
    assert parse_json_content(texto) == esperado


@pytest.mark.parametrize("texto", ["", "   ", "no hay json", "```json\n```", "```json\n{roto\n```"])
def test_parse_json_content_sigue_fallando_igual(texto):
    with pytest.raises(json.JSONDecodeError):
        parse_json_content(texto)


def test_parse_json_content_none_lanza_typeerror_como_json_loads():
    with pytest.raises(TypeError):
        parse_json_content(None)


def test_chat_json_con_vallas_devuelve_dict(monkeypatch):
    payload = {"choices": [{"message": {"content": '```json\n{"opinion": "ok"}\n```'}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, payload))
    d, err = CloudClient(_cfg()).chat_json([{"role": "user", "content": "x"}])
    assert (d, err) == ({"opinion": "ok"}, "")


def test_chat_json_basura_sigue_siendo_other(monkeypatch):
    payload = {"choices": [{"message": {"content": "lo siento, no puedo"}}]}
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, payload))
    cfg = _cfg(); cfg.ia.retries = 0
    assert CloudClient(cfg).chat_json([{"role": "user", "content": "x"}]) == (None, "other")


def test_temperature_de_extra_tambien_se_quita_en_anthropic(monkeypatch):
    cfg = _cfg(); cfg.ia.base_url = "https://api.anthropic.com/v1"
    seen = {}
    payload = {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: seen.update(json) or _Resp(200, payload))
    CloudClient(cfg).chat_json([{"role": "user", "content": "x"}], extra={"temperature": 0.3})
    assert "temperature" not in seen


def test_temperature_de_extra_se_respeta_fuera_de_anthropic(monkeypatch):
    cfg = _cfg(); cfg.ia.base_url = "https://ollama.com/v1"
    seen = {}
    payload = {"choices": [{"message": {"content": "{}"}}]}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: seen.update(json) or _Resp(200, payload))
    CloudClient(cfg).chat_json([{"role": "user", "content": "x"}], extra={"temperature": 0.3})
    assert seen["temperature"] == 0.3


def test_ajustar_temperature_helper():
    assert ajustar_temperature({"temperature": 0}, "https://api.anthropic.com/v1") == {}
    assert ajustar_temperature({"temperature": 0}, "https://x.ai/v1") == {"temperature": 0}


def test_market_ia_call_cloud_anthropic_sin_temperature_y_con_vallas(monkeypatch):
    from jobhunt import market
    cfg = _cfg(); cfg.ia.base_url = "https://api.anthropic.com/v1"; cfg.ia.local_enabled = False
    seen = {}
    payload = {"choices": [{"message": {"content": '```json\n{"bullets": ["a"]}\n```'}}]}
    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: seen.update(json) or _Resp(200, payload))
    assert market._ia_call(cfg, "hola", temperature=0.3) == {"bullets": ["a"]}
    assert "temperature" not in seen
