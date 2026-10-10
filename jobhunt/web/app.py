"""App web (FastAPI) — vista de escritorio del pool de ofertas.

Seguridad (ver auth.py):
- Sin sesión válida NINGUNA página muestra datos (401 genérico).
- Acceso: /web en el bot → enlace /login?t=<token> de un solo uso. El GET solo muestra
  un botón "Entrar"; el token se canjea con POST. Así las vistas previas de enlaces de
  Telegram y los escáneres de URLs no pueden gastar ni usar el enlace.
- Cookie de sesión HttpOnly + SameSite=Lax (+ Secure con HTTPS); POST solo same-origin.
- CSP estricta sin JavaScript, no-referrer (el token nunca sale en un Referer), no-store.
- Escucha en 127.0.0.1 por defecto.
"""
from __future__ import annotations

import ipaddress
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Query, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db as database
from ..config import Config
from ..logging_setup import get_logger
from . import auth

log = get_logger(__name__)

COOKIE = "jh_sesion"
_DIR = Path(__file__).parent
_POR_PAGINA = 50
_ORDENES = {"score": "score", "market": "market_score", "fecha": "date_canonical",
            "cargo": "title", "empresa": "company", "actualizada": "updated_at"}
_MODALIDADES = {"remoto": "remot", "hibrido": "brid", "presencial": "presencial"}
_HEADERS = {
    "Content-Security-Policy": ("default-src 'none'; style-src 'self'; img-src 'self'; "
                                "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# La SPA (web v2) necesita JS propio, nada de terceros ni inline. connect-src 'self' = solo /api.
_CSP_SPA = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; font-src 'self'; worker-src 'self' blob:; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")


def _cookie_segura(cfg: Config) -> bool:
    if cfg.web.cookie_secure is not None:
        return cfg.web.cookie_secure
    return cfg.web.public_url.startswith("https://")


def _misma_origen(request: Request) -> bool:
    """POST aceptado solo si viene de esta misma web (anti-CSRF).

    Defensa en profundidad: SameSite=Lax ya impide que un POST cross-site mande la
    cookie. Sobre HTTP en LAN los navegadores NO mandan Sec-Fetch-* (solo en
    orígenes "trustworthy" = HTTPS/localhost); se cae a Origin/Referer y, si no
    hay ninguno, se acepta: un POST cross-site de navegador SIEMPRE trae Origin,
    así que su ausencia es un cliente no-navegador o same-origin.
    """
    sfs = request.headers.get("sec-fetch-site")
    host = request.headers.get("host", "")
    if sfs in ("same-origin", "none"):
        return True
    for cab in ("origin", "referer"):
        valor = request.headers.get(cab)
        if valor and valor != "null":   # 'null' (origen opaco) no aporta host que comparar
            return valor.split("://", 1)[-1].split("/", 1)[0] == host
    # Sin cabeceras de origen: no es un POST cross-site de navegador (esos SIEMPRE
    # traen Origin). Se rechaza solo si el navegador declaró explícitamente cross-site.
    return sfs != "cross-site"


class _Limitador:
    """Máx. N intentos de login por IP en una ventana (frena la fuerza bruta y el abuso)."""

    def __init__(self, maximo: int = 20, ventana_s: int = 600):
        self.maximo, self.ventana = maximo, ventana_s
        self.intentos: dict[str, list[float]] = defaultdict(list)

    def permitir(self, ip: str) -> bool:
        ahora = time.time()
        lista = [t for t in self.intentos[ip] if ahora - t < self.ventana]
        self.intentos[ip] = lista
        if len(lista) >= self.maximo:
            return False
        lista.append(ahora)
        return True


def _sueldo(row: dict) -> str:
    from ..scoring import _salary_to_clp_monthly
    v = _salary_to_clp_monthly(row.get("salary") or "", row.get("description") or "")
    if v:
        return f"${v:,}".replace(",", ".")
    return (row.get("salary") or "").strip()


def _edad(row: dict) -> str:
    from ..channel import _edad_humana
    from ..domain.fechas import age_days
    if not (row.get("date_posted") or row.get("first_seen")):
        return ""
    return _edad_humana(age_days(row))


def _lista_json(raw) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


def _idiomas(raw) -> list[dict]:
    try:
        v = json.loads(raw or "[]")
        return [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


_ETIQUETAS = {
    "base": "Base", "exp": "Experiencia pedida", "techs": "Tecnologías en el título",
    "role_profile": "Rol de tu perfil en el título", "english": "Inglés", "us_hours": "Horario de EE.UU.",
    "salary_out": "Sueldo fuera de tu rango", "stack_overlap": "Stack que coincide", "staffing": "Consultora / staffing",
    "rejected_by": "Descartada por", "salario_pts": "Sueldo (puntos)", "salario": "Sueldo",
    "mod_pts": "Modalidad (puntos)", "modalidad": "Modalidad", "trans_pts": "Transparencia (puntos)",
    "empresa": "Empresa", "loc_pts": "Ubicación (puntos)", "ubicacion": "Ubicación", "fresh_pts": "Frescura", "edad_dias": "Días publicada", "ben_pts": "Beneficios",
}


def _etiqueta(clave: str) -> str:
    """Clave interna del desglose → texto legible ('mod:remoto' → 'Modalidad remoto')."""
    k = str(clave)
    for pref, txt in (("role:", "Rol en el título: "), ("mod:", "Modalidad "), ("green:", "Señal positiva: "),
                      ("salary:", "Sueldo en tu rango: $")):
        if k.startswith(pref):
            return txt + k[len(pref):]
    return _ETIQUETAS.get(k, k.replace("_", " "))


def _fecha(v) -> str:
    """ISO → '2026-10-06 21:32' (UTC)."""
    t = str(v or "")
    return t[:16].replace("T", " ") if len(t) >= 16 else t


def _url_segura(u: str) -> str:
    """Solo enlaces http(s): nada de javascript:/data: en un href."""
    u = (u or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def crear_app(cfg: Config) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    tpl = Jinja2Templates(directory=str(_DIR / "templates"))
    tpl.env.filters["sueldo"] = _sueldo
    tpl.env.filters["edad"] = _edad
    tpl.env.filters["url_segura"] = _url_segura
    tpl.env.filters["etiqueta"] = _etiqueta
    tpl.env.filters["fecha"] = _fecha
    tpl.env.globals["qs"] = lambda f, **cambios: urlencode(
        {k: v for k, v in {**f, **cambios}.items() if v not in ("", None)})
    app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")
    limitador = _Limitador()

    @app.middleware("http")
    async def _cabeceras(request: Request, call_next):
        resp = await call_next(request)
        for k, v in _HEADERS.items():
            resp.headers.setdefault(k, v)
        if not request.url.path.startswith(("/static/", "/assets/")):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def _conn():
        conn = database.connect(cfg)
        auth.asegurar_tablas(conn)
        return conn

    def _autenticado(request: Request) -> bool:
        conn = _conn()
        try:
            return auth.sesion_valida(conn, request.cookies.get(COOKIE))
        finally:
            conn.close()

    def _sin_acceso(request: Request, motivo: str = "", status: int = 401):
        return tpl.TemplateResponse(request, "acceso.html", {"motivo": motivo}, status_code=status)

    # ---------------- login en dos pasos ----------------

    @app.get("/login", response_class=HTMLResponse)
    def login_confirmar(request: Request, t: str = ""):
        # GET no canjea nada: solo pide confirmar (las vistas previas no gastan el enlace)
        return tpl.TemplateResponse(request, "login.html", {"token": t[:200]})

    @app.post("/login")
    def login(request: Request, t: str = Form("")):
        ip = request.client.host if request.client else "?"
        if not limitador.permitir(ip):
            return _sin_acceso(request, "Demasiados intentos. Espera unos minutos.", 429)
        if not _misma_origen(request):
            log.warning("web: login rechazado por origen · sfs=%r origin=%r host=%r",
                        request.headers.get("sec-fetch-site"), request.headers.get("origin"),
                        request.headers.get("host"))
            return _sin_acceso(request, "Solicitud no válida.", 403)
        conn = _conn()
        try:
            sesion = auth.canjear_token(conn, t, cfg.web.session_days)
        finally:
            conn.close()
        if not sesion:
            log.warning("web: enlace inválido, vencido o ya usado (ip %s)", ip)
            return _sin_acceso(request, "El enlace no es válido, venció o ya se usó. "
                                        "Pide uno nuevo con /web en el bot.", 403)
        log.info("web: sesión iniciada (ip %s)", ip)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, sesion, max_age=cfg.web.session_days * 86400, httponly=True,
                        samesite="lax", secure=_cookie_segura(cfg), path="/")
        return resp

    @app.post("/salir")
    def salir(request: Request):
        if not _misma_origen(request):
            return _sin_acceso(request, "Solicitud no válida.", 403)
        conn = _conn()
        try:
            auth.cerrar_sesion(conn, request.cookies.get(COOKIE))
        finally:
            conn.close()
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    # ---------------- páginas (todas exigen sesión) ----------------

    @app.get("/", response_class=HTMLResponse)
    def ofertas(request: Request, q: str = "", minimo: str = Query("", alias="min"), mod: str = "",
                sueldo: str = "", encaje: str = "", fuente: str = "", inactivas: str = "",
                orden: str = "score", sentido_q: str = Query("desc", alias="dir"), page: int = 1):
        if not _autenticado(request):
            return _sin_acceso(request)
        if cfg.web.ui == "v2" and not request.query_params.get("clasica"):
            return RedirectResponse("/v2", status_code=303)
        where, params = ["1=1"], []
        if not inactivas:
            where.append("active = 1")
        if q.strip():
            like = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(lower(title) LIKE ? ESCAPE '\\' OR lower(company) LIKE ? ESCAPE '\\' "
                         "OR lower(description) LIKE ? ESCAPE '\\')")
            params += [like, like, like]
        if minimo.strip().isdigit():
            where.append("score >= ?")
            params.append(int(minimo))
        if mod in _MODALIDADES:
            where.append("lower(modality) LIKE ?")
            params.append(f"%{_MODALIDADES[mod]}%")
        if sueldo:
            where.append("salary != ''")
        if encaje in ("alto", "medio", "bajo", "ninguno"):
            where.append("ai_encaje = ?")
            params.append(encaje)
        elif encaje == "sin":
            where.append("ai_encaje = ''")
        if fuente.strip():
            where.append("source LIKE ?")
            params.append(fuente.strip().lower()[:20] + ":%")
        col = _ORDENES.get(orden, "score")
        sentido = "ASC" if sentido_q == "asc" else "DESC"
        conn = _conn()
        try:
            total = conn.execute(f"SELECT COUNT(*) FROM ofertas WHERE {' AND '.join(where)}", params).fetchone()[0]
            paginas = max(1, math.ceil(total / _POR_PAGINA))
            page = max(1, min(page, paginas))
            filas = [dict(r) for r in conn.execute(
                f"SELECT * FROM ofertas WHERE {' AND '.join(where)} "
                f"ORDER BY {col} {sentido}, first_seen DESC LIMIT ? OFFSET ?",
                [*params, _POR_PAGINA, (page - 1) * _POR_PAGINA]).fetchall()]
            fuentes = sorted({(r[0] or "").split(":")[0] for r in conn.execute(
                "SELECT DISTINCT source FROM ofertas").fetchall() if r[0]})
        finally:
            conn.close()
        filtros = {"q": q, "min": minimo, "mod": mod, "sueldo": sueldo, "encaje": encaje,
                   "fuente": fuente, "inactivas": inactivas, "orden": orden,
                   "dir": "asc" if sentido == "ASC" else "desc"}
        return tpl.TemplateResponse(request, "ofertas.html", {
            "filas": filas, "total": total, "page": page, "paginas": paginas,
            "f": filtros, "fuentes": fuentes, "ordenes": _ORDENES})

    @app.get("/oferta/{gid:path}", response_class=HTMLResponse)
    def oferta(request: Request, gid: str):
        if not _autenticado(request):
            return _sin_acceso(request)
        conn = _conn()
        try:
            ref = gid.strip()
            fila = (conn.execute("SELECT * FROM ofertas WHERE id = ?", (int(ref),)).fetchone() if ref.isdigit()
                    else conn.execute("SELECT * FROM ofertas WHERE group_id = ?", (ref[:300],)).fetchone())
        finally:
            conn.close()
        if not fila:
            return tpl.TemplateResponse(request, "acceso.html",
                                        {"motivo": "Esa oferta no existe.", "logueado": True},
                                        status_code=404)
        o = dict(fila)
        from ..domain.roles import fit_ok, is_dev
        from ..scoring import compute_market_score, compute_score
        score, desglose = compute_score(o, cfg)
        mscore, mdesglose = compute_market_score(o)
        return tpl.TemplateResponse(request, "oferta.html", {
            "o": o, "score": score, "desglose": desglose, "mscore": mscore,
            "mdesglose": mdesglose, "pasa_fit": fit_ok(o, cfg),
            "es_dev": is_dev(o.get("rol_categoria"), o.get("title") or "", cfg, o.get("description") or ""),
            "rojas": _lista_json(o.get("ai_red_flags")), "verdes": _lista_json(o.get("ai_green_flags")),
            "beneficios": _lista_json(o.get("ai_benefits")), "idiomas": _idiomas(o.get("ai_idiomas")),
            "min_fit": cfg.channel.min_fit_score})

    @app.get("/fuentes", response_class=HTMLResponse)
    def fuentes(request: Request):
        if not _autenticado(request):
            return _sin_acceso(request)
        from .. import salud
        conn = _conn()
        try:
            hist = salud._historial(conn, 10)
        finally:
            conn.close()
        nombres = sorted({f for h in hist for f in h["fuentes"]})
        filas = []
        for f in nombres:
            racha = salud.racha_ceros(hist, f)
            estado = ("caida" if racha >= cfg.alerts.source_sweeps else "vigilar" if racha else "ok")
            filas.append({"nombre": salud._nombre(f), "estado": estado, "racha": racha,
                          "serie": [h["fuentes"].get(f) for h in hist]})
        return tpl.TemplateResponse(request, "fuentes.html", {
            "filas": filas, "barridos": [h["ts"] for h in hist]})

    # ---------------- web v2: API JSON + SPA ----------------

    from .api import crear_router
    app.include_router(crear_router(cfg, _conn, _autenticado, _misma_origen))
    dist = _DIR / "dist"

    def _spa(request: Request):
        if not _autenticado(request):
            return _sin_acceso(request)
        index = dist / "index.html"
        if not index.is_file():
            return PlainTextResponse("La interfaz v2 no está construida: cd frontend && npm ci && npm run build",
                                     status_code=503)
        return FileResponse(index, media_type="text/html", headers={"Content-Security-Policy": _CSP_SPA})

    @app.get("/v2", response_class=HTMLResponse)
    def spa_raiz(request: Request):
        return _spa(request)

    @app.get("/v2/{ruta:path}", response_class=HTMLResponse)
    def spa_ruta(request: Request, ruta: str):
        return _spa(request)

    @app.get("/assets/{ruta:path}")
    def spa_assets(ruta: str):
        # público a propósito (solo código con hash en el nombre, sin datos); resolución segura
        base = (dist / "assets").resolve()
        f = (base / ruta).resolve()
        if base not in f.parents or not f.is_file():
            return PlainTextResponse("no encontrado", status_code=404)
        return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable",
                                        "Content-Security-Policy": _CSP_SPA})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code,
                                headers={"Cache-Control": "no-store"})
        if exc.status_code == 404:
            return _sin_acceso(request, "Página no encontrada.", 404)
        return await http_exception_handler(request, exc)

    return app


def url_base(cfg: Config) -> str:
    if cfg.web.public_url:
        return cfg.web.public_url
    host = cfg.web.host
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    return f"http://{host}:{cfg.web.port}"


def _es_local(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def servir(cfg: Config) -> None:
    """Levanta la web (bloqueante). El daemon la corre en un hilo si WEB_ENABLED=true."""
    import uvicorn
    if not _es_local(cfg.web.host) and not cfg.web.public_url.startswith("https://"):
        log.warning("web: escuchando en %s SIN HTTPS — la cookie viaja en claro por la red. "
                    "Usa Tailscale/VPN o un proxy HTTPS y define WEB_PUBLIC_URL=https://…", cfg.web.host)
    log.info("web: %s (escucha %s:%s)", url_base(cfg), cfg.web.host, cfg.web.port)
    uvicorn.run(crear_app(cfg), host=cfg.web.host, port=cfg.web.port, log_level="warning",
                server_header=False, proxy_headers=False)
