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


# ---------- tabla nativa (experimental) ----------

_OFS = [{"score": 82, "title": "Backend Python", "company": "Acme", "modality": "remoto",
         "salary": "CLP 3200000", "date_posted": "2026-10-07", "url": "https://x/1"},
        {"score": 60, "title": "Dev", "company": "", "modality": "", "salary": "",
         "date_posted": "", "url": ""}]


def test_bloque_tabla_sigue_la_doc():
    from jobhunt.telegram.rich import bloque_tabla
    b = bloque_tabla(_OFS)
    assert b["type"] == "table" and b["is_bordered"] is True and b["is_striped"] is True
    assert len(b["cells"]) == 3 and all(len(r) == 6 for r in b["cells"])
    assert all(c["is_header"] for c in b["cells"][0])
    assert not any("is_header" in c for r in b["cells"][1:] for c in r)
    assert {c["align"] for r in b["cells"] for c in r} <= {"left", "center", "right"}
    assert b["cells"][1][0]["text"] == "82%" and b["cells"][2][2]["text"] == "—"


def test_enviar_tabla_prueba_variantes_y_recuerda(monkeypatch):
    import jobhunt.telegram.rich as rich
    monkeypatch.setattr(rich, "_variante_ok", None)
    llamadas = []
    def tg(method, payload):
        llamadas.append((method, payload))
        if len(llamadas) == 1:
            raise RuntimeError("HTTP 400 {'description': 'Bad Request: invalid rich text'}")
        return {"ok": True}
    ok, errs = rich.enviar_tabla(tg, 1, _OFS)
    assert ok and len(errs) == 1 and "invalid rich text" in errs[0]
    assert llamadas[0][0] == "sendRichMessage" and rich._variante_ok == 1
    llamadas.clear()
    assert rich.enviar_tabla(lambda m, p: llamadas.append(p) or {"ok": True}, 1, _OFS)[0]
    assert "blocks" in llamadas[0]["rich_message"]
    assert isinstance(llamadas[0]["rich_message"]["blocks"][0]["cells"][0][0]["text"], dict)  # recordó la 2


def test_enviar_tabla_todas_fallan_devuelve_errores(monkeypatch):
    import jobhunt.telegram.rich as rich
    monkeypatch.setattr(rich, "_variante_ok", None)
    def tg(m, p): raise RuntimeError("HTTP 404 method not found")
    ok, errs = rich.enviar_tabla(tg, 1, _OFS)
    assert not ok and len(errs) == 3 and all("404" in e for e in errs)


def test_comando_tabla_cae_a_tarjetas_con_error(monkeypatch):
    import jobhunt.telegram.rich as rich
    enviados = []
    def api(cfg, m, p, retries=2):
        enviados.append((m, p))
        if m == "sendRichMessage":
            raise RuntimeError("HTTP 400 nope")
        return {"ok": True}
    monkeypatch.setattr(rich, "_variante_ok", None)
    monkeypatch.setattr(bot, "_tg_api", api)
    monkeypatch.setattr(bot, "_latest_offers", lambda cfg, n=10: _OFS)
    bot._handle_command(_cfg(), {"chat": {"id": 1}, "text": "/tabla 5"}, {})
    textos = [p["text"] for m, p in enviados if m == "sendMessage"]
    assert any("Backend Python" in t for t in textos) and any("no aceptó la tabla nativa" in t for t in textos)


def test_probar_variantes_no_corta_en_la_primera():
    from jobhunt.telegram.rich import probar_variantes
    n = []
    def tg(m, p):
        n.append(1)
        if len(n) == 2:
            raise RuntimeError("HTTP 400 mala")
        return {"ok": True}
    res = probar_variantes(tg, 1, _OFS)
    assert [ok for _, ok, _ in res] == [True, False, True] and "mala" in res[1][2]


# ---------- HTML válido y bajo el límite de Telegram (bug 'Unclosed start tag') ----------

class _Estricto(HTMLParser):
    """Falla ante etiquetas sin cerrar/desbalanceadas (lo que Telegram rechaza con 400)."""
    PERMITIDAS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote"}
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.pila = []
    def handle_starttag(self, t, a):
        assert t in self.PERMITIDAS, f"tag no soportada por Telegram: {t}"
        self.pila.append(t)
    def handle_endtag(self, t):
        assert self.pila and self.pila.pop() == t, f"cierre desbalanceado: {t}"
    def close(self):
        super().close()
        assert not self.pila, f"etiquetas sin cerrar: {self.pila}"


def _html_ok(texto):
    p = _Estricto()
    p.feed(texto)
    p.close()
    assert "<" not in re.sub(r"<[^<>]*>", "", texto), "'<' suelto sin escapar"
    assert len(texto) <= 4096, len(texto)


import re


def _ofertas_feas(n):
    return [{"score": 90 - i % 40, "group_id": f"g{i}",
             "title": f"Desarrollador <Senior> & Líder Técnico Full Stack Python/Java/AWS #{i} " * 3,
             "company": "Empresa & <Hijos> S.A. " * 3, "modality": "híbrido",
             "salary": "CLP 3200000", "techs": "Py;AWS;Docker", "seniority_real": "senior",
             "date_posted": "2026-10-07", "location": "Santiago, Chile",
             "url": "https://www.linkedin.com/jobs/view/" + "x" * 150 + f"?id={i}&ref=a\"b",
             "ai_idiomas": '[{"idioma":"inglés","excluyente":true}]', "ai_encaje": "alto",
             "ai_fit_reason": "Encaje <alto> & stack coincide " * 8, "ia_model": "m"}
            for i in range(n)]


def test_render_page_siempre_html_valido_y_bajo_el_limite():
    c = _cfg()
    ofertas = _ofertas_feas(60)
    for page_size in (1, 5, 10, 20, 50):
        total_vistas = 0
        pag = 0
        while True:
            r = bot.render_page(ofertas, pag, page_size, c)
            _html_ok(r["text"])
            total_vistas += r["text"].count("<a href=")
            nav = [b["callback_data"] for b in r["keyboard"][0]]
            if not any(cb.endswith(f":page:{pag + 1}") for cb in nav):
                break
            pag += 1
        # nada se pierde: cada oferta aparece en alguna página (la 'Mejor match' no es enlace)
        assert total_vistas == len(ofertas), (page_size, total_vistas)


def test_digest_y_post_html_validos():
    from jobhunt.telegram.render import build_digest_text
    c = _cfg()
    c.alerts.max_per_digest = 40
    _html_ok(build_digest_text(_ofertas_feas(40), c))
    from jobhunt.channel import render_offer_post
    texto, _ = render_offer_post({**_ofertas_feas(1)[0], "market_score": 80,
                                  "ai_opinion": "o <x> & y " * 30, "ai_resumen": "r & <z> " * 20,
                                  "ai_red_flags": '["a <b>","c & d"]'})
    _html_ok(texto)


def test_recortar_html_no_corta_tags_ni_entidades():
    from jobhunt.telegram.render import recortar_html
    base = '<b>' + 'a&amp;b ' * 200 + '</b>'
    for lim in (50, 51, 53, 100, 777):
        out = recortar_html(base, lim)
        _html_ok(out)
    sin_saltos = '<a href="https://x/' + "y" * 300 + '">texto largo</a> ' * 5
    _html_ok(recortar_html(sin_saltos, 120))
