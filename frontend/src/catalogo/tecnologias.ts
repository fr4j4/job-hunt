// Tecnologías — "¿Qué se pide, qué paga y qué me falta?" (V-20…V-25)
import { clp, pct, num } from '../lib/formato';
import { demandaTech, evaluar, estadoDe } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { lift, mediana, primaEstratificada } from '../motor/estadistica';
import { barrasH, heatmap, type CeldaHM } from '../viz/formas';
import { FAMILIAS } from '../viz/tema';
import { masc, vacio, type Ctx, type EspecGrafico } from './comun';

const notaBase = (d: { base: number; total: number }) => `% sobre las ${d.base} ofertas con tecnologías conocidas (de ${d.total}); una oferta puede pedir varias`;

/** V-20 — ¿qué tecnologías se piden más? Énfasis: las tuyas en color, el resto en gris. */
export function v20(x: Ctx, top = 25): EspecGrafico {
  const d = demandaTech(x.a, masc(x, 'tech'), top), titulo = '¿Qué tecnologías se piden más?';
  if (!d.items.length) return vacio('V-20', titulo, 'Ninguna oferta de esta selección informa tecnologías.');
  const mostrarPct = d.suficiente;
  const items = d.items.map((t) => ({ nombre: t.tech, valor: mostrarPct ? t.pct : t.n, n: t.n, nBase: d.base, estado: 'ok' as const,
    color: x.mias.has(t.tech) ? x.c.serie[0] : x.c.neutro, clave: t.tech, filas: t.filas }));
  return { id: 'V-20', titulo, span: 'c6', alto: Math.max(180, items.length * 24 + 40),
    subtitulo: `${x.mias.size ? 'En color, las de tu perfil · ' : ''}${mostrarPct ? 'porcentaje' : 'número'} de ofertas`,
    cobertura: notaBase(d), aviso: mostrarPct ? '' : `base de solo ${d.base} ofertas: se muestran conteos, no porcentajes`,
    onclick: (e) => e.data?.meta?.clave && x.filtrar('tech', e.data.meta.clave, true),
    opcion: barrasH(x.c, items, { fmt: (v) => (mostrarPct ? pct(v) : String(v)), etiquetaValor: mostrarPct ? '% de ofertas' : 'ofertas', rotulos: 6, unidadN: 'ofertas que la piden' }),
    tabla: { columnas: ['Tecnología', 'Ofertas', '% de la base'], filas: d.items.map((t) => [t.tech, t.n, Math.round(t.pct * 100)]) } };
}

/** V-21 — ¿qué me falta? Tecnologías demandadas que no están en tu perfil, con lo que pagan. */
export function v21(x: Ctx): EspecGrafico {
  const titulo = '¿Qué me falta?', d = demandaTech(x.a, masc(x, 'tech'), 200);
  if (!x.mias.size) return vacio('V-21', titulo, 'Define PROFILE_TECHS en .env para ver la brecha con tu perfil.');
  const falta = d.items.filter((t) => !x.mias.has(t.tech)).slice(0, 15);
  if (!falta.length) return vacio('V-21', titulo, 'No hay tecnologías demandadas fuera de tu perfil en esta selección.');
  const items = falta.map((t) => { const e = evaluar(x.a, 'sueldo_p50', t.filas);
    return { nombre: t.tech, valor: d.suficiente ? t.pct : t.n, n: t.n, nBase: d.base, estado: 'ok' as const, color: x.c.neutro, clave: t.tech, filas: t.filas,
             detalle: [{ etiqueta: 'sueldo mediano de esas ofertas', valor: e.valor === null ? `sin datos (n=${e.nBase})` : `${clp(e.valor)} (n=${e.nBase})` }] }; });
  return { id: 'V-21', titulo, span: 'c6', alto: Math.max(180, items.length * 24 + 40), subtitulo: 'Lo más pedido que no figura en tu perfil · pasa el cursor para ver cuánto pagan',
    cobertura: notaBase(d), onclick: (e) => e.data?.meta?.clave && x.filtrar('tech', e.data.meta.clave, true),
    opcion: barrasH(x.c, items, { fmt: (v) => (d.suficiente ? pct(v) : String(v)), etiquetaValor: d.suficiente ? '% de ofertas' : 'ofertas', rotulos: 5 }),
    tabla: { columnas: ['Tecnología', 'Ofertas', 'Sueldo mediano', 'n con sueldo'], filas: items.map((i) => { const e = evaluar(x.a, 'sueldo_p50', i.filas!); return [i.nombre, i.n, e.valor, e.nBase]; }) } };
}

