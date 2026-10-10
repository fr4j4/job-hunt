"""E2E de la web v2 con un navegador real (Playwright). Se omite si falta el front construido o el navegador.

Verifica de punta a punta: login → SPA → filtros → detalle → filtro cruzado desde un gráfico → estado/kanban,
y que NO haya violaciones de CSP, peticiones a terceros ni XSS con datos hostiles.
"""
import pathlib
import socket
import threading
import time

import pytest

from jobhunt import db as database
from jobhunt.config import load_config
from jobhunt.web import auth
from jobhunt.web.app import crear_app

DIST = pathlib.Path(__file__).resolve().parent.parent / "jobhunt" / "web" / "dist" / "index.html"
pw = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.skipif(not DIST.is_file(), reason="front sin construir (cd frontend && npm ci && npm run build)")

HOSTIL = "Dev <script>window.__xss=1</script><img src=x onerror=window.__xss=1> & Co"


@pytest.fixture(scope="module")
def servidor(tmp_path_factory):
    import uvicorn
    cfg = load_config()
    cfg.web.ui = "v2"
    cfg.data_dir = tmp_path_factory.mktemp("e2e")
    conn = database.connect(cfg)
    database.init_db(conn)
    filas = [(HOSTIL, "Evil <b>Corp</b>", "linkedin", {"url": "javascript:alert(1)", "rol_categoria": "Backend", "score": 90, "ai_encaje": "alto"})]
    fams = ["Backend", "Data", "QA", "DevOps/Cloud", "Frontend"]
    for i in range(40):
        palabra = "".join(chr(97 + (i * 7 + k * k * 3 + k * 11) % 26) for k in range(9))     # títulos distintos: el dedup no los fusiona
        filas.append((f"{palabra} {fams[i % 5]} {i * 97}", f"Empresa {palabra[:3]}{i}", ["laborum", "indeed", "linkedin"][i % 3],
                      {"rol_categoria": fams[i % 5], "techs": "Py;AWS" if i % 2 else "Java;SQL", "score": 40 + i,
                       "salary": f"CLP {1_000_000 + i * 80_000}" if i % 2 else "", "seniority_real": ["junior", "senior", "semi"][i % 3],
                       "modality": ["remoto", "híbrido", "presencial"][i % 3], "ai_encaje": ["alto", "medio", "bajo"][i % 3]}))
    for t, emp, fte, extra in filas:
        gid, _ = database.upsert(conn, {"title": t, "company": emp, "url": extra.pop("url", f"https://x.cl/{abs(hash(t))}"), "source": f"{fte}:q", "date": "2026-10-07"},
                                 "2026-10-07T00:00:00+00:00")
        conn.execute(f"UPDATE ofertas SET {', '.join(k + '=?' for k in extra)} WHERE group_id=?", (*extra.values(), gid))
    conn.commit()
    s = socket.socket(); s.bind(("127.0.0.1", 0)); puerto = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(crear_app(cfg), host="127.0.0.1", port=puerto, log_level="warning"))
    hilo = threading.Thread(target=srv.run, daemon=True); hilo.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield cfg, f"http://127.0.0.1:{puerto}"
    srv.should_exit = True
    hilo.join(timeout=5)
    conn.close()


@pytest.fixture
def pagina(servidor):
    cfg, base = servidor
    with pw.sync_playwright() as p:
        try:
            nav = p.chromium.launch()
        except Exception as e:                       # navegador no instalado
            pytest.skip(f"sin navegador: {e}")
        ctx = nav.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        pg.problemas, pg.externas = [], []
        pg.on("console", lambda m: pg.problemas.append(m.text) if m.type in ("error", "warning") else None)
        pg.on("pageerror", lambda e: pg.problemas.append(str(e)))
        pg.on("request", lambda r: pg.externas.append(r.url) if not r.url.startswith(base) and not r.url.startswith("data:") and not r.url.startswith("blob:") else None)
        conn = database.connect(cfg); auth.asegurar_tablas(conn); tok = auth.crear_token_login(conn); conn.commit(); conn.close()
        pg.goto(f"{base}/login?t={tok}"); pg.click('button:has-text("Entrar")'); pg.wait_for_url(f"{base}/v2")      # WEB_UI=v2 (por defecto): el login cae en la interfaz nueva
        pg.base = base
        yield pg
        nav.close()


def test_inicio_kpis_sin_errores_ni_terceros(pagina):
    pagina.goto(pagina.base + "/v2"); pagina.wait_for_selector("[data-kpi=activas]")
    assert pagina.inner_text("[data-kpi=activas] .v").replace(".", "") == "41"
    pagina.goto(pagina.base + "/v2/analisis/sueldos"); pagina.wait_for_selector("#V-11 svg")
    assert pagina.problemas == [] and pagina.externas == []


