"""Salud de fuentes, señal de perfil, backfill de encaje y /fuentes."""
import json
import sqlite3

from jobhunt import db as database
from jobhunt import enrich, salud
from jobhunt.config import load_config
from jobhunt.scoring import compute_score, tiene_senal_perfil
from jobhunt.sources import laborum


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    database.init_db(conn)
    return conn


def _scan(conn, fuentes, canal=0):
    conn.execute("INSERT INTO scan_log (ts, total_seen, new_count, sources_summary, channel_posts) "
                 "VALUES ('2026-10-08T00:00:00', 1, 1, ?, ?)", (json.dumps(fuentes), canal))
    conn.commit()


def _cfg():
    c = load_config()
    c.alerts.source_sweeps = 3
    c.channel.enabled, c.channel.chat_id, c.channel.silence_sweeps = True, "-100", 3
    return c


def test_alerta_fuente_en_cero_solo_al_alcanzar_umbral():
    conn, c = _conn(), _cfg()
    ok, muerta = {"n": 5, "err": 0}, {"n": 0, "err": 1}
    _scan(conn, {"linkedin": ok, "indeed": muerta})
    _scan(conn, {"linkedin": ok, "indeed": muerta})
    assert salud.fuentes_a_alertar(conn, c) == {}              # 2 < 3
    _scan(conn, {"linkedin": ok, "indeed": muerta})
    assert salud.fuentes_a_alertar(conn, c) == {"indeed": 3}   # justo al umbral
    _scan(conn, {"linkedin": ok, "indeed": muerta})
    assert salud.fuentes_a_alertar(conn, c) == {}              # no repite en cada barrido
    _scan(conn, {"linkedin": ok, "indeed": {"n": 2, "err": 0}})
    assert salud.fuentes_a_alertar(conn, c) == {}              # se recuperó


def test_canal_en_silencio_avisa_una_vez():
    conn, c = _conn(), _cfg()
    for _ in range(2):
        _scan(conn, {}, canal=0)
    assert salud.canal_en_silencio(conn, c) == 0
    _scan(conn, {}, canal=0)
    assert salud.canal_en_silencio(conn, c) == 3
    _scan(conn, {}, canal=0)
    assert salud.canal_en_silencio(conn, c) == 0


def test_alertar_admin_envia_y_texto_fuentes():
    conn, c = _conn(), _cfg()
    c.telegram.bot_token, c.telegram.chat_id = "t", "1"
    for _ in range(3):
        _scan(conn, {"indeed": {"n": 0, "err": 1}, "laborum": {"n": 7, "err": 0}})
    enviados = []
    assert salud.alertar_admin(conn, c, lambda m, p: enviados.append(p) or {"ok": True})
    assert "indeed" in enviados[0]["text"]
    txt = salud.texto_fuentes(conn, c)
    assert "🔴" in txt and "indeed" in txt and "🟢" in txt


def test_senal_de_perfil():
    c = load_config()
    c.profile.techs, c.profile.roles, c.profile.red_keywords = ["python"], ["backend"], []
    _, bd = compute_score({"title": "Ejecutivo de Cuentas", "location": "Santiago"}, c)
    assert not tiene_senal_perfil(bd)
    _, bd = compute_score({"title": "Desarrollador Python", "location": "Santiago"}, c)
    assert tiene_senal_perfil(bd)
    _, bd = compute_score({"title": "Analista Backend", "location": "Santiago"}, c)
    assert tiene_senal_perfil(bd)


def test_backfill_encaje(monkeypatch):
    conn, c = _conn(), load_config()
    for i, (t, ia) in enumerate([("Dev Python", "m"), ("Cajero", "m"), ("Sin IA", "")]):
        gid, _ = database.upsert(conn, {"title": t, "company": "X", "url": f"https://x/{i}",
                                        "source": "t", "date": "2026-10-01"}, "2026-10-01T00:00:00")
        conn.execute("UPDATE ofertas SET ia_model=? WHERE group_id=?", (ia, gid))
    veredictos = {"Dev Python": "alto", "Cajero": "ninguno"}
    monkeypatch.setattr(enrich, "ia_encaje", lambda cfg, job, p: veredictos.get(job["title"], ""))
    assert enrich.backfill_encaje(conn, c) == (2, 0)
    got = dict(conn.execute("SELECT title, ai_encaje FROM ofertas").fetchall())
    assert got == {"Dev Python": "alto", "Cajero": "ninguno", "Sin IA": ""}
    assert enrich.backfill_encaje(conn, c) == (0, 0)   # idempotente


