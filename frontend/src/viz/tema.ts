import { DIVERGENTE, ESTADO, NEUTRO, ORDINAL, PALETA, SECUENCIAL } from './paleta.mjs';

export interface Colores {
  oscuro: boolean; serie: string[]; ordinal: string[]; secuencial: string[]; divergente: string[];
  neutro: string; texto: string; suave: string; rejilla: string; eje: string; panel: string; acento: string;
  estado: typeof ESTADO;
}
const FALLBACK: Record<string, string> = { '--texto': '#1d2330', '--suave': '#5b6475', '--rejilla': '#e8eaee', '--eje': '#c3c2b7', '--panel': '#ffffff', '--acento': '#2563eb' };
const css = (v: string) => (typeof document === 'undefined' ? FALLBACK[v] ?? '' : getComputedStyle(document.documentElement).getPropertyValue(v).trim());

export function colores(oscuro: boolean): Colores {
  const m = oscuro ? 'dark' : 'light';
  return { oscuro, serie: PALETA[m], ordinal: ORDINAL[m], secuencial: SECUENCIAL[m], divergente: DIVERGENTE[m],
           neutro: NEUTRO[m], texto: css('--texto'), suave: css('--suave'), rejilla: css('--rejilla'), eje: css('--eje'),
           panel: css('--panel'), acento: css('--acento'), estado: ESTADO };
}

/** Color fijo por entidad (no por ranking): filtrar no repinta a los que quedan (spec §7.3). */
export const FAMILIAS = ['Desarrollo', 'Datos e IA', 'Infra y seguridad', 'QA', 'No desarrollo'];
export const FUENTES = ['linkedin', 'computrabajo', 'laborum', 'indeed', 'jooble', 'glassdoor', 'aira', 'accenture'];
export const MODALIDADES = ['remoto', 'hibrido', 'presencial'];

export function colorEntidad(c: Colores, dim: string, valor: string): string {
  if (valor === '' || valor.startsWith('Otras')) return c.neutro;
  if (dim === 'rol_familia') { const i = FAMILIAS.indexOf(valor); return i < 0 || i === 4 ? c.neutro : c.serie[i]; }
  if (dim === 'fuente') { const i = FUENTES.indexOf(valor); return i < 0 ? c.neutro : c.serie[i]; }
  if (dim === 'modalidad') { const i = MODALIDADES.indexOf(valor); return i < 0 ? c.neutro : c.serie[i]; }
  return c.serie[0];
}
export const ORDEN_NATURAL: Record<string, string[]> = {
  rol_familia: FAMILIAS, seniority: ['trainee', 'junior', 'semi', 'senior', 'lead'],
  modalidad: MODALIDADES, encaje: ['ninguno', 'bajo', 'medio', 'alto'], ingles: ['no', 'deseable', 'requerido'],
  estado: ['guardada', 'postulada', 'entrevista', 'oferta', 'descartada'],
};
/** Rampa ordinal de n niveles (interpolando entre los pasos validados). */
export function rampaOrdinal(c: Colores, n: number): string[] {
  if (n <= c.ordinal.length) {
    if (n === c.ordinal.length) return c.ordinal;
    const paso = (c.ordinal.length - 1) / Math.max(1, n - 1);
    return Array.from({ length: n }, (_, i) => c.ordinal[Math.round(i * paso)]);
  }
  return Array.from({ length: n }, (_, i) => c.ordinal[Math.min(c.ordinal.length - 1, Math.floor((i * c.ordinal.length) / n))]);
}
