import threading

from jobhunt.progreso import Limitador, Progreso


def test_render_una_linea_por_fuente_y_ia():
    p = Progreso()
    p.fuente("aira", "ok", n=31, segs=25)
    p.fuente("jooble", "run", query="backend", page=2)
    p.fuente("linkedin", "pend")
    p.fase("ia", hechas=120, total=556, lote=3, lotes=14)
    txt = p.render()
    assert "✅ <b>aira</b> 31 · 25s" in txt
    assert '🔄 <b>jooble</b> "backend" p2' in txt
    assert "⏳ <b>linkedin</b>" in txt
    assert "120/556" in txt and "lote 3/14" in txt


def test_render_escapa_html_de_la_query():
    p = Progreso()
    p.fuente("x", "run", query="<b>&")
    assert "<b>&" not in p.render().split("\n", 1)[1]


def test_estado_es_seguro_entre_hilos():
    p = Progreso()
    ts = [threading.Thread(target=lambda i=i: [p.fuente(f"f{i}", "run", query=str(k)) for k in range(200)])
          for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(p.fuentes) == 8


def test_limitador_deja_pasar_una_por_intervalo():
    t = [100.0]
    lim = Limitador(4.0, reloj=lambda: t[0])
    assert lim.listo()
    assert not lim.listo()
    t[0] += 4.1
    assert lim.listo()


def test_limitador_respeta_retry_after():
    t = [100.0]
    lim = Limitador(4.0, reloj=lambda: t[0])
    lim.esperar_hasta(30)
    t[0] += 20
    assert not lim.listo()
    t[0] += 11
    assert lim.listo()
