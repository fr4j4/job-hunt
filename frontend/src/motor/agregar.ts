// Agregación según el modelo semántico (spec §2.3): cada métrica con su denominador y su n mínimo.
import { codigosDe, getDim, type Almacen } from './almacen';
import { bootstrapMediana, percentil } from './estadistica';

export type EstadoMuestra = 'ok' | 'chica' | 'insuficiente' | 'vacio';
export interface Grupo {
  clave: string;               // etiqueta ('' = sin dato)
  codigo: number;
  valor: number | null;        // null si la muestra es insuficiente
  n: number;                   // ofertas del grupo
  nBase: number;               // denominador efectivo de la métrica (p. ej. con sueldo válido)
  estado: EstadoMuestra;
  ic?: [number, number] | null;
  filas: number[];             // índices de las ofertas del grupo (para "ver ofertas" y puntos individuales)
}
export interface Cobertura { total: number; usadas: number; excluidas: number; nota: string; omitidos?: number }
export interface Resultado { grupos: Grupo[]; cobertura: Cobertura }

export interface MinimosMetrica { minimo: number; ok: number }
export const MINIMOS: Record<string, MinimosMetrica> = {
  ofertas: { minimo: 1, ok: 1 }, pct_con_sueldo: { minimo: 10, ok: 20 }, sueldo_p25: { minimo: 5, ok: 10 },
  sueldo_p50: { minimo: 5, ok: 10 }, sueldo_p75: { minimo: 5, ok: 10 }, score_medio: { minimo: 5, ok: 10 },
  pct_encaje_alto: { minimo: 10, ok: 20 }, antiguedad_mediana: { minimo: 5, ok: 10 }, pct_multifuente: { minimo: 10, ok: 20 },
};

export function estadoDe(metrica: string, n: number): EstadoMuestra {
  const m = MINIMOS[metrica] ?? { minimo: 1, ok: 1 };
  if (n === 0) return 'vacio';
  if (n < m.minimo) return 'insuficiente';
  return n < m.ok ? 'chica' : 'ok';
}

/** Agrupa filas (de la máscara) por dimensión. Multivalor: una fila cuenta una vez por valor. */
export function agrupar(a: Almacen, dimId: string, mask: Uint8Array, opts: { sinDato?: boolean } = {}): Map<number, number[]> {
  const d = getDim(a, dimId);
  const sinDato = opts.sinDato ?? true;
  const vacio = d.etiquetas.indexOf('');
  const por = new Map<number, number[]>();
  for (let i = 0; i < a.n; i++) {
    if (!mask[i]) continue;
    const cs = codigosDe(d, i);
    for (let k = 0; k < cs.length; k++) {
      const c = cs[k];
      if (!sinDato && c === vacio && !d.multi) continue;
      let l = por.get(c);
      if (!l) por.set(c, (l = []));
      l.push(i);
    }
  }
  return por;
}

const valoresValidos = (col: Float64Array, filas: number[]): number[] => {
  const v: number[] = [];
  for (const i of filas) if (!Number.isNaN(col[i])) v.push(col[i]);
  return v;
};

/** Evalúa una métrica sobre un conjunto de filas. */
export function evaluar(a: Almacen, metrica: string, filas: number[], opts: { bootstrap?: boolean } = {}): Pick<Grupo, 'valor' | 'nBase' | 'estado' | 'ic'> {
  let valor: number | null = null, nBase = filas.length, ic: Grupo['ic'] = undefined;
  switch (metrica) {
    case 'ofertas': case 'activas': valor = filas.length; break;
    case 'pct_con_sueldo': {
      let k = 0; for (const i of filas) k += a.sueldoValido[i];
      valor = filas.length ? k / filas.length : null; break;
    }
    case 'sueldo_p25': case 'sueldo_p50': case 'sueldo_p75': {
      const v = valoresValidos(a.sueldo, filas);
      nBase = v.length;
      const p = metrica === 'sueldo_p25' ? 25 : metrica === 'sueldo_p50' ? 50 : 75;
      valor = estadoDe(metrica, v.length) === 'insuficiente' ? null : percentil(v, p);
      if (metrica === 'sueldo_p50' && opts.bootstrap && v.length >= 5) ic = bootstrapMediana(v);
      break;
    }
    case 'score_medio': {
      let s = 0; for (const i of filas) s += a.score[i];
      valor = filas.length ? s / filas.length : null; break;
    }
    case 'pct_encaje_alto': {
      const enc = getDim(a, 'encaje'); let ev = 0, alto = 0;
      for (const i of filas) { const e = enc.etiquetas[enc.cod![i]]; if (e) { ev++; if (e === 'alto') alto++; } }
      nBase = ev; valor = ev ? alto / ev : null; break;
    }
    case 'antiguedad_mediana': valor = percentil(valoresValidos(a.antiguedad, filas), 50); break;
    case 'pct_multifuente': {
      let k = 0; for (const i of filas) if (a.nFuentes[i] >= 2) k++;
      valor = filas.length ? k / filas.length : null; break;
    }
    default: throw new Error(`métrica desconocida: ${metrica}`);
  }
  const estado = estadoDe(metrica, nBase);
  if (estado === 'insuficiente' || estado === 'vacio') valor = metrica === 'ofertas' ? valor : null;
  return { valor, nBase, estado, ic };
}

