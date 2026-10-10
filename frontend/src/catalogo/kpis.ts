import { evaluar } from '../motor/agregar';
import { masc, type Ctx } from './comun';

export interface Kpi { id: string; etiqueta: string; valor: number | null; n: number; formato: 'n' | 'clp' | 'pct'; nota: string; aviso?: string }

export function kpis(x: Ctx): Kpi[] {
  const { a } = x, m = masc(x), filas: number[] = [];
  for (let i = 0; i < a.n; i++) if (m[i]) filas.push(i);
  const hoy = Math.max(...Array.from(a.fecha).filter((d) => d >= 0), 0);
  const nuevas7 = filas.filter((i) => a.fecha[i] >= hoy - 6).length;
  const sue = evaluar(a, 'sueldo_p50', filas), tr = evaluar(a, 'pct_con_sueldo', filas), en = evaluar(a, 'pct_encaje_alto', filas);
  return [
    { id: 'activas', etiqueta: 'Ofertas activas', valor: filas.length, n: filas.length, formato: 'n', nota: 'según los filtros actuales' },
    { id: 'nuevas', etiqueta: 'Nuevas en 7 días', valor: nuevas7, n: filas.length, formato: 'n', nota: 'vistas por primera vez' },
    { id: 'sueldo', etiqueta: 'Sueldo mediano', valor: sue.valor, n: sue.nBase, formato: 'clp', nota: `n=${sue.nBase} con sueldo`,
      aviso: sue.estado === 'chica' ? 'muestra chica' : sue.estado === 'insuficiente' ? `muestra insuficiente (n=${sue.nBase}, mín. 5)` : undefined },
    { id: 'transparencia', etiqueta: 'Declaran sueldo', valor: tr.valor, n: filas.length, formato: 'pct', nota: `${Math.round((tr.valor ?? 0) * filas.length)} de ${filas.length}` },
    { id: 'encaje', etiqueta: 'Encaje alto', valor: en.valor, n: en.nBase, formato: 'pct', nota: `de ${en.nBase} evaluadas por la IA`,
      aviso: en.estado === 'insuficiente' ? 'pocas evaluadas' : undefined },
  ];
}