def test_ia_encaje_normaliza(monkeypatch):
    c = load_config()
    c.ia.local_enabled = True
    monkeypatch.setattr(enrich, "_llm_local", lambda cfg, p: ({"encaje": " Medio "}, ""))
    assert enrich.ia_encaje(c, {"title": "x"}, "perfil") == "medio"
    monkeypatch.setattr(enrich, "_llm_local", lambda cfg, p: ({"encaje": "genial"}, ""))
    assert enrich.ia_encaje(c, {"title": "x"}, "perfil") == ""
    monkeypatch.setattr(enrich, "_llm_local", lambda cfg, p: (None, "other"))
    assert enrich.ia_encaje(c, {"title": "x"}, "perfil") == ""


def test_laborum_fetch_detail_sin_red(monkeypatch):
    monkeypatch.setattr(laborum, "_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("red")))
    assert laborum.fetch_detail(1)["description"] == ""


def test_errores_por_fuente_se_cuentan_y_limpian(monkeypatch):
    from jobhunt.sources import errores, indeed
    errores.reset()
    monkeypatch.setattr(indeed.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("403")))
    assert indeed.jobs(["python"], "t:", max_pages=2) == []
    assert errores.tomar("indeed") == 1
    assert errores.tomar("indeed") == 0          # tomar limpia


def test_cmd_run_registra_errores_en_salud(monkeypatch, tmp_path):
    import jobhunt.cli as cli
    from jobhunt.sources import errores, linkedin
    c = load_config()
    c.data_dir = tmp_path
    monkeypatch.setattr(type(c), "db_path", property(lambda self: tmp_path / "t.sqlite"), raising=False)
    for k in c.sources:
        c.sources[k] = (k == "linkedin")
    c.search.mode, c.search.queries_linkedin = "profile", ["python"]
    c.ia.enabled, c.channel.chat_id = False, ""
    def _caida(*a, **k):
        errores.registrar("linkedin")
        return []
    monkeypatch.setattr(linkedin, "fetch_jobs", _caida)
    cli.cmd_run(c, notify=False)
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    resumen = json.loads(conn.execute("SELECT sources_summary FROM scan_log").fetchone()[0])
    assert resumen["linkedin"] == {"n": 0, "err": 1}


def test_bot_encaje_async(monkeypatch):
    import jobhunt.bot as bot
    enviados = []
    monkeypatch.setattr(bot, "_tg_api", lambda cfg, m, p: enviados.append(p.get("text", "")))
    monkeypatch.setattr(bot.database, "connect", lambda cfg: _conn_enc())
    monkeypatch.setattr(enrich, "backfill_encaje", lambda conn, cfg, n, on_progress=None: (2, 1))
    monkeypatch.setattr(bot.database, "rescore_all", lambda *a, **k: 5)
    bot._encaje_async(load_config(), 1, None)
    assert any("iniciado" in t for t in enviados) and any("2 asignados" in t and "rescore: 5" in t
                                                          for t in enviados)
    assert not bot._IA_STATE["running"]


def _conn_enc():
    conn = _conn()
    gid, _ = database.upsert(conn, {"title": "Dev", "company": "X", "url": "https://x/9",
                                    "source": "t", "date": "2026-10-01"}, "2026-10-01T00:00:00")
    conn.execute("UPDATE ofertas SET ia_model='m' WHERE group_id=?", (gid,))

    class _SinClose:
        def __init__(self, c): self._c = c
        def __getattr__(self, n): return getattr(self._c, n)
        def close(self): pass
    return _SinClose(conn)
