// Explorador libre: una spec {x, color, metrica, forma} se traduce a un gráfico usando las MISMAS métricas
// y degradaciones del modelo semántico. El recomendador elige la forma y las barandas evitan gráficos engañosos.
import { clp, etiquetaValor, pct } from '../lib/formato';
import { agregar, matriz, type Celda } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { percentil } from '../motor/estadistica';
import { heatmap, histograma, lineas, type CeldaHM } from '../viz/formas';
import { ORDEN_NATURAL } from '../viz/tema';
import { fechaCorta } from '../lib/formato';
import { masc, vacio, type Ctx, type EspecGrafico } from './comun';
import { apilado100, barrasDim } from './roles';
import { sueldosPor } from './sueldos';

export type Forma = 'barras' | 'strip' | 'apiladas' | 'heatmap' | 'lineas' | 'histograma' | 'tabla';
export interface SpecEx { x: string; color: string; metrica: string; forma: Forma | '' }

export const METRICAS_EX = ['ofertas', 'pct_con_sueldo', 'sueldo_p50', 'score_medio', 'antiguedad_mediana', 'pct_encaje_alto', 'pct_multifuente'];
export const DIMS_X = ['rol_familia', 'rol', 'seniority', 'modalidad', 'fuente', 'encaje', 'ingles', 'empleo', 'region', 'comuna', 'empresa', 'tech',
  'beneficio', 'alerta', 'dia', 'semana', 'mes', 'sueldo', 'score', 'antiguedad'];
export const DIMS_COLOR = ['rol_familia', 'seniority', 'modalidad', 'fuente', 'encaje', 'ingles', 'empleo', 'region'];
const TEMPORALES = ['dia', 'semana', 'mes'], CUANT = ['sueldo', 'score', 'antiguedad'];

export interface Recomendacion { forma: Forma; alternativas: Forma[]; avisos: string[]; invalidas: Record<string, string> }

export function recomendar(s: SpecEx, cardColor = 0): Recomendacion {
  const av: string[] = [], inv: Record<string, string> = {};
  const temporal = TEMPORALES.includes(s.x), cuant = CUANT.includes(s.x), conColor = !!s.color;
  let forma: Forma, alt: Forma[];
  if (cuant) { forma = 'histograma'; alt = ['tabla']; if (conColor) av.push('El histograma no admite color: se ignora el desglose.'); }
  else if (temporal) { forma = 'lineas'; alt = ['barras', 'tabla']; if (conColor) av.push('Las líneas no admiten desglose por color todavía: se ignora.'); }
  else if (conColor && s.metrica === 'ofertas') { forma = 'apiladas'; alt = ['heatmap', 'tabla']; }
  else if (conColor) { forma = 'heatmap'; alt = ['tabla']; }
  else if (s.metrica === 'sueldo_p50') { forma = 'strip'; alt = ['barras', 'tabla']; }
  else { forma = 'barras'; alt = ['tabla']; }
  if (!temporal && !cuant && !conColor && forma !== 'strip') alt = ['tabla'];
  if (conColor && cardColor > 8) { forma = 'heatmap'; av.push(`El desglose tiene ${cardColor} valores: más de 8 colores no se distinguen. Se usa un mapa de calor.`); inv.apiladas = 'más de 8 colores'; }
  if (temporal || cuant) { inv.apiladas = 'requiere dos dimensiones nominales'; inv.heatmap = 'requiere dos dimensiones nominales'; }
  if (cuant && s.metrica !== 'ofertas') av.push('Con una dimensión numérica se cuenta ofertas por tramo (la métrica elegida se ignora).');
  if (s.x === 'tech' && s.color) av.push('Una oferta puede tener varias tecnologías: los totales no suman las ofertas.');
  if (s.metrica === 'sueldo_p50') av.push('Los sueldos solo existen en ~1 de cada 5 ofertas: cada grupo muestra su n.');
  return { forma, alternativas: alt, avisos: av, invalidas: inv };
}

const FMT: Record<string, (v: number) => string> = { ofertas: (v) => String(Math.round(v)), pct_con_sueldo: (v) => pct(v), pct_encaje_alto: (v) => pct(v),
  pct_multifuente: (v) => pct(v), sueldo_p50: clp, score_medio: (v) => v.toFixed(1), antiguedad_mediana: (v) => `${Math.round(v)} d` };
