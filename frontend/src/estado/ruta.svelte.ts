// Router mínimo sobre History API. La URL es la única fuente de verdad del estado (filtros, vista, pestaña).
import { aQuery, desdeQuery, filtrosVacios, RANGOS, type Filtros } from '../motor/filtros';

export const BASE = '/v2';
const CLAVES_FILTRO = new Set(['q', 'tm', 'cs', 'fam', 'rol', 'sen', 'mod', 'fte', 'enc', 'ing', 'emp', 'reg', 'com', 'cia', 'tec',
  'ben', 'ale', 'fav', 'est', ...RANGOS]);

export const ruta = $state({ path: '/', search: '' });

function leer() {
  const p = location.pathname.startsWith(BASE) ? location.pathname.slice(BASE.length) : location.pathname;
  ruta.path = p === '' ? '/' : p;
  ruta.search = location.search;
}
if (typeof window !== 'undefined') {
  leer();
  window.addEventListener('popstate', leer);
}

export function ir(path: string, search = '', reemplazar = false): void {
  const url = `${BASE}${path === '/' ? '' : path}${search ? (search.startsWith('?') ? search : '?' + search) : ''}`;
  if (reemplazar) history.replaceState(null, '', url);
  else history.pushState(null, '', url);
  leer();
}

export function params(): URLSearchParams { return new URLSearchParams(ruta.search); }

/** Cambia solo los parámetros que no son filtros (vista, orden, pestaña…). `null` borra la clave. */
export function setParams(cambios: Record<string, string | null>, reemplazar = true): void {
  const p = params();
  for (const [k, v] of Object.entries(cambios)) { if (v === null || v === '') p.delete(k); else p.set(k, v); }
  ir(ruta.path, p.toString(), reemplazar);
}

export function setFiltros(f: Filtros, reemplazar = false, path = ruta.path): void {
  const p = params();
  for (const k of [...p.keys()]) if (CLAVES_FILTRO.has(k)) p.delete(k);
  const q = [p.toString(), aQuery(f)].filter(Boolean).join('&');
  ir(path, q, reemplazar);
}

export function filtrosActuales(): Filtros {
  const usp = new URLSearchParams();
  for (const [k, v] of params()) if (CLAVES_FILTRO.has(k)) usp.append(k, v);
  return usp.toString() ? desdeQuery(usp.toString()) : filtrosVacios();
}

/** Mantiene los filtros al cambiar de página (Ofertas ⇄ Análisis ⇄ Explorador). */
export function queryDeFiltros(): string { return aQuery(filtrosActuales()); }

export function seccion(): string { return ruta.path.split('/')[1] ?? ''; }
export function subruta(): string { return decodeURIComponent(ruta.path.split('/').slice(2).join('/')); }
