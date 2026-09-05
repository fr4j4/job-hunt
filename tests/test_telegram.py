"""Tests del paquete jobhunt/telegram (refactor de estructura, PASO 5)."""
from __future__ import annotations

import pathlib
import re

from jobhunt.telegram.api import TelegramClient


class _FakeResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


def test_call_ok(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append((url, json, timeout))
        return _FakeResp(200, {"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("jobhunt.telegram.api.requests.post", fake_post)
    client = TelegramClient("tok", {123})
    resp = client.call("sendMessage", chat_id=123, text="hola")
    assert resp["ok"] is True
    assert calls[0][0] == "https://api.telegram.org/bottok/sendMessage"


def test_call_429_duerme_retry_after(monkeypatch):
    sleeps = []
    monkeypatch.setattr("jobhunt.telegram.api.time.sleep", lambda s: sleeps.append(s))

    responses = [
        _FakeResp(429, {"parameters": {"retry_after": 5}}),
        _FakeResp(200, {"ok": True}),
    ]
    monkeypatch.setattr("jobhunt.telegram.api.requests.post",
                        lambda *a, **k: responses.pop(0))
    client = TelegramClient("tok", {123})
    resp = client.call("sendMessage", chat_id=123)
    assert resp["ok"] is True
    assert sleeps == [5]


def test_call_429_retry_after_topeado_a_30(monkeypatch):
    sleeps = []
    monkeypatch.setattr("jobhunt.telegram.api.time.sleep", lambda s: sleeps.append(s))
    responses = [
        _FakeResp(429, {"parameters": {"retry_after": 999}}),
        _FakeResp(200, {"ok": True}),
    ]
    monkeypatch.setattr("jobhunt.telegram.api.requests.post",
                        lambda *a, **k: responses.pop(0))
    client = TelegramClient("tok", {123})
    client.call("sendMessage", chat_id=123)
    assert sleeps == [30]


def test_call_chat_no_permitido():
    client = TelegramClient("tok", {111})
    try:
        client.call("sendMessage", chat_id=999)
        assert False, "debía lanzar PermissionError"
    except PermissionError:
        pass


def test_arquitectura_telegram_no_importa_de_modulos_viejos():
    prohibido = re.compile(
        r"from\s+\.\.(enrich|channel|bot|cli|scoring|db|stats)\s+import"
        r"|from\s+jobhunt\.(enrich|channel|bot|cli|scoring|db|stats)\b")
    d = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "telegram"
    ofensores = [f.name for f in d.glob("*.py") if prohibido.search(f.read_text(encoding="utf-8"))]
    assert not ofensores, f"import prohibido en telegram/: {ofensores}"


def test_notify_viejo_sigue_reexportando():
    from jobhunt import notify
    assert callable(notify.esc)
    assert callable(notify.table_block)
    assert callable(notify.send_digest)
