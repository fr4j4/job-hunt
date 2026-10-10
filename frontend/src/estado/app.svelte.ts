import { api, SinSesion } from '../lib/api';
import type { Perfil, Semantica } from '../lib/tipos';
import { construirAlmacen, type Almacen } from '../motor/almacen';

export const app = $state({
  cargando: true, error: '', sinSesion: false,
  almacen: null as Almacen | null, perfil: null as Perfil | null, semantica: null as Semantica | null,
  version: 0,
});

export async function cargar(silencioso = false): Promise<void> {
  if (!silencioso) app.cargando = true;
  app.error = '';
  try {
    const [snap, perfil, sem] = await Promise.all([api.snapshot(), api.perfil(), api.semantica()]);
    app.almacen = construirAlmacen(snap);
    app.perfil = perfil; app.semantica = sem;
    app.version++;
  } catch (e) {
    if (e instanceof SinSesion) app.sinSesion = true;
    else app.error = e instanceof Error ? e.message : String(e);
  } finally {
    app.cargando = false;
  }
}

// ---- tema ----
export type Tema = 'auto' | 'light' | 'dark';
export const ui = $state({ tema: 'auto' as Tema, oscuro: false, toast: '' });

const mq = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
function calcular() { ui.oscuro = ui.tema === 'dark' || (ui.tema === 'auto' && !!mq?.matches); }
export function iniciarTema(): void {
  try { const t = localStorage.getItem('jh_tema'); if (t === 'light' || t === 'dark') ui.tema = t; } catch { /* sin storage */ }
  aplicarTema();
  mq?.addEventListener('change', calcular);
}
function aplicarTema() {
  if (ui.tema === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', ui.tema);
  calcular();
}
export function ciclarTema(): void {
  ui.tema = ui.tema === 'auto' ? 'light' : ui.tema === 'light' ? 'dark' : 'auto';
  try { if (ui.tema === 'auto') localStorage.removeItem('jh_tema'); else localStorage.setItem('jh_tema', ui.tema); } catch { /* sin storage */ }
  aplicarTema();
}
let tTimer: ReturnType<typeof setTimeout>;
export function avisar(msg: string): void { ui.toast = msg; clearTimeout(tTimer); tTimer = setTimeout(() => (ui.toast = ''), 2600); }
