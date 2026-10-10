// Tu posición — "¿Dónde están mis oportunidades?" (V-80, V-81) y calidad de datos (V-72, V-73)
import { clp, etiquetaValor } from '../lib/formato';
import { agrupar } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { percentil } from '../motor/estadistica';
import { barrasH, dispersion } from '../viz/formas';
import { rampaOrdinal } from '../viz/tema';
import { apilado100 } from './roles';
import { masc, vacio, type Ctx, type EspecGrafico } from './comun';

const NIVELES = ['ninguno', 'bajo', 'medio', 'alto'];

/** V-80 — mapa de oportunidades: puntaje (x) vs sueldo (y). Las ofertas sin sueldo van en un carril aparte. */
export function v80(x: Ctx): EspecGrafico {
  const titulo = 'Mapa de oportunidades', m = masc(x), { a } = x, p = x.perfil;
  const enc = getDim(a, 'encaje');
  const rampa = rampaOrdinal(x.c, 4);
  const cats = [...NIVELES.map((n, i) => ({ nombre: `Encaje ${n}`, color: rampa[i] })), { nombre: 'Encaje sin evaluar', color: x.c.neutro }];
  const puntos: { x: number; y: number | null; id: number; titulo: string; empresa: string; cat: number }[] = [];
  const sue: number[] = [];
  for (let i = 0; i < a.n; i++) {
    if (!m[i]) continue;
    const e = enc.etiquetas[enc.cod![i]], k = e ? NIVELES.indexOf(e) : 4;
    const y = Number.isNaN(a.sueldo[i]) ? null : a.sueldo[i]; if (y !== null) sue.push(y);
    puntos.push({ x: a.score[i], y, id: a.ids[i], titulo: a.snap.cols.titulo[i], empresa: a.snap.dicts.empresa[a.snap.cols.empresa[i]] ?? '', cat: k < 0 ? 4 : k });
  }
  if (!puntos.length) return vacio('V-80', titulo, 'Sin ofertas en esta selección.', 'c12');
  if (sue.length < 3) return vacio('V-80', titulo, `Se necesitan al menos 3 ofertas con sueldo para el mapa (hay ${sue.length}).`, 'c12');
  const yMax = Math.min(Math.max(...sue), (percentil(sue, 98) as number) * 1.1), yMin = Math.max(0, Math.min(...sue) * 0.9);
  const dentro = puntos.map((q) => (q.y !== null && q.y > yMax ? { ...q, y: yMax } : q));
  const sinSueldo = puntos.filter((q) => q.y === null).length;
  return { id: 'V-80', titulo, span: 'c12', alto: 380, subtitulo: 'Arriba a la derecha: encajan con tu perfil y pagan. Clic en un punto abre la oferta',
    cobertura: `${sue.length} ofertas con sueldo · ${sinSueldo} sin sueldo en el carril inferior (no se descartan)`,
    leyenda: cats.map((c) => ({ nombre: c.nombre, color: c.color })), onclick: (e) => e.data?.meta?.id && x.abrir(e.data.meta.id),
    opcion: dispersion(x.c, dentro, cats, { fmtX: String, fmtY: clp, carril: 'Sin sueldo declarado', ejeX: 'Puntaje de encaje (0–100)', ejeY: 'Sueldo mensual',
      refX: p?.min_fit ? { valor: p.min_fit, texto: `mínimo del canal (${p.min_fit})` } : undefined,
      refY: p?.salary_min ? { valor: p.salary_min, texto: `tu mínimo ${clp(p.salary_min)}` } : undefined, yMin, yMax }),
    tabla: { columnas: ['Cargo', 'Puntaje', 'Sueldo'], filas: puntos.slice(0, 300).map((q) => [q.titulo, q.x, q.y]) } };
}

/** V-81 — ¿cuántas encajan conmigo y en qué roles? */
export const v81 = (x: Ctx): EspecGrafico => apilado100(x, 'V-81', '¿Cuántas encajan conmigo, por rol?', 'rol', 'encaje',
  { ordinal: true, subtitulo: 'Veredicto de encaje de la IA por rol (100 %) · gris: sin evaluar', limiteFilas: 12 });

