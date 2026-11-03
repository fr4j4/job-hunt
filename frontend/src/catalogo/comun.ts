// Contexto y tipos comunes del catálogo de visualizaciones. Cada V-xx es una función PURA
// (ctx) => EspecGrafico: se testea con datos de fixture sin DOM ni ECharts.
import type { Perfil } from '../lib/tipos';
import type { Almacen } from '../motor/almacen';
import type { Cobertura, Grupo } from '../motor/agregar';
import type { Filtros } from '../motor/filtros';
import { mascara } from '../motor/filtros';
import type { Colores } from '../viz/tema';
import type { EChartsOption } from '../viz/echarts';

export interface Ctx {
  a: Almacen; f: Filtros; c: Colores; perfil: Perfil | null; mias: Set<string>;
  filtrar: (dim: string, valor: string, aditivo?: boolean) => void;
  abrir: (id: number) => void;
  verOfertas: () => void;
}
export interface EspecGrafico {
  id: string; titulo: string; subtitulo?: string; span?: 'c4' | 'c6' | 'c8' | 'c12'; alto?: number;
  opcion?: EChartsOption | null; tabla?: { columnas: string[]; filas: (string | number | null)[][] } | null;
  estado?: 'ok' | 'vacio' | 'oculto'; mensaje?: string; cobertura?: string; aviso?: string;
  leyenda?: { nombre: string; color: string }[]; onclick?: (p: any) => void; tablaDirecta?: boolean;
  acciones?: { texto: string; fn: () => void }[];
}

/** Máscara con TODOS los filtros salvo los de la(s) dimensión(es) que el gráfico agrupa (filtro cruzado). */
export const masc = (x: Ctx, ...excluir: string[]) => mascara(x.a, x.f, excluir);

export function textoCobertura(cob: Cobertura, base: string): string {
  const partes = [`${cob.usadas.toLocaleString('es-CL')} de ${cob.total.toLocaleString('es-CL')} ofertas ${base}`];
  if (cob.excluidas > 0) partes.push(`${cob.excluidas.toLocaleString('es-CL')} sin dato`);
  return partes.join(' · ');
}
export function avisoChica(grupos: Grupo[]): string {
  const chicas = grupos.filter((g) => g.estado === 'chica').length, insuf = grupos.filter((g) => g.estado === 'insuficiente').length;
  return [chicas ? `${chicas} grupo(s) con muestra chica` : '', insuf ? `${insuf} sin muestra suficiente` : ''].filter(Boolean).join(' · ');
}
export const vacio = (id: string, titulo: string, mensaje: string, span: EspecGrafico['span'] = 'c6'): EspecGrafico =>
  ({ id, titulo, span, estado: 'vacio', mensaje });

/** Familia de rol del perfil (para comparar "tu rango"): la familia de la primera categoría reconocible. */
const FAM_ROL: Record<string, string> = { 'Full Stack': 'Desarrollo', Backend: 'Desarrollo', Frontend: 'Desarrollo', Mobile: 'Desarrollo',
  Software: 'Desarrollo', 'Tech Lead': 'Desarrollo', Data: 'Datos e IA', 'AI/ML': 'Datos e IA', 'DevOps/Cloud': 'Infra y seguridad',
  Seguridad: 'Infra y seguridad', 'Soporte/TI': 'Infra y seguridad', QA: 'QA' };
export function familiaPerfil(p: Perfil | null): string {
  for (const r of p?.roles ?? []) for (const [k, v] of Object.entries(FAM_ROL)) if (r.toLowerCase().includes(k.toLowerCase())) return v;
  return 'Desarrollo';
}