export interface OpcionesAgregar { dim: string; metrica: string; mask: Uint8Array; sinDato?: boolean; top?: number;
                                   orden?: 'valor' | 'etiqueta' | 'natural'; bootstrap?: boolean; ordenNatural?: string[]; otras?: boolean }

export function agregar(a: Almacen, o: OpcionesAgregar): Resultado {
  const d = getDim(a, o.dim);
  const por = agrupar(a, o.dim, o.mask, { sinDato: o.sinDato });
  let total = 0; for (let i = 0; i < a.n; i++) total += o.mask[i];
  let grupos: Grupo[] = [...por.entries()].map(([c, filas]) => ({
    clave: d.etiquetas[c], codigo: c, n: filas.length, filas, ...evaluar(a, o.metrica, filas, { bootstrap: o.bootstrap }),
  }));
  let omitidos = 0;
  const conDato = grupos.filter((g) => g.clave !== '');
  const sin = grupos.find((g) => g.clave === '');
  const ord = o.orden ?? 'valor';
  conDato.sort(ord === 'etiqueta' ? (x, y) => x.clave.localeCompare(y.clave, 'es')
    : ord === 'natural' && o.ordenNatural ? (x, y) => o.ordenNatural!.indexOf(x.clave) - o.ordenNatural!.indexOf(y.clave)
    : (x, y) => (y.valor ?? -1) - (x.valor ?? -1) || y.n - x.n);
  grupos = conDato;
  if (o.top && grupos.length > o.top) {
    const resto = grupos.slice(o.top);
    grupos = grupos.slice(0, o.top);
    if (o.otras === false) { omitidos = resto.length; } else {
    const filas = [...new Set(resto.flatMap((g) => g.filas))];
    grupos.push({ clave: `Otras (${resto.length})`, codigo: -2, n: filas.length, filas, ...evaluar(a, o.metrica, filas) });
    }
  }
  if (sin && o.sinDato !== false) grupos.push(sin);
  const usadas = new Set(grupos.filter((g) => g.clave !== '').flatMap((g) => g.filas)).size;
  return { grupos, cobertura: { total, usadas, excluidas: total - usadas,
    nota: total - usadas > 0 ? `${total - usadas} sin dato de ${d.id}` : '', omitidos } };
}

/** Matriz dimX × dimY (heatmap / barras apiladas): celda = métrica sobre las filas de (x,y). */
export interface Celda { x: string; y: string; valor: number | null; n: number; nBase: number; estado: EstadoMuestra; filas: number[] }
export function matriz(a: Almacen, dimX: string, dimY: string, metrica: string, mask: Uint8Array,
                       opts: { sinDato?: boolean } = {}): { celdas: Celda[]; xs: string[]; ys: string[]; cobertura: Cobertura } {
  const dx = getDim(a, dimX), dy = getDim(a, dimY);
  const mapa = new Map<string, number[]>();
  const vx = dx.etiquetas.indexOf(''), vy = dy.etiquetas.indexOf('');
  let total = 0, usadas = 0;
  for (let i = 0; i < a.n; i++) {
    if (!mask[i]) continue;
    total++;
    let usada = false;
    const cx = codigosDe(dx, i), cy = codigosDe(dy, i);
    for (let p = 0; p < cx.length; p++) for (let q = 0; q < cy.length; q++) {
      if (opts.sinDato === false && ((!dx.multi && cx[p] === vx) || (!dy.multi && cy[q] === vy))) continue;
      const k = `${cx[p]}|${cy[q]}`;
      let l = mapa.get(k); if (!l) mapa.set(k, (l = []));
      l.push(i); usada = true;
    }
    if (usada) usadas++;
  }
  const celdas: Celda[] = [];
  const xs = new Set<string>(), ys = new Set<string>();
  for (const [k, filas] of mapa) {
    const [cx, cy] = k.split('|').map(Number);
    const ev = evaluar(a, metrica, filas);
    celdas.push({ x: dx.etiquetas[cx], y: dy.etiquetas[cy], filas, n: filas.length, ...ev });
    xs.add(dx.etiquetas[cx]); ys.add(dy.etiquetas[cy]);
  }
  return { celdas, xs: [...xs], ys: [...ys], cobertura: { total, usadas, excluidas: total - usadas, nota: '' } };
}

/** Demanda de tecnologías: denominador = ofertas con techs CONOCIDAS (no el total). */
export function demandaTech(a: Almacen, mask: Uint8Array, top = 25): { items: { tech: string; n: number; pct: number; filas: number[] }[];
                                                                       base: number; total: number; suficiente: boolean } {
  const d = getDim(a, 'tech');
  let base = 0, total = 0;
  const por = new Map<number, number[]>();
  for (let i = 0; i < a.n; i++) {
    if (!mask[i]) continue;
    total++;
    if (!d.conocida![i]) continue;
    base++;
    for (let k = d.off![i]; k < d.off![i + 1]; k++) {
      const c = d.val![k]; let l = por.get(c); if (!l) por.set(c, (l = [])); l.push(i);
    }
  }
  const items = [...por.entries()].map(([c, filas]) => ({ tech: d.etiquetas[c], n: filas.length, pct: base ? filas.length / base : 0, filas }))
    .sort((x, y) => y.n - x.n).slice(0, top);
  return { items, base, total, suficiente: base >= 20 };
}
