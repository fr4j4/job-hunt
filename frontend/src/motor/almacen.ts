// Almacén columnar en memoria. Las dimensiones categóricas se codifican a enteros (Uint16Array) y las
// multivalor (techs, tags) en CSR; los números en Float64Array con NaN = dato ausente.
import type { Snapshot } from '../lib/tipos';

export interface DimData {
  id: string;
  etiquetas: string[];                 // código → etiqueta ('' = sin dato)
  multi: boolean;
  cod?: Uint16Array;                   // univalor
  off?: Uint32Array; val?: Uint16Array; // multivalor (CSR)
  conocida?: Uint8Array;               // multivalor: 0 = la fuente no informó la lista (p. ej. techs=null)
}

export interface Almacen {
  n: number;
  snap: Snapshot;
  ids: number[];
  dim: Record<string, DimData>;
  sueldo: Float64Array; sueldoValido: Uint8Array;
  score: Float64Array; market: Float64Array; antiguedad: Float64Array; applicants: Float64Array;
  exp: Float64Array; nFuentes: Float64Array;
  fecha: Int32Array;                   // días desde epoch de first_seen (-1 = sin dato)
  texto: string[];                     // título+empresa+resumen en minúsculas (búsqueda rápida)
  indice: Map<number, number>;
  nombreEmpresa: Map<string, string>;  // clave canónica → nombre original más frecuente
}

export const DIMS_UNIVALOR = ['rol_familia', 'rol', 'seniority', 'modalidad', 'modalidad_src', 'fuente', 'encaje',
  'ingles', 'empleo', 'region', 'comuna', 'empresa', 'estado'] as const;
export const DIMS_MULTI = ['tech', 'beneficio', 'alerta', 'a_favor', 'fuentes'] as const;
export const DIMS_TEMPORALES = ['dia', 'semana', 'mes'] as const;

function codificar(valores: string[]): DimData['etiquetas'] & { cods: Uint16Array } {
  const idx = new Map<string, number>();
  const et: string[] = [];
  const cods = new Uint16Array(valores.length);
  valores.forEach((v, i) => {
    let c = idx.get(v);
    if (c === undefined) { c = et.length; idx.set(v, c); et.push(v); }
    cods[i] = c;
  });
  return Object.assign(et, { cods }) as DimData['etiquetas'] & { cods: Uint16Array };
}

function csr(listas: (number[] | null | undefined)[], etiquetas: string[]): Pick<DimData, 'off' | 'val' | 'conocida'> & { etiquetas: string[] } {
  let total = 0;
  for (const l of listas) total += l ? l.length : 0;
  const off = new Uint32Array(listas.length + 1);
  const val = new Uint16Array(total);
  const conocida = new Uint8Array(listas.length);
  let p = 0;
  listas.forEach((l, i) => {
    off[i] = p;
    if (l) { conocida[i] = 1; for (const x of l) val[p++] = x; }
  });
  off[listas.length] = p;
  return { off, val, conocida, etiquetas };
}

const diaEpoch = (iso: string): number => {
  if (!iso || iso.length < 10) return -1;
  const t = Date.parse(iso.slice(0, 10) + 'T00:00:00Z');
  return Number.isNaN(t) ? -1 : Math.floor(t / 86400000);
};

