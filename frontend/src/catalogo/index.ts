import type { HistoriaDiaria, HistoriaTechs, Supervivencia } from '../lib/tipos';
import { v02, v16, v24, v60, v61, v62, v63 } from './dinamica';
import { v40, v41, v42 } from './empresas';
import { v72, v73, v80, v81 } from './posicion';
import { apilado100, barrasDim, rolSeniority } from './roles';
import { sueldosPor, transparencia, v10, v15 } from './sueldos';
import { v20, v21, v22, v23, v25 } from './tecnologias';
import type { Ctx, EspecGrafico } from './comun';
import { pct } from '../lib/formato';

export const PESTANAS = [['resumen', 'Resumen'], ['sueldos', 'Sueldos'], ['tecnologias', 'Tecnologías'], ['roles', 'Roles'], ['empresas', 'Empresas'],
  ['condiciones', 'Condiciones'], ['dinamica', 'Dinámica'], ['calidad', 'Calidad de datos'], ['posicion', 'Tu posición']] as const;
export type Pestana = (typeof PESTANAS)[number][0];

export interface Historia { diaria: HistoriaDiaria | null; p25: HistoriaDiaria | null; p50: HistoriaDiaria | null; p75: HistoriaDiaria | null;
                            cerradas: HistoriaDiaria | null; porFamilia: HistoriaDiaria | null; surv: Supervivencia | null; techs: HistoriaTechs | null }
export const historiaVacia = (): Historia => ({ diaria: null, p25: null, p50: null, p75: null, cerradas: null, porFamilia: null, surv: null, techs: null });

const cond = (x: Ctx) => [
  apilado100(x, 'V-50', '¿Cuánta oferta es remota?', 'rol_familia', 'modalidad', { subtitulo: 'Modalidad por familia de rol · gris: la oferta no la informa', nota: 'Incluye modalidad inferida del texto cuando la fuente no la trae' }),
  apilado100(x, 'V-51', '¿Cuánto pesa el inglés?', 'rol_familia', 'ingles', { ordinal: true, subtitulo: 'Exigencia de inglés por familia · gris: desconocido (la mayoría de las ofertas no lo aclara)' }),
  barrasDim(x, 'V-52', '¿Qué beneficios se ofrecen?', 'beneficio', { top: 15, color: x.c.serie[0], subtitulo: 'Solo ofertas donde la IA extrajo beneficios (una oferta puede tener varios)' }),
  barrasDim(x, 'V-53', '⚠ ¿Qué alertas son comunes?', 'alerta', { top: 15, color: x.c.estado.serio, subtitulo: 'Cosas a considerar detectadas por la IA (riesgos, no identidad)' }),
  barrasDim(x, 'V-54', '¿Cómo son las jornadas?', 'empleo', { color: x.c.serie[0] }),
  barrasDim(x, 'V-55', '¿Dónde están?', 'region', { top: 12, subtitulo: 'Región normalizada a partir del texto de ubicación; "remoto" separado', color: x.c.serie[0] }),
];

export function especs(tab: Pestana, x: Ctx, h: Historia): EspecGrafico[] {
  switch (tab) {
    case 'resumen': return [
      { ...sueldosPor(x, 'rol_familia', 'V-11', '¿Cuánto paga cada familia de rol?'), span: 'c6' }, v20(x, 15), cond(x)[0], v63(x), v80(x)];
    case 'sueldos': return [v10(x), sueldosPor(x, 'rol_familia', 'V-11', '¿Cuánto paga cada familia de rol?'), sueldosPor(x, 'seniority', 'V-12', '¿Cuánto sube con la seniority?'),
      sueldosPor(x, 'modalidad', 'V-13', '¿Remoto paga distinto?'), transparencia(x, 'fuente', 'V-14', '¿Qué fuente publica el sueldo?'),
      transparencia(x, 'rol_familia', 'V-14b', '¿Qué familias publican el sueldo?'), v15(x)];
    case 'tecnologias': return [v20(x), v21(x), v22(x), v23(x), v25(x), v24(x, h.techs)];
    case 'roles': return [rolSeniority(x, 'ofertas'), rolSeniority(x, 'sueldo_p50'),
      apilado100(x, 'V-32', '¿Qué fuente trae qué tipo de rol?', 'fuente', 'rol_familia', { subtitulo: 'Familias de rol que publica cada fuente (100 %)' }),
      apilado100(x, 'V-33', '¿Qué tan bien encajan, por familia?', 'rol_familia', 'encaje', { ordinal: true, subtitulo: 'Veredicto de encaje de la IA (100 %) · gris: sin evaluar' })];
    case 'empresas': return [v40(x), v42(x), v41(x)];
    case 'condiciones': return cond(x);
    case 'dinamica': return [v02(x, h.diaria), v60(x, h.porFamilia), v62(x, h.diaria, h.cerradas), v16(x, h.p50, h.p25, h.p75), v61(x, h.surv), v63(x)];
    case 'calidad': return [v72(x), v73(x)];
    case 'posicion': return [v80(x), v81(x), v15(x)];
  }
}
void pct;
