"""UX end-user: ayuda simple/admin, sugerencias, botones de acción rápida, post del canal."""
import json
from html.parser import HTMLParser

import jobhunt.bot as bot
from jobhunt import salud
from jobhunt.channel import render_offer_post
from jobhunt.config import load_config


class _V(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
    def handle_starttag(self, t, a): self.stack.append(t)
    def handle_endtag(self, t): assert self.stack.pop() == t


def _cfg():
    c = load_config()
    c.telegram.allowed_chats = (1,)
    return c


def test_help_simple_vs_admin():
    simple, admin = bot._help_text(), bot._help_text(admin=True)
    for t in (simple, admin):
        _V().feed(t)
    assert len(simple.splitlines()) < len(admin.splitlines()) / 2
    assert "/fuentes" in simple and "/help admin" in simple
    assert "/db_all_confirm" in admin and "/db_all_confirm" not in simple


def test_menu_sin_duplicados_y_con_limites_de_telegram():
    cmds = [c["command"] for c in bot._MENU_COMANDOS]
    assert len(cmds) == len(set(cmds)) <= 100
    assert all(1 <= len(c["description"]) <= 256 for c in bot._MENU_COMANDOS)
    assert {"fuentes", "encaje", "help", "latest"} <= set(cmds)
    assert cmds[0] == "latest"            # lo cotidiano primero


def test_sugerencia_de_comando():
    assert "/latest" in bot._sugerir_comando("/lastest")
    assert bot._sugerir_comando("/zzzzzz") == ""


def _capturar(monkeypatch):
    enviados = []
    monkeypatch.setattr(bot, "_tg_api", lambda cfg, m, p: enviados.append((m, p)) or {"ok": True})
    return enviados


def test_comando_desconocido_sugiere(monkeypatch):
    env = _capturar(monkeypatch)
    bot._handle_command(_cfg(), {"chat": {"id": 1}, "text": "/lastest"}, {})
    txt = env[-1][1]["text"]
    assert "No conozco" in txt and "/latest" in txt


def test_help_trae_botones_y_admin_no(monkeypatch):
    env = _capturar(monkeypatch)
    bot._handle_command(_cfg(), {"chat": {"id": 1}, "text": "/help"}, {})
    kb = json.loads(env[-1][1]["reply_markup"])
    assert {b["callback_data"] for r in kb["inline_keyboard"] for b in r} >= {"go:latest", "go:fuentes"}
    bot._handle_command(_cfg(), {"chat": {"id": 1}, "text": "/help admin"}, {})
    assert "reply_markup" not in env[-1][1] and "administración" in env[-1][1]["text"]


def test_boton_go_ejecuta_comando_y_respeta_allowlist(monkeypatch):
    env = _capturar(monkeypatch)
    llamados = []
    monkeypatch.setattr(bot, "_handle_command", lambda cfg, m, st: llamados.append(m["text"]))
    bot.handle_go(_cfg(), {"id": "q", "data": "go:fuentes", "message": {"chat": {"id": 1}}}, {})
    bot.handle_go(_cfg(), {"id": "q", "data": "go:fuentes", "message": {"chat": {"id": 999}}}, {})
    bot.handle_go(_cfg(), {"id": "q", "data": "go:db_all_confirm", "message": {"chat": {"id": 1}}}, {})
    assert llamados == ["/fuentes"]       # fuera de allowlist y comando peligroso: ignorados


def test_post_muestra_encaje_y_edad_humana():
    from datetime import date, timedelta
    ayer = (date.today() - timedelta(days=1)).isoformat()
    base = {"market_score": 82, "title": "Dev Python", "company": "X", "modality": "remoto",
            "location": "Santiago", "salary": "CLP 2500000", "techs": "Py", "ai_idiomas": "",
            "url": "", "first_seen": ayer, "date_posted": ayer, "source": "linkedin:x"}
    post, _ = render_offer_post({**base, "ai_encaje": "alto"})
    assert "⭐ 82/100  🎯 Encaje alto" in post and "📅 Ayer" in post and "/mes" in post
    post, _ = render_offer_post({**base, "ai_encaje": ""})
    assert "Encaje" not in post


def test_hace_legible():
    from datetime import datetime, timedelta, timezone
    f = lambda m: (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat()
    assert salud._hace(f(5)) == "hace 5 min" and salud._hace(f(180)) == "hace 3 h"
    assert salud._hace(f(60 * 50)) == "hace 2 días"
