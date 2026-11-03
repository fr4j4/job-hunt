// Modelo de filtros + máscaras. Cada filtro activo es una máscara Uint8Array; la global es su AND.
// Regla de filtro cruzado: al agregar por la dimensión D se excluye el filtro de D (ver `mascara`).
import { codigosDe, getDim, type Almacen } from './almacen';

export type Rango = [number | null, number | null];
export interface Filtros {
  q: string;
  qIds: number[] | null;                       // ids devueltos por /api/buscar (búsqueda en descripción)
  sel: Record<string, string[]>;               // dimensión → valores seleccionados ('' = sin dato)
  techsModo: 'o' | 'y';
  rangos: Partial<Record<'sueldo' | 'score' | 'antiguedad' | 'fecha', Rango>>;
  conSueldo: boolean;
}
export const filtrosVacios = (): Filtros => ({ q: '', qIds: null, sel: {}, techsModo: 'o', rangos: {}, conSueldo: false });

export const RANGOS = ['sueldo', 'score', 'antiguedad', 'fecha'] as const;

export function hayFiltros(f: Filtros): boolean {
  return !!(f.q || f.qIds || f.conSueldo || Object.values(f.sel).some((v) => v.length)
    || Object.values(f.rangos).some((r) => r && (r[0] !== null || r[1] !== null)));
}
export function contarFiltros(f: Filtros): number {
  return (f.q ? 1 : 0) + (f.conSueldo ? 1 : 0)
    + Object.values(f.sel).filter((v) => v.length).length
    + Object.values(f.rangos).filter((r) => r && (r[0] !== null || r[1] !== null)).length;
}

function maskSel(a: Almacen, dimId: string, valores: string[], modo: 'o' | 'y'): Uint8Array {
  const d = getDim(a, dimId);
  const quiero = new Set<number>();
  d.etiquetas.forEach((e, c) => { if (valores.includes(e)) quiero.add(c); });
  const m = new Uint8Array(a.n);
  for (let i = 0; i < a.n; i++) {
    const cs = codigosDe(d, i);
    if (modo === 'y' && d.multi) {
      let hits = 0;
      const vistos = new Set<number>();
      for (let k = 0; k < cs.length; k++) if (quiero.has(cs[k]) && !vistos.has(cs[k])) { vistos.add(cs[k]); hits++; }
      m[i] = hits === quiero.size && quiero.size > 0 ? 1 : 0;
    } else {
      for (let k = 0; k < cs.length; k++) if (quiero.has(cs[k])) { m[i] = 1; break; }
    }
  }
  return m;
}

function maskRango(a: Almacen, id: string, r: Rango): Uint8Array {
  const col = id === 'sueldo' ? a.sueldo : id === 'score' ? a.score : id === 'antiguedad' ? a.antiguedad : null;
  const m = new Uint8Array(a.n);
  const [lo, hi] = r;
  if (id === 'fecha') {
    for (let i = 0; i < a.n; i++) { const v = a.fecha[i]; m[i] = v >= 0 && (lo === null || v >= lo) && (hi === null || v <= hi) ? 1 : 0; }
    return m;
  }
  for (let i = 0; i < a.n; i++) {
    const v = col![i];
    m[i] = !Number.isNaN(v) && (lo === null || v >= lo) && (hi === null || v <= hi) ? 1 : 0;
  }
  return m;
}

const cache = new WeakMap<Almacen, Map<string, Uint8Array>>();
function memo(a: Almacen, clave: string, fn: () => Uint8Array): Uint8Array {
  let m = cache.get(a);
  if (!m) cache.set(a, (m = new Map()));
  let v = m.get(clave);
  if (!v) {
    if (m.size > 80) m.clear();
    m.set(clave, (v = fn()));
  }
  return v;
}

