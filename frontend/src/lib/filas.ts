// Accesores de fila sobre el almacén (la lista y las tarjetas leen de aquí, nunca del DOM).
import type { Almacen } from '../motor/almacen';
import { getDim } from '../motor/almacen';

export interface FilaOferta {
  i: number; id: number; titulo: string; empresa: string; url: string; fuente: string; fuentes: string[];
  rol: string; familia: string; seniority: string; modalidad: string; modalidadSrc: string; region: string; comuna: string;
  sueldo: number | null; techs: string[]; score: number; market: number; encaje: string; antiguedad: number;
  resumen: string; estado: string; posibleCerrada: boolean; ingles: string; nFuentes: number; staffing: boolean;
}
const cat = (a: Almacen, dim: string, i: number) => { const d = getDim(a, dim); return d.etiquetas[d.cod![i]]; };

export function fila(a: Almacen, i: number): FilaOferta {
  const c = a.snap.cols, s = a.snap.dicts;
  const tech = a.dim.tech, fu = a.dim.fuentes;
  const techs: string[] = [];
  for (let k = tech.off![i]; k < tech.off![i + 1]; k++) techs.push(tech.etiquetas[tech.val![k]]);
  const fuentes: string[] = [];
  for (let k = fu.off![i]; k < fu.off![i + 1]; k++) fuentes.push(fu.etiquetas[fu.val![k]]);
  return {
    i, id: c.id[i], titulo: c.titulo[i], empresa: s.empresa[c.empresa[i]] ?? '', url: c.url[i], fuente: cat(a, 'fuente', i), fuentes,
    rol: cat(a, 'rol', i), familia: c.rol_familia[i], seniority: c.seniority[i], modalidad: c.modalidad[i], modalidadSrc: c.modalidad_src[i],
    region: cat(a, 'region', i), comuna: cat(a, 'comuna', i),
    sueldo: Number.isNaN(a.sueldo[i]) ? null : a.sueldo[i], techs, score: c.score[i], market: c.market_score[i],
    encaje: c.encaje[i], antiguedad: c.antiguedad[i], resumen: c.resumen[i], estado: c.estado[i],
    posibleCerrada: !!c.posible_cerrada[i], ingles: c.ingles[i], nFuentes: c.n_fuentes[i], staffing: !!c.staffing[i],
  };
}

export const urlSegura = (u: string) => (/^https?:\/\//i.test(u) ? u : '');
