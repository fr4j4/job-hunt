// Tipos de la API (jobhunt/web/api). Escritos a mano; se validan contra el snapshot real en los tests.
export interface Snapshot {
  v: number;
  n: number;
  truncado: boolean;
  dicts: Record<'empresa' | 'fuente' | 'rol' | 'tech' | 'tag' | 'region' | 'comuna', string[]>;
  cols: {
    id: number[]; titulo: string[]; url: string[];
    empresa: number[]; empresa_canon: string[]; fuente: number[]; fuentes: number[][]; rol: number[];
    rol_familia: string[]; seniority: string[]; modalidad: string[]; modalidad_src: string[];
    empleo: string[]; region: number[]; comuna: number[];
    sueldo: (number | null)[]; sueldo_min: (number | null)[]; sueldo_max: (number | null)[]; sueldo_valido: number[];
    techs: (number[] | null)[]; beneficios: number[][]; alertas: number[][]; a_favor: number[][];
    score: number[]; market_score: number[]; encaje: string[]; ingles: string[];
    first_seen: string[]; fecha_pub: string[]; antiguedad: number[];
    applicants: (number | null)[]; exp_anios: (number | null)[]; staffing: number[]; n_fuentes: number[];
    active: number[]; posible_cerrada: number[]; resumen: string[]; estado: string[];
  };
}

export interface Perfil {
  techs: string[]; roles: string[]; salary_min: number; salary_max: number; years_exp: number;
  min_fit: number; titulo: string;
}

export interface Dimension {
  etiqueta: string; tipo: 'nominal' | 'ordinal' | 'temporal' | 'cuantitativa';
  orden?: string[]; multivalor?: boolean; top?: number; drill?: string;
}
export interface MetricaDef { etiqueta: string; unidad: string; min_n: number; min_n_aviso?: number; denominador: string }
export interface Semantica {
  v: number; dimensiones: Record<string, Dimension>; metricas: Record<string, MetricaDef>;
  drill: Record<string, string | null>; historia_min: Record<string, number>; estados: string[];
}

export interface Evento { ts: string; tipo: string; antes: string; despues: string }
export interface DetalleOferta {
  id: number; ref: string; titulo: string; empresa: string; ubicacion: string; url: string; fuente: string; fuentes: string[];
  modalidad: string; modalidad_src: string; rol: string; rol_familia: string; seniority: string;
  sueldo_texto: string; sueldo: number | null; sueldo_status: string; techs: string[];
  idiomas: { idioma: string; nivel?: string; excluyente?: boolean }[]; exp_anios: number | null;
  first_seen: string; last_seen: string; actualizada: string; active: number;
  score_guardado: number; score: number; desglose: { clave: string; etiqueta: string; valor: number | string }[];
  market_score: number; mdesglose: { clave: string; etiqueta: string; valor: number | string }[];
  encaje: string; pasa_fit: boolean; es_dev: boolean; min_fit: number;
  resumen: string; opinion: string; fit_reason: string; alertas: string[]; a_favor: string[]; beneficios: string[];
  descripcion: string; eventos: (Evento & { })[]; estado: { estado: string; nota: string; actualizado: string } | null;
}

export interface FuentesResp {
  barridos: string[];
  fuentes: { fuente: string; nombre: string; racha_ceros: number; estado: 'ok' | 'vigilar' | 'caida';
             serie: ({ n: number; err: number } | null)[] }[];
  cobertura: Record<string, number | string>[];
  analytics: { ts: string; filas: number; derivadas: number; ms: number; norm: string } | null;
}
export interface HistoriaDiaria {
  fechas: string[]; dias_historia: number; metrica: string; por: string;
  series: { clave: string; valores: (number | null)[]; n_con_sueldo: (number | null)[] }[];
}
export interface HistoriaTechs {
  semanas: string[]; semanas_historia: number;
  series: { tech: string; demanda: (number | null)[]; n_activas: (number | null)[]; n_base: (number | null)[] }[];
}
export interface Supervivencia {
  oculto: boolean; n: number; cierres: number; mediana?: number | null; t?: number[]; s?: number[];
  minimo_n?: number; minimo_cierres?: number;
}
export interface EventoHist { ts: string; oferta_id: number; title: string; company: string; rol_familia: string;
                              fuente: string; tipo: string; antes: string; despues: string }
export interface Vista { id: number; nombre: string; tipo: string; spec: Record<string, unknown>; creada: string }
