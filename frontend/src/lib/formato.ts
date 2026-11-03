// Formato chileno: $1,8 M · $850 mil · 12,5 % · fechas es-CL.
const nf = (d: number) => new Intl.NumberFormat('es-CL', { maximumFractionDigits: d, minimumFractionDigits: 0 });

export function clp(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1_000_000) return `$${nf(1).format(v / 1_000_000)} M`;
  if (Math.abs(v) >= 1_000) return `$${nf(0).format(v / 1_000)} mil`;
  return `$${nf(0).format(v)}`;
}
export const clpCompleto = (v: number | null | undefined) => (v == null ? '—' : `$${nf(0).format(v)}`);
export const pct = (v: number | null | undefined, d = 0) => (v == null || Number.isNaN(v) ? '—' : `${nf(d).format(v * 100)} %`);
export const num = (v: number | null | undefined, d = 0) => (v == null || Number.isNaN(v) ? '—' : nf(d).format(v));

export function fechaCorta(iso: string): string {
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? iso : new Intl.DateTimeFormat('es-CL', { day: '2-digit', month: 'short' }).format(d);
}
export function edad(dias: number): string {
  if (dias <= 0) return 'hoy';
  if (dias === 1) return 'ayer';
  if (dias < 30) return `hace ${dias} d`;
  return `hace ${Math.round(dias / 30)} m`;
}
export const ETIQUETA_VALOR: Record<string, string> = {
  hibrido: 'Híbrido', remoto: 'Remoto', presencial: 'Presencial', trainee: 'Trainee', junior: 'Junior',
  semi: 'Semi senior', senior: 'Senior', lead: 'Lead', completa: 'Jornada completa', parcial: 'Jornada parcial',
  contrato: 'Contrato', practica: 'Práctica', otro: 'Otro', alto: 'Alto', medio: 'Medio', bajo: 'Bajo',
  ninguno: 'Ninguno', no: 'No requerido', deseable: 'Deseable', requerido: 'Requerido', guardada: 'Guardada',
  postulada: 'Postulada', entrevista: 'Entrevista', oferta: 'Oferta', descartada: 'Descartada',
};
export const etiquetaValor = (v: string) => (v === '' ? 'Sin dato' : (ETIQUETA_VALOR[v] ?? v));