export function construirAlmacen(s: Snapshot): Almacen {
  const c = s.cols, n = s.n;
  const dim: Record<string, DimData> = {};
  const uni = (id: string, valores: string[]) => {
    const e = codificar(valores);
    dim[id] = { id, etiquetas: Array.from(e), multi: false, cod: e.cods };
  };
  const desdeDict = (id: string, codigos: number[], dict: string[]) => {
    dim[id] = { id, etiquetas: dict.slice(), multi: false, cod: Uint16Array.from(codigos) };
  };
  uni('rol_familia', c.rol_familia); uni('seniority', c.seniority); uni('modalidad', c.modalidad);
  uni('modalidad_src', c.modalidad_src); uni('encaje', c.encaje); uni('ingles', c.ingles);
  uni('empleo', c.empleo); uni('estado', c.estado);
  desdeDict('rol', c.rol, s.dicts.rol); desdeDict('fuente', c.fuente, s.dicts.fuente);
  desdeDict('region', c.region, s.dicts.region); desdeDict('comuna', c.comuna, s.dicts.comuna);

  // empresa: se agrupa por clave canónica; se muestra el nombre original más frecuente
  const frec = new Map<string, Map<string, number>>();
  for (let i = 0; i < n; i++) {
    const k = c.empresa_canon[i], nombre = s.dicts.empresa[c.empresa[i]] ?? '';
    if (!k) continue;
    let m = frec.get(k);
    if (!m) frec.set(k, (m = new Map()));
    m.set(nombre, (m.get(nombre) ?? 0) + 1);
  }
  const nombreEmpresa = new Map<string, string>();
  for (const [k, m] of frec) nombreEmpresa.set(k, [...m.entries()].sort((a, b) => b[1] - a[1])[0][0]);
  uni('empresa', c.empresa_canon);
  dim.empresa.etiquetas = dim.empresa.etiquetas.map((k) => (k ? (nombreEmpresa.get(k) ?? k) : ''));

  const multi = (id: string, listas: (number[] | null | undefined)[], dict: string[]) => {
    dim[id] = { id, multi: true, ...csr(listas, dict) };
  };
  multi('tech', c.techs, s.dicts.tech);
  multi('beneficio', c.beneficios, s.dicts.tag);
  multi('alerta', c.alertas, s.dicts.tag);
  multi('a_favor', c.a_favor, s.dicts.tag);
  multi('fuentes', c.fuentes, s.dicts.fuente);

  const f64 = (a: (number | null)[]) => Float64Array.from(a, (x) => (x === null || x === undefined ? NaN : x));
  const sueldoValido = Uint8Array.from(c.sueldo_valido);
  const sueldo = Float64Array.from(c.sueldo, (x, i) => (x !== null && sueldoValido[i] ? x : NaN));
  const indice = new Map<number, number>();
  c.id.forEach((id, i) => indice.set(id, i));
  const texto = c.titulo.map((t, i) => `${t} ${s.dicts.empresa[c.empresa[i]] ?? ''} ${c.resumen[i] ?? ''}`.toLowerCase());
  return {
    n, snap: s, ids: c.id, dim, sueldo, sueldoValido,
    score: Float64Array.from(c.score), market: Float64Array.from(c.market_score),
    antiguedad: Float64Array.from(c.antiguedad), applicants: f64(c.applicants), exp: f64(c.exp_anios),
    nFuentes: Float64Array.from(c.n_fuentes), fecha: Int32Array.from(c.first_seen, diaEpoch),
    texto, indice, nombreEmpresa,
  };
}

/** Dimensión temporal derivada (dia | semana | mes) construida bajo demanda. */
export function dimTemporal(a: Almacen, id: 'dia' | 'semana' | 'mes'): DimData {
  const cache = (a as unknown as { _t?: Record<string, DimData> });
  cache._t ??= {};
  if (cache._t[id]) return cache._t[id];
  const claves = new Array<string>(a.n);
  for (let i = 0; i < a.n; i++) {
    const d = a.fecha[i];
    if (d < 0) { claves[i] = ''; continue; }
    const f = new Date(d * 86400000);
    const iso = f.toISOString().slice(0, 10);
    if (id === 'dia') claves[i] = iso;
    else if (id === 'mes') claves[i] = iso.slice(0, 7);
    else { // lunes de la semana
      const dow = (f.getUTCDay() + 6) % 7;
      claves[i] = new Date((d - dow) * 86400000).toISOString().slice(0, 10);
    }
  }
  const e = codificar(claves);
  return (cache._t[id] = { id, etiquetas: Array.from(e), multi: false, cod: e.cods });
}

export function getDim(a: Almacen, id: string): DimData {
  if (id === 'dia' || id === 'semana' || id === 'mes') return dimTemporal(a, id);
  const d = a.dim[id];
  if (!d) throw new Error(`dimensión desconocida: ${id}`);
  return d;
}

/** Códigos de la fila i en la dimensión d (univalor → 1 código; multivalor → 0..k; no informado → 0). */
export function codigosDe(d: DimData, i: number): ArrayLike<number> {
  if (!d.multi) return [d.cod![i]];
  return d.val!.subarray(d.off![i], d.off![i + 1]);
}
