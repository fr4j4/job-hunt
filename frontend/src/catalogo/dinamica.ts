// Dinámica del mercado — "¿Se mueve rápido?" (V-02, V-16, V-60…V-63). Usa la historia acumulada del servidor.
import { clp, fechaCorta } from '../lib/formato';
import type { HistoriaDiaria, Supervivencia } from '../lib/tipos';
import { base } from '../viz/formas';
import { lineas } from '../viz/formas';
import { colorEntidad, FAMILIAS } from '../viz/tema';
import { tip } from '../viz/tip';
import { histograma } from '../viz/formas';
import { masc, vacio, type Ctx, type EspecGrafico } from './comun';

export const MIN_DIAS = 7;
const esperando = (id: string, titulo: string, dias: number, min = MIN_DIAS, span: EspecGrafico['span'] = 'c6'): EspecGrafico =>
  ({ ...vacio(id, titulo, `Acumulando historia: ${dias}/${min} días. Esta vista aparece cuando hay suficientes días de datos.`, span), estado: 'oculto' });

/** V-02 — ¿cuántas ofertas nuevas llegan cada día? (columnas + media de 7 días) */
export function v02(x: Ctx, h: HistoriaDiaria | null): EspecGrafico {
  const titulo = '¿Cuántas ofertas llegan cada día?';
  if (!h) return vacio('V-02', titulo, 'Cargando historia…');
  if (h.dias_historia < MIN_DIAS) return esperando('V-02', titulo, h.dias_historia);
  const s = h.series[0]; if (!s) return vacio('V-02', titulo, 'Sin datos.');
  const v = s.valores.map((n) => n ?? 0), c = x.c;
  const media = v.map((_, i) => (i < 6 ? null : v.slice(i - 6, i + 1).reduce((p, q) => p + q, 0) / 7));
  const opcion = { ...base(c), grid: { left: 8, right: 12, top: 12, bottom: 22, containLabel: true },
    xAxis: { type: 'category', data: h.fechas.map(fechaCorta), axisTick: { show: false }, axisLine: { lineStyle: { color: c.eje } }, axisLabel: { color: c.suave, hideOverlap: true } },
    yAxis: { type: 'value', axisLabel: { color: c.suave }, splitLine: { lineStyle: { color: c.rejilla } }, minInterval: 1 },
    tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.suave } }, formatter: (ps: any[]) => tip(h.fechas[ps[0].dataIndex], ps.map((p) => ({ color: p.seriesIndex ? c.texto : c.serie[0], etiqueta: p.seriesName, valor: p.value == null ? '—' : String(Math.round(p.value * 10) / 10) }))) },
    series: [{ type: 'bar', name: 'nuevas', data: v, barMaxWidth: 14, itemStyle: { color: c.serie[0], borderRadius: [4, 4, 0, 0] } },
      { type: 'line', name: 'media 7 días', data: media, showSymbol: false, lineStyle: { color: c.texto, width: 2 }, itemStyle: { color: c.texto } }] };
  return { id: 'V-02', titulo, span: 'c6', alto: 240, opcion, subtitulo: 'Ofertas nuevas por día · línea: media de 7 días', cobertura: `${h.dias_historia} días de historia`,
    tabla: { columnas: ['Día', 'Nuevas'], filas: h.fechas.map((f, i) => [f, v[i]]) } };
}

/** V-60 — ¿crece o se achica? ofertas activas por familia (áreas apiladas). */
export function v60(x: Ctx, h: HistoriaDiaria | null): EspecGrafico {
  const titulo = '¿Crece o se achica el mercado?';
  if (!h) return vacio('V-60', titulo, 'Cargando historia…');
  if (h.dias_historia < MIN_DIAS) return esperando('V-60', titulo, h.dias_historia);
  const orden = FAMILIAS.filter((f) => h.series.some((s) => s.clave === f));
  const series = orden.map((f) => ({ nombre: f, color: colorEntidad(x.c, 'rol_familia', f), valores: h.series.find((s) => s.clave === f)!.valores }));
  return { id: 'V-60', titulo, span: 'c6', alto: 260, subtitulo: 'Ofertas activas por familia de rol', cobertura: `${h.dias_historia} días de historia`, leyenda: series.map((s) => ({ nombre: s.nombre, color: s.color })),
    opcion: lineas(x.c, h.fechas, series, { fmt: (v) => String(Math.round(v)), apilar: true, etiquetaFecha: fechaCorta }),
    tabla: { columnas: ['Día', ...series.map((s) => s.nombre)], filas: h.fechas.map((f, i) => [f, ...series.map((s) => s.valores[i])]) } };
}

