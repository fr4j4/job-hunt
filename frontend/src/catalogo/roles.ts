// Roles y seniority — "¿Dónde está la demanda?" (V-30…V-33) + Condiciones (V-50…V-55)
import { clp, etiquetaValor, pct } from '../lib/formato';
import { agregar, matriz, type Celda } from '../motor/agregar';
import { barras100, barrasH, heatmap, type CeldaHM, type SerieApilada } from '../viz/formas';
import { colorEntidad, FAMILIAS, ORDEN_NATURAL, rampaOrdinal } from '../viz/tema';
import { masc, textoCobertura, vacio, type Ctx, type EspecGrafico } from './comun';

/** Heatmap rol × seniority (V-30 conteo, V-31 mediana de sueldo). */
export function rolSeniority(x: Ctx, metrica: 'ofertas' | 'sueldo_p50'): EspecGrafico {
  const id = metrica === 'ofertas' ? 'V-30' : 'V-31';
  const titulo = metrica === 'ofertas' ? '¿Qué roles y niveles se buscan?' : '¿Cuánto paga cada combinación?';
  const m = masc(x, 'rol', 'seniority'), sen = ORDEN_NATURAL.seniority;
  const r = matriz(x.a, 'seniority', 'rol', metrica, m, { sinDato: false });
  if (!r.celdas.length) return vacio(id, titulo, 'Sin ofertas con rol y seniority conocidos.', 'c8');
  const tot = new Map<string, number>(); r.celdas.forEach((c) => tot.set(c.y, (tot.get(c.y) ?? 0) + c.n));
  const ys = [...tot.entries()].filter(([y]) => y).sort((p, q) => q[1] - p[1]).map(([y]) => y);
  const xs = sen.filter((s) => r.celdas.some((c) => c.x === s));
  const celdas: CeldaHM[] = []; const por = new Map<string, Celda>(r.celdas.map((c) => [`${c.x}|${c.y}`, c]));
  ys.forEach((y, yi) => xs.forEach((s, xi) => { const c = por.get(`${s}|${y}`); if (c) celdas.push({ xi, yi, valor: metrica === 'ofertas' ? c.n : c.valor, n: c.n, estado: metrica === 'ofertas' ? 'ok' : c.estado, extra: metrica === 'sueldo_p50' ? `n con sueldo: ${c.nBase}` : '' }); }));
  const fmt = metrica === 'ofertas' ? (v: number) => String(Math.round(v)) : clp;
  return { id, titulo, span: 'c8', alto: ys.length * 26 + 100, subtitulo: metrica === 'ofertas' ? 'Número de ofertas' : 'Mediana del sueldo declarado · trama = menos de 5 ofertas con sueldo (no es cero)',
    cobertura: textoCobertura(r.cobertura, 'con rol y seniority'),
    opcion: heatmap(x.c, xs.map(etiquetaValor), ys, celdas.map((c) => ({ ...c })), { fmt, etiquetaValor: metrica === 'ofertas' ? 'ofertas' : 'sueldo mediano', rampa: x.c.secuencial, valorEnCelda: true }),
    tabla: { columnas: ['Rol', 'Seniority', 'Ofertas', metrica === 'ofertas' ? '' : 'Sueldo mediano'].filter(Boolean), filas: r.celdas.map((c) => [c.y, c.x, c.n, ...(metrica === 'ofertas' ? [] : [c.valor])]) } };
}