/** V-22 — ¿qué tecnologías van juntas? lift = P(A∧B) / (P(A)·P(B)); 1 = independientes. */
export function v22(x: Ctx): EspecGrafico {
  const titulo = '¿Qué tecnologías van juntas?', { a } = x, d = getDim(a, 'tech'), m = masc(x, 'tech');
  const top = demandaTech(a, m, 18);
  if (top.base < 20) return { ...vacio('V-22', titulo, `Se necesitan al menos 20 ofertas con tecnologías conocidas (hay ${top.base}).`), subtitulo: 'Lift entre pares de tecnologías' };
  const nombres = top.items.map((t) => t.tech), cod = new Map(nombres.map((t, i) => [d.etiquetas.indexOf(t), i]));
  const n = nombres.length, pares = Array.from({ length: n }, () => new Array(n).fill(0));
  for (let i = 0; i < a.n; i++) {
    if (!m[i] || !d.conocida![i]) continue;
    const ks: number[] = [];
    for (let k = d.off![i]; k < d.off![i + 1]; k++) { const j = cod.get(d.val![k]); if (j !== undefined) ks.push(j); }
    for (const p of ks) for (const q of ks) pares[p][q]++;
  }
  const celdas: CeldaHM[] = [];
  for (let p = 0; p < n; p++) for (let q = 0; q < n; q++) {
    if (p === q) continue;
    const l = lift(pares[p][q], pares[p][p], pares[q][q], top.base), nn = pares[p][q];
    celdas.push({ xi: q, yi: p, valor: l === null ? null : Math.log2(l), n: nn, estado: nn >= 5 ? 'ok' : 'insuficiente', extra: l === null ? '' : `lift ${l.toFixed(2)}` });
  }
  return { id: 'V-22', titulo, span: 'c8', alto: n * 26 + 90, subtitulo: 'Rojo: aparecen juntas más de lo esperable · azul: menos · gris: independientes (lift = 1)',
    cobertura: `${top.base} ofertas con tecnologías conocidas · celdas con trama: menos de 5 ofertas con ambas`,
    opcion: heatmap(x.c, nombres, nombres, celdas, { fmt: (v) => (2 ** v).toFixed(1) + '×', etiquetaValor: 'lift', rampa: x.c.divergente, min: -2, max: 2, rotX: 45 }),
    tabla: { columnas: ['A', 'B', 'ofertas con ambas', 'lift'], filas: celdas.filter((c) => c.valor !== null && c.xi < c.yi).map((c) => [nombres[c.yi], nombres[c.xi], c.n, +(2 ** (c.valor as number)).toFixed(2)]) } };
}