/** V-72 — ¿qué fuente aporta ofertas que nadie más tiene? (intersecciones de fuentes) */
export function v72(x: Ctx): EspecGrafico {
  const titulo = '¿Qué fuente aporta ofertas únicas?', { a } = x, m = masc(x), df = getDim(a, 'fuentes');
  const cuenta = new Map<string, number[]>();
  for (let i = 0; i < a.n; i++) {
    if (!m[i]) continue;
    const fs = new Set<string>(); for (let k = df.off![i]; k < df.off![i + 1]; k++) fs.add(df.etiquetas[df.val![k]]);
    if (!fs.size) continue;
    const clave = [...fs].sort().join(' + '); (cuenta.get(clave) ?? cuenta.set(clave, []).get(clave)!).push(i);
  }
  const top = [...cuenta.entries()].sort((p, q) => q[1].length - p[1].length).slice(0, 12);
  if (!top.length) return vacio('V-72', titulo, 'Sin ofertas con fuente informada.');
  const multi = [...cuenta.entries()].filter(([k]) => k.includes('+')).reduce((s, [, v]) => s + v.length, 0), tot = [...cuenta.values()].reduce((s, v) => s + v.length, 0);
  return { id: 'V-72', titulo, span: 'c6', alto: top.length * 26 + 40, subtitulo: `${Math.round((multi / tot) * 100)} % de las ofertas aparece en 2+ fuentes · una barra por combinación de fuentes`,
    cobertura: `${tot} ofertas`, opcion: barrasH(x.c, top.map(([k, f]) => ({ nombre: k, valor: f.length, n: f.length, estado: 'ok' as const, color: k.includes('+') ? x.c.serie[1] : x.c.serie[0], filas: f })),
      { fmt: String, etiquetaValor: 'ofertas', rotulos: 6 }),
    tabla: { columnas: ['Fuentes', 'Ofertas'], filas: top.map(([k, f]) => [k, f.length]) } };
}

/** V-73 — ¿coinciden los dos puntajes? encaje con tu perfil vs calidad de la publicación. */
export function v73(x: Ctx): EspecGrafico {
  const titulo = '¿Coinciden los dos puntajes?', m = masc(x), { a } = x, enc = getDim(a, 'encaje');
  const rampa = rampaOrdinal(x.c, 4);
  const cats = [...NIVELES.map((n, i) => ({ nombre: `Encaje ${n}`, color: rampa[i] })), { nombre: 'Sin evaluar', color: x.c.neutro }];
  const puntos = [];
  for (let i = 0; i < a.n; i++) if (m[i]) { const e = enc.etiquetas[enc.cod![i]], k = e ? NIVELES.indexOf(e) : 4;
    puntos.push({ x: a.score[i], y: a.market[i], id: a.ids[i], titulo: a.snap.cols.titulo[i], empresa: a.snap.dicts.empresa[a.snap.cols.empresa[i]] ?? '', cat: k < 0 ? 4 : k }); }
  if (!puntos.length) return vacio('V-73', titulo, 'Sin ofertas en esta selección.');
  return { id: 'V-73', titulo, span: 'c6', alto: 320, subtitulo: 'x: qué tan bien encaja contigo · y: qué tan completa y clara es la publicación', cobertura: `${puntos.length} ofertas`,
    leyenda: cats.map((c) => ({ nombre: c.nombre, color: c.color })), onclick: (e) => e.data?.meta?.id && x.abrir(e.data.meta.id),
    opcion: dispersion(x.c, puntos as any, cats, { fmtX: String, fmtY: String, ejeX: 'Puntaje de encaje', ejeY: 'Puntaje de mercado', yMin: 0, yMax: 100 }),
    tabla: { columnas: ['Cargo', 'Encaje', 'Mercado'], filas: puntos.slice(0, 300).map((q) => [q.titulo, q.x, q.y]) } };
}
void [agrupar, etiquetaValor];