/** Barras 100 % apiladas: categoría (filas) × dimensión de color (partes del todo). */
export function apilado100(x: Ctx, id: string, titulo: string, filasDim: string, colorDim: string, o: { span?: EspecGrafico['span']; subtitulo?: string;
                           nota?: string; ordenFilas?: string[]; ordinal?: boolean; sinDatoGris?: boolean; limiteFilas?: number } = {}): EspecGrafico {
  const m = masc(x, filasDim, colorDim), span = o.span ?? 'c6';
  const r = matriz(x.a, colorDim, filasDim, 'ofertas', m, { sinDato: true });
  if (!r.celdas.length) return vacio(id, titulo, 'Sin ofertas con estos datos.', span);
  const tot = new Map<string, number>(); r.celdas.forEach((c) => tot.set(c.y, (tot.get(c.y) ?? 0) + c.n));
  let filas = (o.ordenFilas ?? FAMILIAS).filter((f) => tot.has(f));
  if (!filas.length || (!o.ordenFilas && filasDim !== 'rol_familia')) filas = [...tot.entries()].filter(([k]) => k).sort((p, q) => q[1] - p[1]).slice(0, o.limiteFilas ?? 12).map(([k]) => k);
  const partes = [...new Set(r.celdas.map((c) => c.x))];
  const orden = ORDEN_NATURAL[colorDim];
  partes.sort((p, q) => (p === '' ? 1 : q === '' ? -1 : orden ? orden.indexOf(p) - orden.indexOf(q) : 0));
  const conDato = partes.filter((p) => p !== '');
  const rampa = o.ordinal ? rampaOrdinal(x.c, conDato.length) : [];
  const color = (p: string) => (p === '' ? x.c.neutro : o.ordinal ? rampa[conDato.indexOf(p)] : colorEntidad(x.c, colorDim, p) === x.c.neutro ? x.c.serie[Math.min(7, conDato.indexOf(p))] : colorEntidad(x.c, colorDim, p));
  const por = new Map(r.celdas.map((c) => [`${c.x}|${c.y}`, c.n]));
  const series: SerieApilada[] = partes.map((p) => ({ nombre: etiquetaValor(p), color: color(p),
    valores: filas.map((f) => ((tot.get(f) ?? 0) ? (por.get(`${p}|${f}`) ?? 0) / (tot.get(f) as number) : null)), ns: filas.map((f) => por.get(`${p}|${f}`) ?? 0) }));
  return { id, titulo, span, alto: Math.max(150, filas.length * 34 + 70), subtitulo: o.subtitulo ?? 'Reparto de las ofertas de cada fila (100 %)', cobertura: o.nota ?? `${r.cobertura.total} ofertas`,
    aviso: filas.some((f) => (tot.get(f) ?? 0) < 10) ? 'filas con menos de 10 ofertas: porcentajes poco estables' : '',
    leyenda: series.map((s) => ({ nombre: s.nombre, color: s.color })),
    onclick: (e) => { const i = e.dataIndex, s = partes[e.seriesIndex]; if (i != null && s !== undefined) { x.filtrar(filasDim, filas[i]); } },
    opcion: barras100(x.c, filas.map(etiquetaValor), series, { totales: filas.map((f) => tot.get(f) ?? 0) }),
    tabla: { columnas: [filasDim, ...series.map((s) => s.nombre), 'Total'], filas: filas.map((f, i) => [f, ...series.map((s) => s.ns[i]), tot.get(f) ?? 0]) } };
}

/** Barras horizontales de conteo (o métrica) por dimensión, top-N + "Otras". */
export function barrasDim(x: Ctx, id: string, titulo: string, dim: string, o: { metrica?: string; top?: number; span?: EspecGrafico['span']; subtitulo?: string;
                          color?: string; sinDato?: boolean; fmt?: (v: number) => string; filtrable?: boolean; etiquetaValor?: string; orden?: 'valor' | 'natural' } = {}): EspecGrafico {
  const span = o.span ?? 'c6', metrica = o.metrica ?? 'ofertas', m = masc(x, dim);
  const res = agregar(x.a, { dim, metrica, mask: m, top: o.top ?? 15, otras: false, sinDato: o.sinDato ?? false, orden: o.orden ?? 'valor', ordenNatural: ORDEN_NATURAL[dim] });
  if (!res.grupos.length) return vacio(id, titulo, 'Sin datos en esta selección.', span);
  const items = res.grupos.map((g) => ({ nombre: etiquetaValor(g.clave) || 'Sin dato', valor: g.valor, n: g.n, nBase: g.nBase, estado: g.estado, clave: g.clave, filas: g.filas,
    color: o.color ?? colorEntidad(x.c, dim, g.clave) }));
  return { id, titulo, span, alto: Math.max(150, items.length * 24 + 40), subtitulo: o.subtitulo, cobertura: textoCobertura(res.cobertura, 'con este dato') + (res.cobertura.omitidos ? ` · ${res.cobertura.omitidos} más no se muestran` : ''),
    onclick: o.filtrable === false ? undefined : (e) => { const k = e.data?.meta?.clave; if (k && k !== '' && !String(k).startsWith('Otras')) x.filtrar(dim, k); },
    opcion: barrasH(x.c, items, { fmt: o.fmt ?? ((v) => String(Math.round(v))), etiquetaValor: o.etiquetaValor ?? 'ofertas' }),
    tabla: { columnas: [dim, 'Ofertas'], filas: res.grupos.map((g) => [g.clave || 'Sin dato', g.n]) } };
}
void pct;