/** V-16 — ¿cómo evolucionó la mediana de sueldo? (con banda p25–p75; solo días con ≥5 sueldos) */
export function v16(x: Ctx, p50: HistoriaDiaria | null, p25: HistoriaDiaria | null, p75: HistoriaDiaria | null): EspecGrafico {
  const titulo = '¿Cómo evolucionó la mediana de sueldo?';
  if (!p50) return vacio('V-16', titulo, 'Cargando historia…');
  if (p50.dias_historia < 28) return esperando('V-16', titulo, p50.dias_historia, 28);
  const s = p50.series[0]; if (!s || s.valores.every((v) => v === null)) return vacio('V-16', titulo, 'Ningún día tiene al menos 5 sueldos para calcular una mediana.');
  return { id: 'V-16', titulo, span: 'c6', alto: 240, subtitulo: 'Mediana (línea) y p25–p75 (banda) del sueldo declarado · días con menos de 5 sueldos quedan en blanco',
    opcion: lineas(x.c, p50.fechas, [{ nombre: 'Mediana', color: x.c.serie[0], valores: s.valores, ns: s.n_con_sueldo }], { fmt: clp, etiquetaFecha: fechaCorta,
      banda: p25 && p75 ? { bajo: p25.series[0]?.valores ?? [], alto: p75.series[0]?.valores ?? [], color: x.c.serie[0] } : undefined }),
    tabla: { columnas: ['Día', 'Mediana', 'n con sueldo'], filas: p50.fechas.map((f, i) => [f, s.valores[i], s.n_con_sueldo[i]]) } };
}

/** V-62 — ¿entran más de las que salen? nuevas (arriba) y cerradas (abajo) en el mismo eje. */
export function v62(x: Ctx, nuevas: HistoriaDiaria | null, cerradas: HistoriaDiaria | null): EspecGrafico {
  const titulo = '¿Entran más ofertas de las que salen?';
  if (!nuevas || !cerradas) return vacio('V-62', titulo, 'Cargando historia…');
  if (nuevas.dias_historia < 28) return esperando('V-62', titulo, nuevas.dias_historia, 28);
  const n = nuevas.series[0]?.valores.map((v) => v ?? 0) ?? [], c = (cerradas.series[0]?.valores ?? []).map((v) => -(v ?? 0)), col = x.c;
  const opcion = { ...base(col), grid: { left: 8, right: 12, top: 12, bottom: 22, containLabel: true },
    xAxis: { type: 'category', data: nuevas.fechas.map(fechaCorta), axisTick: { show: false }, axisLine: { lineStyle: { color: col.eje } }, axisLabel: { color: col.suave, hideOverlap: true } },
    yAxis: { type: 'value', axisLabel: { color: col.suave, formatter: (v: number) => String(Math.abs(v)) }, splitLine: { lineStyle: { color: col.rejilla } } },
    tooltip: { ...base(col).tooltip, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps: any[]) => tip(nuevas.fechas[ps[0].dataIndex], [{ color: col.divergente[0], etiqueta: 'nuevas', valor: String(n[ps[0].dataIndex]) }, { color: col.divergente[5], etiqueta: 'cerradas (posibles)', valor: String(-c[ps[0].dataIndex]) }]) },
    series: [{ type: 'bar', stack: 'f', data: n, itemStyle: { color: col.divergente[0], borderRadius: [4, 4, 0, 0] }, barMaxWidth: 14 }, { type: 'bar', stack: 'f', data: c, itemStyle: { color: col.divergente[5], borderRadius: [0, 0, 4, 4] }, barMaxWidth: 14 }] };
  return { id: 'V-62', titulo, span: 'c6', alto: 240, opcion, subtitulo: 'Arriba: nuevas · abajo: posiblemente cerradas', leyenda: [{ nombre: 'nuevas', color: col.divergente[0] }, { nombre: 'cerradas', color: col.divergente[5] }],
    tabla: { columnas: ['Día', 'Nuevas', 'Cerradas'], filas: nuevas.fechas.map((f, i) => [f, n[i], -c[i]]) } };
}