/** AND de todos los filtros activos salvo los de `excluir` (ids de dimensión o de rango). */
export function mascara(a: Almacen, f: Filtros, excluir: string[] = []): Uint8Array {
  const out = new Uint8Array(a.n).fill(1);
  const aplicar = (m: Uint8Array) => { for (let i = 0; i < out.length; i++) out[i] &= m[i]; };
  for (const [dimId, valores] of Object.entries(f.sel)) {
    if (!valores.length || excluir.includes(dimId)) continue;
    const modo = dimId === 'tech' ? f.techsModo : 'o';
    aplicar(memo(a, `s:${dimId}:${modo}:${[...valores].sort().join('\u0001')}`, () => maskSel(a, dimId, valores, modo)));
  }
  for (const id of RANGOS) {
    const r = f.rangos[id];
    if (!r || (r[0] === null && r[1] === null) || excluir.includes(id)) continue;
    aplicar(memo(a, `r:${id}:${r[0]}:${r[1]}`, () => maskRango(a, id, r)));
  }
  if (f.conSueldo && !excluir.includes('sueldo')) aplicar(memo(a, 'cs', () => Uint8Array.from(a.sueldoValido)));
  if (f.q.trim()) {
    const q = f.q.trim().toLowerCase();
    aplicar(memo(a, `q:${q}`, () => Uint8Array.from(a.texto, (t) => (t.includes(q) ? 1 : 0))));
  }
  if (f.qIds) {
    const ids = new Set(f.qIds);
    aplicar(memo(a, `qi:${f.qIds.length}:${f.qIds[0] ?? ''}`, () => Uint8Array.from(a.ids, (id) => (ids.has(id) ? 1 : 0))));
  }
  return out;
}

export const contar = (m: Uint8Array): number => { let n = 0; for (let i = 0; i < m.length; i++) n += m[i]; return n; };

// ---------- serialización a la URL (ida y vuelta) ----------
const CORTAS: Record<string, string> = { rol_familia: 'fam', rol: 'rol', seniority: 'sen', modalidad: 'mod', fuente: 'fte',
  encaje: 'enc', ingles: 'ing', empleo: 'emp', region: 'reg', comuna: 'com', empresa: 'cia', tech: 'tec',
  beneficio: 'ben', alerta: 'ale', a_favor: 'fav', estado: 'est' };
const LARGAS = Object.fromEntries(Object.entries(CORTAS).map(([k, v]) => [v, k]));
const enc = (v: string) => encodeURIComponent(v).replace(/%2C/g, '%252C');   // la coma separa valores
const dec = (v: string) => decodeURIComponent(v);

export function aQuery(f: Filtros): string {
  const p: string[] = [];
  if (f.q) p.push(`q=${encodeURIComponent(f.q)}`);
  for (const [dimId, vals] of Object.entries(f.sel)) {
    if (vals.length) p.push(`${CORTAS[dimId] ?? dimId}=${vals.map((v) => enc(v === '' ? '~' : v)).join(',')}`);
  }
  if (f.techsModo === 'y') p.push('tm=y');
  for (const id of RANGOS) {
    const r = f.rangos[id];
    if (r && (r[0] !== null || r[1] !== null)) p.push(`${id}=${r[0] ?? ''}~${r[1] ?? ''}`);
  }
  if (f.conSueldo) p.push('cs=1');
  return p.join('&');
}

export function desdeQuery(qs: string): Filtros {
  const f = filtrosVacios();
  const usp = new URLSearchParams(qs.startsWith('?') ? qs.slice(1) : qs);
  for (const [k, v] of usp) {
    if (k === 'q') f.q = v;
    else if (k === 'tm') f.techsModo = v === 'y' ? 'y' : 'o';
    else if (k === 'cs') f.conSueldo = v === '1';
    else if ((RANGOS as readonly string[]).includes(k)) {
      const [a, b] = v.split('~');
      f.rangos[k as keyof Filtros['rangos']] = [a === '' || a === undefined ? null : Number(a), b === '' || b === undefined ? null : Number(b)];
    } else if (LARGAS[k] || Object.values(LARGAS).includes(k)) {
      const dimId = LARGAS[k] ?? k;
      f.sel[dimId] = v.split(',').filter((x) => x !== '').map((x) => { const d = dec(x); return d === '~' ? '' : d; });
    }
  }
  return f;
}