/** V-23 — ¿qué tecnología paga más? Prima estratificada por familia×seniority, con avisos explícitos. */
export function v23(x: Ctx): EspecGrafico {
  const titulo = '¿Qué tecnología paga más? (indicativo)', { a } = x, m = masc(x, 'tech');
  const d = getDim(a, 'tech'), dem = demandaTech(a, m, 60);
  const fam = a.snap.cols.rol_familia, sen = a.snap.cols.seniority;
  const filasBase: { i: number; estrato: string }[] = [];
  for (let i = 0; i < a.n; i++) if (m[i] && !Number.isNaN(a.sueldo[i])) filasBase.push({ i, estrato: `${fam[i]}|${sen[i]}` });
  if (filasBase.length < 10) return { ...vacio('V-23', titulo, `Faltan ofertas con sueldo (n=${filasBase.length}, mínimo 10) para estimar diferencias.`), subtitulo: 'Diferencia de sueldo con y sin la tecnología' };
  const res: { tech: string; prima: number; nCon: number }[] = [];
  let omitidas = 0;
  for (const t of dem.items) {
    const code = d.etiquetas.indexOf(t.tech), tiene = new Set<number>();
    for (const i of t.filas) tiene.add(i);
    void code;
    const [p, nCon] = primaEstratificada(filasBase.map(({ i, estrato }) => [estrato, a.sueldo[i], tiene.has(i)] as [string, number, boolean]));
    if (p === null || nCon < 10) omitidas++; else res.push({ tech: t.tech, prima: p, nCon });
  }
  if (!res.length) return { ...vacio('V-23', titulo, 'Ninguna tecnología tiene ≥10 ofertas con sueldo comparables dentro de su familia y seniority.'), aviso: `${omitidas} tecnologías omitidas por muestra insuficiente` };
  res.sort((p, q) => q.prima - p.prima);
  const items = res.slice(0, 20).map((r) => ({ nombre: r.tech, valor: r.prima, n: r.nCon, estado: 'chica' as const, nBase: r.nCon,
    color: r.prima >= 0 ? x.c.divergente[0] : x.c.divergente[5], clave: r.tech }));
  return { id: 'V-23', titulo, span: 'c6', alto: Math.max(180, items.length * 24 + 40),
    subtitulo: 'Diferencia % de la mediana de sueldo con vs sin la tecnología, dentro de cada familia y seniority (correlación, no causa)',
    cobertura: `${filasBase.length} ofertas con sueldo · mínimo 10 comparables por tecnología`, aviso: `indicativo${omitidas ? ` · ${omitidas} tecnologías omitidas por muestra insuficiente` : ''}`,
    opcion: barrasH(x.c, items, { fmt: (v) => `${v >= 0 ? '+' : ''}${Math.round(v * 100)} %`, etiquetaValor: 'prima salarial', unidadN: 'ofertas con la tecnología y sueldo', rotulos: 8 }),
    tabla: { columnas: ['Tecnología', 'Prima %', 'Ofertas comparables'], filas: res.map((r) => [r.tech, Math.round(r.prima * 100), r.nCon]) } };
}

/** V-25 — ¿qué stack pide cada familia? Demanda de cada tecnología dentro de la familia. */
export function v25(x: Ctx): EspecGrafico {
  const titulo = '¿Qué stack pide cada familia?', { a } = x, m = masc(x, 'tech', 'rol_familia');
  const top = demandaTech(a, m, 16).items.map((t) => t.tech);
  if (!top.length) return vacio('V-25', titulo, 'Sin tecnologías conocidas en esta selección.');
  const d = getDim(a, 'tech'), df = getDim(a, 'rol_familia');
  const base = new Map<string, number>(), cuenta = new Map<string, number>();
  for (let i = 0; i < a.n; i++) {
    if (!m[i] || !d.conocida![i]) continue;
    const f = df.etiquetas[df.cod![i]]; base.set(f, (base.get(f) ?? 0) + 1);
    for (let k = d.off![i]; k < d.off![i + 1]; k++) { const t = d.etiquetas[d.val![k]]; if (top.includes(t)) cuenta.set(`${f}|${t}`, (cuenta.get(`${f}|${t}`) ?? 0) + 1); }
  }
  const fams = FAMILIAS.filter((f) => base.has(f));
  const celdas: CeldaHM[] = [];
  fams.forEach((f, xi) => top.forEach((t, yi) => { const b = base.get(f) ?? 0, n = cuenta.get(`${f}|${t}`) ?? 0;
    celdas.push({ xi, yi, valor: b >= 20 ? n / b : null, n, estado: b >= 20 ? 'ok' : 'insuficiente', extra: `base: ${b} ofertas de la familia con techs` }); }));
  return { id: 'V-25', titulo, span: 'c6', alto: top.length * 26 + 100, subtitulo: '% de las ofertas de la familia que piden cada tecnología', cobertura: 'columnas con trama: familia con menos de 20 ofertas con tecnologías conocidas',
    opcion: heatmap(x.c, fams, top, celdas, { fmt: (v) => pct(v), etiquetaValor: '% de la familia', rampa: x.c.secuencial, min: 0, valorEnCelda: true }),
    tabla: { columnas: ['Tecnología', ...fams], filas: top.map((t) => [t, ...fams.map((f) => { const b = base.get(f) ?? 0; return b >= 20 ? Math.round(((cuenta.get(`${f}|${t}`) ?? 0) / b) * 100) : null; })]) } };
}
void [estadoDe, mediana, num];