/** V-61 — ¿cuánto dura una oferta? Curva de supervivencia (Kaplan-Meier, las abiertas son censuradas). */
export function v61(x: Ctx, s: Supervivencia | null): EspecGrafico {
  const titulo = '¿Cuánto dura una oferta publicada?';
  if (!s) return vacio('V-61', titulo, 'Cargando historia…');
  if (s.oculto) return { ...vacio('V-61', titulo, `Acumulando historia: ${s.n}/${s.minimo_n} ofertas y ${s.cierres}/${s.minimo_cierres} cierres observados.`), estado: 'oculto' };
  const c = x.c, t = s.t ?? [], sv = s.s ?? [];
  const datos: [number, number][] = [[0, 1], ...t.map((ti, i) => [ti, sv[i]] as [number, number])];
  const opcion = { ...base(c), grid: { left: 8, right: 16, top: 14, bottom: 30, containLabel: true },
    xAxis: { type: 'value', name: 'días desde que apareció', nameLocation: 'middle', nameGap: 22, nameTextStyle: { color: c.suave }, axisLabel: { color: c.suave }, splitLine: { lineStyle: { color: c.rejilla } } },
    yAxis: { type: 'value', min: 0, max: 1, axisLabel: { color: c.suave, formatter: (v: number) => `${Math.round(v * 100)} %` }, splitLine: { lineStyle: { color: c.rejilla } } },
    tooltip: { ...base(c).tooltip, trigger: 'axis', formatter: (ps: any[]) => tip(`${Math.round(ps[0].value[0])} días`, [{ color: c.serie[0], etiqueta: 'siguen abiertas', valor: `${Math.round(ps[0].value[1] * 100)} %` }]) },
    series: [{ type: 'line', step: 'end', data: datos, showSymbol: false, lineStyle: { color: c.serie[0], width: 2 }, itemStyle: { color: c.serie[0] },
      markLine: s.mediana != null ? { symbol: 'none', silent: true, lineStyle: { color: c.suave, type: 'solid' }, label: { formatter: `mediana ${Math.round(s.mediana)} d`, color: c.suave }, data: [{ xAxis: s.mediana }] } : undefined }] };
  return { id: 'V-61', titulo, span: 'c6', alto: 240, opcion, subtitulo: `Fracción de ofertas que siguen abiertas con el paso de los días (n=${s.n}, ${s.cierres} cierres observados)`,
    cobertura: 'Las ofertas aún abiertas cuentan como censuradas (no se subestima su duración)', tabla: { columnas: ['Días', 'Siguen abiertas'], filas: datos.map(([a, b]) => [Math.round(a * 10) / 10, +b.toFixed(3)]) } };
}

/** V-63 — ¿qué tan frescas son las activas? (siempre disponible: usa el snapshot) */
export function v63(x: Ctx): EspecGrafico {
  const titulo = '¿Qué tan frescas son las ofertas?', m = masc(x), { a } = x;
  const lim = [1, 3, 7, 14, 30, Infinity], et = ['0–1 d', '2–3 d', '4–7 d', '8–14 d', '15–30 d', '> 30 d'], cu = new Array(6).fill(0);
  let tot = 0; for (let i = 0; i < a.n; i++) if (m[i]) { tot++; cu[lim.findIndex((l) => a.antiguedad[i] <= l)]++; }
  if (!tot) return vacio('V-63', titulo, 'Sin ofertas en esta selección.');
  return { id: 'V-63', titulo, span: 'c6', alto: 220, subtitulo: 'Días desde la fecha de publicación (o desde que la vimos por primera vez)', cobertura: `${tot} ofertas`,
    opcion: histograma(x.c, et, cu, { color: x.c.serie[0] }), tabla: { columnas: ['Antigüedad', 'Ofertas'], filas: et.map((e, i) => [e, cu[i]]) } };
}