export const ETQ_METRICA: Record<string, string> = { ofertas: 'Número de ofertas', pct_con_sueldo: '% que declara sueldo', sueldo_p50: 'Sueldo mediano', score_medio: 'Puntaje medio',
  antiguedad_mediana: 'Antigüedad mediana', pct_encaje_alto: '% con encaje alto', pct_multifuente: '% en 2+ fuentes' };

export function construir(x: Ctx, s: SpecEx, etiquetaDim: (d: string) => string): { espec: EspecGrafico; rec: Recomendacion } {
  const a = x.a;
  const cardColor = s.color ? new Set(Array.from({ length: a.n }, (_, i) => getDim(a, s.color).cod![i])).size - (getDim(a, s.color).etiquetas.includes('') ? 1 : 0) : 0;
  const rec = recomendar(s, cardColor);
  const forma = (s.forma && !rec.invalidas[s.forma] ? s.forma : rec.forma) as Forma;
  const titulo = `${ETQ_METRICA[s.metrica]} por ${etiquetaDim(s.x).toLowerCase()}${s.color ? ' y ' + etiquetaDim(s.color).toLowerCase() : ''}`;
  const fmt = FMT[s.metrica] ?? FMT.ofertas, id = 'EX';
  let e: EspecGrafico;
  if (forma === 'histograma') {
    const m = masc(x, s.x), col = s.x === 'sueldo' ? a.sueldo : s.x === 'score' ? a.score : a.antiguedad, v: number[] = [];
    for (let i = 0; i < a.n; i++) if (m[i] && !Number.isNaN(col[i])) v.push(col[i]);
    if (!v.length) e = vacio(id, titulo, 'Ninguna oferta tiene este dato en la selección actual.', 'c12');
    else { const mx = Math.max(...v), mn = Math.min(...v), nb = 12, paso = (mx - mn) / nb || 1, cu = new Array(nb).fill(0);
      v.forEach((q) => { cu[Math.min(nb - 1, Math.floor((q - mn) / paso))]++; });
      const et = cu.map((_, i) => (s.x === 'sueldo' ? clp(mn + i * paso) : String(Math.round(mn + i * paso))));
      e = { id, titulo, span: 'c12', alto: 300, subtitulo: `${v.length} ofertas con dato`, cobertura: `${v.length} ofertas`, opcion: histograma(x.c, et, cu, { color: x.c.serie[0] }),
            tabla: { columnas: ['Desde', 'Ofertas'], filas: et.map((q, i) => [q, cu[i]]) } }; }
  } else if (forma === 'lineas' || (TEMPORALES.includes(s.x) && forma === 'barras')) {
    const r = agregar(a, { dim: s.x, metrica: s.metrica, mask: masc(x, s.x), sinDato: false, orden: 'etiqueta' });
    if (!r.grupos.length) e = vacio(id, titulo, 'Sin datos con fecha en la selección.', 'c12');
    else e = { id, titulo, span: 'c12', alto: 300, subtitulo: 'Por fecha de primera vez vista · los grupos con muestra insuficiente quedan en blanco', cobertura: `${r.cobertura.usadas} ofertas`,
      opcion: lineas(x.c, r.grupos.map((g) => g.clave), [{ nombre: ETQ_METRICA[s.metrica], color: x.c.serie[0], valores: r.grupos.map((g) => g.valor), ns: r.grupos.map((g) => g.n) }], { fmt, etiquetaFecha: (f) => (f.length === 10 ? fechaCorta(f) : f) }),
      tabla: { columnas: [etiquetaDim(s.x), 'Valor', 'n'], filas: r.grupos.map((g) => [g.clave, g.valor, g.n]) } };
  } else if (forma === 'apiladas' && s.color) {
    e = apilado100(x, id, titulo, s.x, s.color, { span: 'c12', ordinal: ['seniority', 'encaje', 'ingles'].includes(s.color), subtitulo: 'Reparto (100 %) de cada fila por el desglose elegido' });
  } else if (forma === 'heatmap' && s.color) {
    const r = matriz(a, s.color, s.x, s.metrica, masc(x, s.x, s.color), { sinDato: false });
    if (!r.celdas.length) e = vacio(id, titulo, 'Sin datos para esta combinación.', 'c12');
    else { const tot = new Map<string, number>(); r.celdas.forEach((c) => tot.set(c.y, (tot.get(c.y) ?? 0) + c.n));
      const ys = [...tot.entries()].sort((p, q) => q[1] - p[1]).slice(0, 18).map(([k]) => k);
      const ord = ORDEN_NATURAL[s.color], xs = [...new Set(r.celdas.map((c) => c.x))].filter(Boolean).sort((p, q) => (ord ? ord.indexOf(p) - ord.indexOf(q) : p.localeCompare(q)));
      const por = new Map<string, Celda>(r.celdas.map((c) => [`${c.x}|${c.y}`, c])), celdas: CeldaHM[] = [];
      ys.forEach((y, yi) => xs.forEach((c, xi) => { const k = por.get(`${c}|${y}`); if (k) celdas.push({ xi, yi, valor: k.valor, n: k.n, estado: k.estado, extra: s.metrica.startsWith('sueldo') ? `n con sueldo: ${k.nBase}` : '' }); }));
      e = { id, titulo, span: 'c12', alto: ys.length * 26 + 100, subtitulo: 'Trama = muestra insuficiente (no es cero)', cobertura: `${r.cobertura.usadas} ofertas`,
        opcion: heatmap(x.c, xs.map(etiquetaValor), ys.map((q) => etiquetaValor(q)), celdas, { fmt, etiquetaValor: ETQ_METRICA[s.metrica], rampa: x.c.secuencial, valorEnCelda: true }),
        tabla: { columnas: [etiquetaDim(s.x), etiquetaDim(s.color), 'Valor', 'n'], filas: r.celdas.map((c) => [c.y, c.x, c.valor, c.n]) } }; }
  } else if (forma === 'strip') {
    e = { ...sueldosPor(x, s.x, id, titulo, 'c12') };
  } else if (forma === 'tabla') {
    const r = agregar(a, { dim: s.x, metrica: s.metrica, mask: masc(x, s.x), sinDato: true });
    e = { id, titulo, span: 'c12', alto: 380, tablaDirecta: true, tabla: { columnas: [etiquetaDim(s.x), ETQ_METRICA[s.metrica], 'Ofertas', 'n de la base', 'Muestra'], filas: r.grupos.map((g) => [etiquetaValor(g.clave) || 'Sin dato', g.valor === null ? null : +g.valor.toFixed(3), g.n, g.nBase, g.estado]) },
      cobertura: `${r.cobertura.usadas} de ${r.cobertura.total} ofertas con dato` };
  } else {
    e = { ...barrasDim(x, id, titulo, s.x, { metrica: s.metrica, top: 18, span: 'c12', fmt, etiquetaValor: ETQ_METRICA[s.metrica], orden: ORDEN_NATURAL[s.x] ? 'natural' : 'valor' }) };
  }
  return { espec: e, rec };
}

export const EJEMPLOS: { nombre: string; q: string; spec: SpecEx }[] = [
  { nombre: 'Demanda de techs en Desarrollo senior', q: 'fam=Desarrollo&sen=senior', spec: { x: 'tech', color: '', metrica: 'ofertas', forma: 'barras' } },
  { nombre: 'Mediana de sueldo por modalidad en Datos e IA', q: 'fam=Datos%20e%20IA', spec: { x: 'modalidad', color: '', metrica: 'sueldo_p50', forma: 'strip' } },
  { nombre: 'Transparencia salarial por fuente', q: '', spec: { x: 'fuente', color: '', metrica: 'pct_con_sueldo', forma: 'barras' } },
  { nombre: 'Empresas con más ofertas remotas', q: 'mod=remoto', spec: { x: 'empresa', color: '', metrica: 'ofertas', forma: 'barras' } },
  { nombre: 'Techs más pedidas junto a Python', q: 'tec=Python', spec: { x: 'tech', color: '', metrica: 'ofertas', forma: 'barras' } },
];
void percentil;
