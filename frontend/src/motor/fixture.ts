// Snapshot sintético para pruebas del motor (misma forma que /api/snapshot).
import type { Snapshot } from '../lib/tipos';

export interface FilaFix {
  id: string; titulo?: string; empresa?: string; fuente?: string; rol?: string; fam?: string; sen?: string; mod?: string;
  sueldo?: number | null; techs?: string[] | null; score?: number; enc?: string; ant?: number; dia?: string; nf?: number;
}
export function fixture(filas: FilaFix[]): Snapshot {
  const dicts = { empresa: [] as string[], fuente: [] as string[], rol: [] as string[], tech: [] as string[], tag: [] as string[],
                  region: [''] as string[], comuna: [''] as string[] };
  const ix = (k: keyof typeof dicts, v: string) => { let i = dicts[k].indexOf(v); if (i < 0) { i = dicts[k].length; dicts[k].push(v); } return i; };
  const c: any = {};
  const push = (k: string, v: unknown) => (c[k] ??= []).push(v);
  filas.forEach((f, n) => {
    push('id', n + 1); push('titulo', f.titulo ?? f.id); push('url', '');
    push('empresa', ix('empresa', f.empresa ?? '')); push('empresa_canon', (f.empresa ?? '').toLowerCase());
    push('fuente', ix('fuente', f.fuente ?? 'laborum')); push('fuentes', [ix('fuente', f.fuente ?? 'laborum')]);
    push('rol', ix('rol', f.rol ?? '')); push('rol_familia', f.fam ?? 'Desarrollo'); push('seniority', f.sen ?? '');
    push('modalidad', f.mod ?? ''); push('modalidad_src', f.mod ? 'oficial' : ''); push('empleo', ''); push('region', 0); push('comuna', 0);
    push('sueldo', f.sueldo ?? null); push('sueldo_min', f.sueldo ?? null); push('sueldo_max', f.sueldo ?? null);
    push('sueldo_valido', f.sueldo ? 1 : 0);
    push('techs', f.techs === undefined || f.techs === null ? null : f.techs.map((t) => ix('tech', t)));
    push('beneficios', []); push('alertas', []); push('a_favor', []);
    push('score', f.score ?? 50); push('market_score', 50); push('encaje', f.enc ?? ''); push('ingles', '');
    push('first_seen', f.dia ?? '2026-10-09'); push('fecha_pub', ''); push('antiguedad', f.ant ?? 1);
    push('applicants', null); push('exp_anios', null); push('staffing', 0); push('n_fuentes', f.nf ?? 1);
    push('active', 1); push('posible_cerrada', 0); push('resumen', ''); push('estado', '');
  });
  return { v: 2, n: filas.length, truncado: false, dicts, cols: c };
}