def test_datos_hostiles_se_pintan_como_texto(pagina):
    pagina.goto(pagina.base + "/v2/ofertas"); pagina.wait_for_selector(".vlist .vfila")
    assert "<script>" in pagina.inner_text(".vlist")                       # visible como texto, no ejecutado
    pagina.locator(".vlist .vfila").first.click(); pagina.wait_for_selector(".drawer h1")
    assert "<script>" in pagina.inner_text(".drawer h1")
    assert pagina.evaluate("window.__xss") is None
    assert pagina.locator(".drawer a:has-text('Ver y postular')").count() == 0     # url javascript: nunca es enlace
    assert pagina.problemas == []


def test_filtro_cruza_lista_y_graficos(pagina):
    pagina.goto(pagina.base + "/v2/ofertas"); pagina.wait_for_selector(".vlist .vfila")
    pagina.click('button:has-text("Familia")'); pagina.click('label:has-text("QA")')
    assert "fam=QA" in pagina.url
    n = int(pagina.inner_text(".resultado").split()[0].replace(".", ""))
    assert n == 8
    pagina.keyboard.press("Escape")
    pagina.click("nav.nav >> text=Análisis")                                 # los filtros viajan a Análisis
    pagina.wait_for_selector("[data-kpi=activas]")
    assert pagina.inner_text("[data-kpi=activas] .v") == "8" and "fam=QA" in pagina.url


def test_clic_en_grafico_filtra_y_deshacer_con_atras(pagina):
    pagina.goto(pagina.base + "/v2/analisis/tecnologias"); pagina.wait_for_selector("#V-20 svg"); pagina.wait_for_timeout(600)
    caja = pagina.locator("#V-20 svg").bounding_box()
    pagina.mouse.click(caja["x"] + 150, caja["y"] + 22); pagina.wait_for_timeout(400)
    assert "tec=" in pagina.url
    pagina.go_back(); pagina.wait_for_timeout(300)
    assert "tec=" not in pagina.url


def test_estado_y_kanban(pagina):
    pagina.goto(pagina.base + "/v2/ofertas"); pagina.wait_for_selector(".vlist .vfila")
    pagina.locator(".vlist .vfila").nth(2).click(); pagina.wait_for_selector(".drawer h1")
    pagina.click(".drawer button:has-text('Guardada')"); pagina.wait_for_timeout(700)
    pagina.goto(pagina.base + "/v2/ofertas?vista=kanban"); pagina.wait_for_selector(".kanban")
    assert pagina.locator(".kcol:has(h3:has-text('Guardada')) .kcard").count() == 1


def test_explorador_ejemplo_y_vista_guardada(pagina):
    pagina.goto(pagina.base + "/v2/explorador"); pagina.click("text=Transparencia salarial por fuente"); pagina.wait_for_selector(".grafico svg")
    assert "x=fuente" in pagina.url and "m=pct_con_sueldo" in pagina.url
    pagina.fill("input[placeholder='Nombre para guardar']", "mi vista"); pagina.click("text=Guardar vista"); pagina.wait_for_selector("text=mi vista")


def test_movil_sin_scroll_horizontal(pagina):
    pagina.set_viewport_size({"width": 390, "height": 844})
    for ruta in ("/v2", "/v2/ofertas?vista=tarjetas", "/v2/analisis/sueldos"):
        pagina.goto(pagina.base + ruta); pagina.wait_for_timeout(900)
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), ruta


def test_sin_sesion_la_spa_no_se_sirve(servidor):
    import requests
    _, base = servidor
    assert requests.get(base + "/v2").status_code == 401 and requests.get(base + "/api/snapshot").status_code == 401


def test_comparador_de_ofertas(pagina):
    pagina.goto(pagina.base + "/v2/ofertas"); pagina.wait_for_selector(".vlist .vfila")
    for k in (1, 2):
        pagina.locator(".vlist .vfila").nth(k).click(); pagina.wait_for_selector(".drawer h1")
        pagina.click(".drawer button:has-text('Agregar a comparación')"); pagina.keyboard.press("Escape"); pagina.wait_for_timeout(200)
    # la selección vive en memoria: se mantiene al navegar dentro de la SPA (no con una recarga completa)
    pagina.click("button:has-text('Comparar (2)')"); pagina.wait_for_selector("table[aria-label='Comparación de ofertas']")
    assert pagina.locator("table[aria-label='Comparación de ofertas'] thead th").count() == 3      # etiqueta + 2 ofertas


def test_comparar_segmentos_con_veredicto_honesto(pagina):
    pagina.goto(pagina.base + "/v2/explorador"); pagina.wait_for_selector("[data-testid=veredicto]")
    assert pagina.inner_text("[data-testid=veredicto]")        # nunca vacío: o hay diferencia, o dice por qué no se puede afirmar


def test_clasica_sigue_disponible(pagina):
    pagina.goto(pagina.base + "/?clasica=1")
    assert pagina.locator("table.responsiva").count() == 1
