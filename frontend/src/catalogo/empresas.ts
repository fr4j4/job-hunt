// Empresas — "¿Quién contrata y cómo?" (V-40…V-42)
import { clp, pct } from '../lib/formato';
import { agrupar, evaluar } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { percentil } from '../motor/estadistica';
import { base } from '../viz/formas';
import { tip } from '../viz/tip';
import { barrasDim } from './roles';
import { masc, vacio, type Ctx, type EspecGrafico } from './comun';

/** V-40 — ¿quién publica más? + concentración del top-10. */
export function v40(x: Ctx): EspecGrafico {
  const e = barrasDim(x, 'V-40', '¿Quién publica más?', 'empresa', { top: 20, color: x.c.serie[0] });
  const por = agrupar(x.a, 'empresa', masc(x, 'empresa'), { sinDato: false });
  const conteos = [...por.values()].map((f) => f.length).sort((p, q) => q - p), tot = conteos.reduce((s, v) => s + v, 0);
  if (tot >= 30) e.subtitulo = `Las 10 primeras concentran el ${pct(conteos.slice(0, 10).reduce((s, v) => s + v, 0) / tot)} de las ofertas con empresa informada`;
  return e;
}

interface FilaEmpresa { empresa: string; filas: number[] }
function empresas(x: Ctx): FilaEmpresa[] {
  const d = getDim(x.a, 'empresa');
  return [...agrupar(x.a, 'empresa', masc(x, 'empresa'), { sinDato: false }).entries()].map(([c, filas]) => ({ empresa: d.etiquetas[c], filas }))
    .sort((p, q) => q.filas.length - p.filas.length);
}

/** V-41 — tabla de empresas (ordenable en la UI): ofertas, familias, transparencia, sueldo mediano, encaje alto, última publicación. */
export function v41(x: Ctx): EspecGrafico {
  const titulo = 'Empresas: quién contrata, qué ofrece y qué tan claro lo dice', { a } = x;
  const es = empresas(x).slice(0, 60);
  if (!es.length) return vacio('V-41', titulo, 'Sin ofertas con empresa informada.', 'c12');
  const fam = a.snap.cols.rol_familia;
  const filas = es.map((e) => {
    const cuenta = new Map<string, number>(); e.filas.forEach((i) => cuenta.set(fam[i], (cuenta.get(fam[i]) ?? 0) + 1));
    const trans = evaluar(a, 'pct_con_sueldo', e.filas), sue = evaluar(a, 'sueldo_p50', e.filas), alto = evaluar(a, 'pct_encaje_alto', e.filas);
    return [e.empresa, e.filas.length, [...cuenta.entries()].sort((p, q) => q[1] - p[1]).map(([f, n]) => `${f} ${n}`).join(', '),
      Math.round((trans.valor ?? 0) * 100) + ' %', sue.valor === null ? '—' : clp(sue.valor) + (sue.estado === 'chica' ? ' ⚠' : ''),
      alto.valor === null ? '—' : Math.round(alto.valor * 100) + ' %', `${Math.min(...e.filas.map((i) => a.antiguedad[i]))} d`];
  });
  return { id: 'V-41', titulo, span: 'c12', alto: 420, tablaDirecta: true, subtitulo: 'Top 60 por número de ofertas · ⚠ = muestra chica (5–9 sueldos) · "—" = datos insuficientes',
    cobertura: 'Solo empresas con nombre informado', tabla: { columnas: ['Empresa', 'Ofertas', 'Familias', '% con sueldo', 'Sueldo mediano', 'Encaje alto', 'Última publicación'], filas } };
}

/** V-42 — ¿quién paga y lo dice? ofertas (x, log) vs % con sueldo (y), empresas con ≥3 ofertas. */
export function v42(x: Ctx): EspecGrafico {
  const titulo = '¿Quién publica el sueldo?', { a, c } = x;
  const todas = empresas(x), es = todas.filter((e) => e.filas.length >= 3);
  if (es.length < 3) return vacio('V-42', titulo, `Se necesitan al menos 3 empresas con 3+ ofertas (hay ${es.length}).`);
  const pts = es.map((e) => { const t = evaluar(a, 'pct_con_sueldo', e.filas); return { empresa: e.empresa, n: e.filas.length, p: t.valor ?? 0 }; });
  const top = new Set(pts.slice(0, 8).map((p) => p.empresa));
  const opcion = { ...base(c), grid: { left: 8, right: 24, top: 14, bottom: 34, containLabel: true },
    xAxis: { type: 'log', name: 'ofertas publicadas (escala log)', nameLocation: 'middle', nameGap: 24, min: 2, nameTextStyle: { color: c.suave }, axisLabel: { color: c.suave }, splitLine: { lineStyle: { color: c.rejilla } } },
    yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: c.suave, formatter: (v: number) => pct(v) }, splitLine: { lineStyle: { color: c.rejilla } }, name: '% con sueldo', nameTextStyle: { color: c.suave, align: 'left' } },
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (p: any) => tip(p.data.meta.empresa, [{ etiqueta: 'ofertas', valor: String(p.data.meta.n) }, { etiqueta: 'declaran sueldo', valor: pct(p.data.meta.p) }], 'Clic para filtrar por la empresa') },
    series: [{ type: 'scatter', symbolSize: 10, data: pts.map((p) => ({ value: [p.n, p.p], meta: p, itemStyle: { color: c.serie[0], borderColor: c.panel, borderWidth: 2 },
      label: { show: top.has(p.empresa), formatter: p.empresa.slice(0, 16), position: 'right', color: c.texto, fontSize: 11 } })),
      labelLayout: { hideOverlap: true } }] };
  return { id: 'V-42', titulo, span: 'c6', alto: 300, opcion, subtitulo: 'Empresas con 3 o más ofertas · arriba a la derecha: contratan mucho y publican el sueldo',
    cobertura: `${es.length} empresas con 3+ ofertas (de ${todas.length})`, aviso: es.some((e) => e.filas.length < 10) ? 'empresas con pocas ofertas: el % es poco estable' : '',
    onclick: (e) => e.data?.meta?.empresa && x.filtrar('empresa', e.data.meta.empresa),
    tabla: { columnas: ['Empresa', 'Ofertas', '% con sueldo'], filas: pts.map((p) => [p.empresa, p.n, Math.round(p.p * 100)]) } };
}
void percentil;
