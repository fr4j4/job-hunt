// Comparar dos segmentos (A vs B) con las mismas métricas y degradaciones del resto del análisis.
import { clp, etiquetaValor, pct } from '../lib/formato';
import { demandaTech, evaluar, type Grupo } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { percentil } from '../motor/estadistica';
import { mascara } from '../motor/filtros';
import { stripCaja } from '../viz/formas';
import type { Ctx, EspecGrafico } from './comun';

export interface ResumenSegmento {
  etiqueta: string; n: number; nSueldo: number; pctSueldo: number | null; mediana: number | null; ic: [number, number] | null;
  p25: number | null; p75: number | null; estadoSueldo: Grupo['estado']; score: number | null; encajeAlto: number | null; antiguedad: number | null;
  techs: { tech: string; pct: number }[]; base: number; filas: number[];
}
export interface Comparacion { a: ResumenSegmento; b: ResumenSegmento; veredicto: string; espec: EspecGrafico }

export function resumir(x: Ctx, dim: string, valor: string): ResumenSegmento {
  const f = { ...x.f, sel: { ...x.f.sel, [dim]: [valor] } };       // el segmento reemplaza el filtro de esa dimensión
  const m = mascara(x.a, f);
  const filas: number[] = []; for (let i = 0; i < x.a.n; i++) if (m[i]) filas.push(i);
  const sue = evaluar(x.a, 'sueldo_p50', filas, { bootstrap: true }), tr = evaluar(x.a, 'pct_con_sueldo', filas);
  const v = filas.map((i) => x.a.sueldo[i]).filter((s) => !Number.isNaN(s));
  const dem = demandaTech(x.a, m, 5);
  return { etiqueta: etiquetaValor(valor), n: filas.length, nSueldo: v.length, pctSueldo: tr.valor, mediana: sue.valor, ic: sue.ic ?? null,
    p25: sue.estado === 'insuficiente' ? null : percentil(v, 25), p75: sue.estado === 'insuficiente' ? null : percentil(v, 75), estadoSueldo: sue.estado,
    score: evaluar(x.a, 'score_medio', filas).valor, encajeAlto: evaluar(x.a, 'pct_encaje_alto', filas).valor,
    antiguedad: evaluar(x.a, 'antiguedad_mediana', filas).valor, techs: dem.items.map((t) => ({ tech: t.tech, pct: t.pct })), base: dem.base, filas };
}

/** Veredicto honesto: sin muestra o con intervalos solapados NO se declara diferencia. */
export function veredicto(a: ResumenSegmento, b: ResumenSegmento): string {
  if (a.n === 0 || b.n === 0) return 'Uno de los segmentos no tiene ofertas.';
  if (a.estadoSueldo === 'insuficiente' || b.estadoSueldo === 'insuficiente' || a.mediana === null || b.mediana === null)
    return `Sin sueldos suficientes para comparar (${a.etiqueta}: n=${a.nSueldo}, ${b.etiqueta}: n=${b.nSueldo}; mínimo 5 cada uno).`;
  const dif = b.mediana - a.mediana, rel = dif / a.mediana;
  const solapan = !!a.ic && !!b.ic && a.ic[0] <= b.ic[1] && b.ic[0] <= a.ic[1];
  const fuerte = `${b.etiqueta} paga ${pct(Math.abs(rel))} ${dif >= 0 ? 'más' : 'menos'} que ${a.etiqueta} (${clp(b.mediana)} vs ${clp(a.mediana)}).`;
  if (!a.ic || !b.ic) return `${fuerte} Sin intervalo de confianza: tómalo como referencia.`;
  return solapan ? `Los intervalos de confianza se solapan: no hay evidencia clara de diferencia. ${fuerte}` : `${fuerte} Los intervalos no se solapan: la diferencia es consistente con estos datos.`;
}

export function comparar(x: Ctx, dim: string, valA: string, valB: string): Comparacion {
  const a = resumir(x, dim, valA), b = resumir(x, dim, valB);
  const cats = [a, b].map((s, k) => ({ nombre: s.etiqueta, color: x.c.serie[k], n: s.nSueldo, p25: s.estadoSueldo === 'insuficiente' ? null : s.p25,
    p50: s.estadoSueldo === 'insuficiente' ? null : s.mediana, p75: s.estadoSueldo === 'insuficiente' ? null : s.p75, ic: s.ic }));
  const puntos = [a, b].flatMap((s, k) => s.filas.filter((i) => !Number.isNaN(x.a.sueldo[i])).map((i) => ({ cat: k, valor: x.a.sueldo[i], id: x.a.ids[i],
    titulo: x.a.snap.cols.titulo[i], empresa: x.a.snap.dicts.empresa[x.a.snap.cols.empresa[i]] ?? '' })));
  const d = getDim(x.a, dim);
  void d;
  const espec: EspecGrafico = { id: 'CMP', titulo: `Sueldos: ${a.etiqueta} vs ${b.etiqueta}`, span: 'c12', alto: 220, subtitulo: 'Cada punto es una oferta · caja p25–p75 · raya mediana · barra inferior IC 90 %',
    cobertura: `${a.etiqueta}: ${a.nSueldo} con sueldo de ${a.n} · ${b.etiqueta}: ${b.nSueldo} con sueldo de ${b.n}`, leyenda: cats.map((c) => ({ nombre: c.nombre, color: c.color })),
    onclick: (e) => e.data?.meta?.id && x.abrir(e.data.meta.id),
    opcion: puntos.length ? stripCaja(x.c, cats, puntos, { fmt: clp }) : null, estado: puntos.length ? 'ok' : 'vacio', mensaje: 'Ninguno de los dos segmentos declara sueldo.',
    tabla: { columnas: ['Segmento', 'Ofertas', 'Con sueldo', 'Mediana', 'p25', 'p75'], filas: [a, b].map((s) => [s.etiqueta, s.n, s.nSueldo, s.mediana, s.p25, s.p75]) } };
  return { a, b, veredicto: veredicto(a, b), espec };
}
