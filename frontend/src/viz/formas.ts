// Constructores de opciones ECharts. Una función por forma; los gráficos del catálogo solo eligen datos.
// Reglas (spec §7.5): marcas finas, extremo redondeado 4 px, hueco de 2 px entre rellenos, rejilla sólida
// recesiva, nunca doble eje, rótulos selectivos, texto en tokens de texto (no en el color de la serie).
import type { EChartsOption } from './echarts';
import type { EstadoMuestra } from '../motor/agregar';
import type { Colores } from './tema';
import { tip, type FilaTip } from './tip';

const FUENTE = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

export function base(c: Colores): EChartsOption {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: c.suave, fontFamily: FUENTE, fontSize: 12 },
    aria: { enabled: true },
    tooltip: { confine: true, backgroundColor: c.panel, borderColor: c.eje, borderWidth: 1, padding: 8,
               textStyle: { color: c.texto, fontSize: 12 }, extraCssText: 'box-shadow:0 6px 24px rgba(0,0,0,.18);' },
  };
}
const ejeValor = (c: Colores, fmt: (v: number) => string, extra: Record<string, unknown> = {}) => ({
  type: 'value', axisLine: { show: false }, axisTick: { show: false },
  splitLine: { lineStyle: { color: c.rejilla, width: 1, type: 'solid' } },
  axisLabel: { color: c.suave, formatter: (v: number) => fmt(v), hideOverlap: true }, ...extra,
});
const ejeCat = (c: Colores, data: string[], extra: Record<string, unknown> = {}) => ({
  type: 'category', data, axisTick: { show: false }, axisLine: { lineStyle: { color: c.eje } },
  axisLabel: { color: c.suave, interval: 0, hideOverlap: false }, ...extra,
});
const recortar = (s: string, n = 26) => (s.length > n ? s.slice(0, n - 1) + '…' : s);

export interface ItemBarra {
  nombre: string; valor: number | null; n: number; nBase?: number; estado: EstadoMuestra; color: string;
  clave?: string; detalle?: FilaTip[]; ic?: [number, number] | null; filas?: number[];
}

/** Barras horizontales ordenadas, con énfasis por color, "muestra chica" con trama y "insuficiente" explícito. */
export function barrasH(c: Colores, items: ItemBarra[], o: { fmt: (v: number) => string; etiquetaValor: string;
                         max?: number; rotulos?: number; unidadN?: string; referencia?: { valor: number; texto: string } }): EChartsOption {
  const nombres = items.map((i) => recortar(i.nombre));
  const rot = o.rotulos ?? (items.length <= 12 ? items.length : 5);
  return {
    ...base(c),
    grid: { left: 8, right: 56, top: 8, bottom: 22, containLabel: true },
    xAxis: ejeValor(c, o.fmt, { max: o.max }),
    yAxis: ejeCat(c, nombres, { inverse: true, axisLine: { show: false }, axisLabel: { color: c.texto, interval: 0, width: 150, overflow: 'truncate' } }),
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (p: any) => {
      const it = items[p.dataIndex];
      const filas: FilaTip[] = [{ color: it.color, etiqueta: o.etiquetaValor, valor: it.valor === null ? '—' : o.fmt(it.valor) }];
      if (it.ic) filas.push({ etiqueta: 'IC 90 % de la mediana', valor: `${o.fmt(it.ic[0])} – ${o.fmt(it.ic[1])}` });
      filas.push({ etiqueta: o.unidadN ?? 'ofertas', valor: String(it.nBase ?? it.n) });
      filas.push(...(it.detalle ?? []));
      return tip(it.nombre, filas, it.estado === 'chica' ? 'Muestra chica: tómalo como referencia' :
        it.estado === 'insuficiente' ? 'Muestra insuficiente para resumir' : '');
    } },
    series: [{
      type: 'bar', barMaxWidth: 16, data: items.map((it, i) => ({
        value: it.valor ?? 0, meta: it,
        itemStyle: { color: it.color, borderRadius: [0, 4, 4, 0], opacity: it.estado === 'chica' ? 0.62 : 1,
                     decal: it.estado === 'chica' ? { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [2, 4], rotation: Math.PI / 4, color: 'rgba(255,255,255,.55)' } : undefined },
        label: it.valor === null
          ? { show: true, position: 'right', color: c.suave, formatter: `n=${it.nBase ?? it.n} · insuficiente` }
          : { show: i < rot, position: 'right', color: c.texto, formatter: () => o.fmt(it.valor as number) },
      })),
      markLine: o.referencia ? { symbol: 'none', silent: true, lineStyle: { color: c.suave, type: 'solid', width: 1 },
        label: { formatter: o.referencia.texto, color: c.suave, position: 'insideEndTop' }, data: [{ xAxis: o.referencia.valor }] } : undefined,
    }],
  };
}

export interface SerieApilada { nombre: string; color: string; valores: (number | null)[]; ns: number[] }
/** Barras horizontales 100 % apiladas (parte-del-todo por categoría). */
export function barras100(c: Colores, categorias: string[], series: SerieApilada[], o: { totales: number[]; fmtPct?: (v: number) => string }): EChartsOption {
  const f = o.fmtPct ?? ((v: number) => `${Math.round(v * 100)} %`);
  return {
    ...base(c),
    grid: { left: 8, right: 16, top: 8, bottom: 22, containLabel: true },
    xAxis: ejeValor(c, (v) => `${Math.round(v * 100)} %`, { max: 1, min: 0, interval: 0.25 }),
    yAxis: ejeCat(c, categorias.map((x) => recortar(x)), { inverse: true, axisLine: { show: false }, axisLabel: { color: c.texto, interval: 0, width: 140, overflow: 'truncate' } }),
    tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'none' }, formatter: (ps: any[]) => {
      const i = ps[0].dataIndex;
      return tip(categorias[i], series.map((s) => ({ color: s.color, etiqueta: `${s.nombre} (n=${s.ns[i]})`, valor: s.valores[i] === null ? '—' : f(s.valores[i] as number) })),
        `${o.totales[i]} ofertas`);
    } },
    series: series.map((s, k) => ({
      type: 'bar', stack: 't', name: s.nombre, barMaxWidth: 18, data: s.valores.map((v) => v ?? 0),
      itemStyle: { color: s.color, borderColor: c.panel, borderWidth: 1,
                   borderRadius: k === series.length - 1 ? [0, 4, 4, 0] : 0 },
      emphasis: { focus: 'series' },
    })),
  };
}

export interface CeldaHM { xi: number; yi: number; valor: number | null; n: number; estado: EstadoMuestra; extra?: string }
/** Heatmap secuencial/divergente. Celdas sin datos suficientes: trama, nunca coloreadas como cero. */
export function heatmap(c: Colores, xs: string[], ys: string[], celdas: CeldaHM[], o: { fmt: (v: number) => string;
                        etiquetaValor: string; rampa: string[]; centro?: number; min?: number; max?: number; valorEnCelda?: boolean;
                        rotX?: number }): EChartsOption {
  const conDato = celdas.filter((x) => x.valor !== null && x.estado !== 'insuficiente');
  const sin = celdas.filter((x) => x.valor === null || x.estado === 'insuficiente');
  const vals = conDato.map((x) => x.valor as number);
  const mn = o.min ?? (vals.length ? Math.min(...vals) : 0), mx = o.max ?? (vals.length ? Math.max(...vals) : 1);
  const trama = { color: c.panel, borderColor: c.rejilla, borderWidth: 1,
                  decal: { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [2, 3], rotation: Math.PI / 4, color: c.eje } };
  return {
    ...base(c),
    grid: { left: 8, right: 16, top: 8, bottom: 56, containLabel: true },
    xAxis: { ...ejeCat(c, xs.map((x) => recortar(x, 16)), { splitArea: { show: false }, position: 'bottom' }), axisLabel: { color: c.texto, interval: 0, rotate: o.rotX ?? 0 } },
    yAxis: ejeCat(c, ys.map((y) => recortar(y, 22)), { inverse: true, axisLine: { show: false }, axisLabel: { color: c.texto, interval: 0 } }),
    visualMap: { type: 'continuous', min: mn, max: mx, calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
                 itemWidth: 10, itemHeight: 120, textStyle: { color: c.suave }, formatter: (v: number) => o.fmt(v),
                 inRange: { color: o.rampa }, seriesIndex: 0 },
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (p: any) => {
      const d = p.data.meta as CeldaHM;
      return tip(`${xs[d.xi]} · ${ys[d.yi]}`, [{ etiqueta: o.etiquetaValor, valor: d.valor === null ? '—' : o.fmt(d.valor) },
        { etiqueta: 'ofertas', valor: String(d.n) }, ...(d.extra ? [{ etiqueta: d.extra, valor: '' }] : [])],
        d.estado === 'insuficiente' ? 'Muestra insuficiente (sin datos suficientes, no es cero)' : d.estado === 'chica' ? 'Muestra chica' : '');
    } },
    series: [
      { type: 'heatmap', data: conDato.map((x) => ({ value: [x.xi, x.yi, x.valor], meta: x,
          // texto legible sobre cualquier celda: en la rampa clara→oscura (tema claro) las celdas altas son oscuras
          label: { color: ((((x.valor as number) - mn) / ((mx - mn) || 1)) > 0.5) === !c.oscuro ? '#ffffff' : '#0b0b0b' } })),
        itemStyle: { borderColor: c.panel, borderWidth: 2, borderRadius: 3 },
        label: { show: o.valorEnCelda ?? false, color: c.texto, formatter: (p: any) => o.fmt(p.value[2]), fontSize: 11 } },
      { type: 'heatmap', data: sin.map((x) => ({ value: [x.xi, x.yi, 0], meta: x })), silent: false,
        itemStyle: { ...trama, borderRadius: 3 }, tooltip: { show: true } },
    ],
  };
}

/** Histograma de barras finas con bandas/líneas de referencia (tu rango, p25/p50/p75). */
export function histograma(c: Colores, etiquetas: string[], conteos: number[], o: { color: string; bandas?: { desde: number; hasta: number; texto: string }[];
                           lineas?: { idx: number; texto: string; rotulo?: boolean }[]; fmtN?: (v: number) => string; etiquetaN?: string; tituloEjeX?: string }): EChartsOption {
  return {
    ...base(c),
    grid: { left: 8, right: 12, top: 18, bottom: 22, containLabel: true },
    xAxis: ejeCat(c, etiquetas, { axisLabel: { color: c.suave, interval: Math.max(0, Math.ceil(etiquetas.length / 8) - 1), hideOverlap: true } }),
    yAxis: ejeValor(c, (v) => String(v), { minInterval: 1 }),
    tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps: any[]) =>
      tip(etiquetas[ps[0].dataIndex], [{ color: o.color, etiqueta: o.etiquetaN ?? 'ofertas', valor: String(conteos[ps[0].dataIndex]) }]) },
    series: [{
      type: 'bar', data: conteos, barCategoryGap: '12%', itemStyle: { color: o.color, borderRadius: [4, 4, 0, 0] },
      markArea: o.bandas?.length ? { silent: true, itemStyle: { color: c.acento, opacity: 0.12 },
        label: { color: c.suave, position: 'insideTop' }, data: o.bandas.map((b) => [{ xAxis: b.desde, name: b.texto }, { xAxis: b.hasta }]) } : undefined,
      markLine: o.lineas?.length ? { symbol: 'none', silent: true, lineStyle: { color: c.suave, width: 1, type: 'solid' },
        label: { color: c.suave, formatter: (p: any) => p.name }, data: o.lineas.map((l) => ({ xAxis: l.idx, name: l.texto, label: { show: l.rotulo !== false } })) } : undefined,
    }],
  };
}

export interface PuntoOferta { x: number; y: number | null; id: number; titulo: string; empresa: string; color: string; extra?: FilaTip[] }
/** Franja de puntos (un punto por oferta) + caja p25–p75 + mediana + IC, por categoría. */
export function stripCaja(c: Colores, cats: { nombre: string; color: string; p25?: number | null; p50?: number | null; p75?: number | null;
                          ic?: [number, number] | null; n: number }[], puntos: { cat: number; valor: number; id: number; titulo: string; empresa: string }[],
                          o: { fmt: (v: number) => string; banda?: { desde: number; hasta: number; texto: string } }): EChartsOption {
  const jit = (id: number) => { const s = String(id); let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return ((h % 1000) / 1000 - 0.5) * 0.46; };
  return {
    ...base(c),
    grid: { left: 8, right: 16, top: 8, bottom: 24, containLabel: true },
    xAxis: ejeValor(c, o.fmt, { scale: true }),
    yAxis: ejeCat(c, cats.map((k) => `${recortar(k.nombre, 22)} (n=${k.n})`), { inverse: true, axisLine: { show: false },
      axisLabel: { color: c.texto, interval: 0, width: 170, overflow: 'truncate' }, splitLine: { show: true, lineStyle: { color: c.rejilla } } }),
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (p: any) => {
      const d = p.data.meta;
      if (d?.tipo === 'punto') return tip(d.titulo, [{ color: cats[d.cat].color, etiqueta: 'sueldo', valor: o.fmt(d.valor) }, { etiqueta: 'empresa', valor: d.empresa || '—' }], 'Clic para abrir la oferta');
      if (d?.tipo === 'caja') { const k = cats[d.cat]; return tip(k.nombre, [{ etiqueta: 'mediana', valor: k.p50 == null ? '—' : o.fmt(k.p50) }, { etiqueta: 'p25 – p75', valor: k.p25 == null ? '—' : `${o.fmt(k.p25)} – ${o.fmt(k.p75 as number)}` }, { etiqueta: 'IC 90 % mediana', valor: k.ic ? `${o.fmt(k.ic[0])} – ${o.fmt(k.ic[1])}` : '—' }, { etiqueta: 'n', valor: String(k.n) }]); }
      return '';
    } },
    series: [
      { type: 'custom', silent: false, z: 1,
        data: cats.map((k, i) => ({ value: [i, k.p25, k.p50, k.p75, k.ic?.[0] ?? null, k.ic?.[1] ?? null], meta: { tipo: 'caja', cat: i } })),
        renderItem: (_p: any, api: any) => {
          const i = api.value(0), p25 = api.value(1), p50 = api.value(2), p75 = api.value(3), lo = api.value(4), hi = api.value(5);
          if (p25 == null || Number.isNaN(p25)) return { type: 'group', children: [] };
          const a = api.coord([p25, i]), b = api.coord([p75, i]), m = api.coord([p50, i]);
          const ch: any[] = [
            { type: 'rect', shape: { x: a[0], y: a[1] - 9, width: Math.max(1, b[0] - a[0]), height: 18, r: 3 }, style: { fill: c.neutro, opacity: 0.2, stroke: c.neutro, lineWidth: 1 } },
            { type: 'line', shape: { x1: m[0], y1: m[1] - 11, x2: m[0], y2: m[1] + 11 }, style: { stroke: c.texto, lineWidth: 2 } },
          ];
          if (lo != null && hi != null && !Number.isNaN(lo)) {
            const l = api.coord([lo, i]), h = api.coord([hi, i]);
            ch.push({ type: 'line', shape: { x1: l[0], y1: l[1] + 14, x2: h[0], y2: h[1] + 14 }, style: { stroke: c.suave, lineWidth: 2 } });
          }
          return { type: 'group', children: ch };
        } },
      { type: 'scatter', symbolSize: 8, z: 3, data: puntos.map((pt) => ({ value: [pt.valor, pt.cat + jit(pt.id)], meta: { tipo: 'punto', ...pt },
          itemStyle: { color: cats[pt.cat].color, borderColor: c.panel, borderWidth: 2 } })),
        markArea: o.banda ? { silent: true, itemStyle: { color: c.acento, opacity: 0.1 }, label: { color: c.suave, position: 'insideTop' },
          data: [[{ xAxis: o.banda.desde, name: o.banda.texto }, { xAxis: o.banda.hasta }]] } : undefined },
    ],
  };
}

/** Dispersión con color ordinal y carril aparte para "sin dato en Y" (no se descartan ni se inventan). */
export function dispersion(c: Colores, puntos: { x: number; y: number | null; id: number; titulo: string; empresa: string; cat: number }[],
                           cats: { nombre: string; color: string }[], o: { fmtX: (v: number) => string; fmtY: (v: number) => string; carril?: string;
                           refX?: { valor: number; texto: string }; refY?: { valor: number; texto: string }; yMin: number; yMax: number; ejeX: string; ejeY: string }): EChartsOption {
  const rango = o.yMax - o.yMin, carril = o.yMin - rango * 0.12;
  const por = (k: number) => puntos.filter((p) => p.cat === k).map((p) => ({ value: [p.x, p.y ?? carril], meta: p,
    itemStyle: { color: cats[k].color, borderColor: c.panel, borderWidth: 2, opacity: p.y === null ? 0.7 : 1 } }));
  return {
    ...base(c),
    grid: { left: 8, right: 20, top: 16, bottom: 36, containLabel: true },
    xAxis: ejeValor(c, o.fmtX, { min: 0, max: 100, name: o.ejeX, nameLocation: 'middle', nameGap: 24, nameTextStyle: { color: c.suave } }),
    yAxis: ejeValor(c, (v) => (v < o.yMin ? '' : o.fmtY(v)), { min: carril - rango * 0.04, max: o.yMax + rango * 0.05, name: o.ejeY, nameTextStyle: { color: c.suave, align: 'left' } }),
    legend: { show: false },
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (p: any) => {
      const d = p.data.meta;
      return tip(d.titulo, [{ color: cats[d.cat].color, etiqueta: 'puntaje', valor: String(d.x) },
        { etiqueta: 'sueldo', valor: d.y === null ? 'no declarado' : o.fmtY(d.y) }, { etiqueta: 'empresa', valor: d.empresa || '—' }], 'Clic para abrir la oferta');
    } },
    series: cats.map((k, i) => ({
      type: 'scatter', name: k.nombre, symbolSize: 9, z: 3 + i, data: por(i), large: false,
      markLine: i === 0 ? { symbol: 'none', silent: true, lineStyle: { color: c.suave, width: 1, type: 'solid' }, label: { color: c.suave },
        data: [...(o.refX ? [{ xAxis: o.refX.valor, label: { formatter: o.refX.texto, position: 'insideEndTop' } }] : []),
               ...(o.refY ? [{ yAxis: o.refY.valor, label: { formatter: o.refY.texto, position: 'insideEndTop' } }] : [])] } : undefined,
      markArea: i === 0 && o.carril ? { silent: true, itemStyle: { color: c.neutro, opacity: 0.1 }, label: { color: c.suave, position: 'insideLeft' },
        data: [[{ yAxis: carril - rango * 0.04, name: o.carril }, { yAxis: o.yMin - rango * 0.04 }]] } : undefined,
    })),
  };
}

export interface SerieLinea { nombre: string; color: string; valores: (number | null)[]; ns?: (number | null)[]; enfasis?: boolean; area?: boolean }
/** Líneas/áreas temporales. Los huecos se CORTAN (no se interpolan). */
export function lineas(c: Colores, fechas: string[], series: SerieLinea[], o: { fmt: (v: number) => string; apilar?: boolean; etiquetaFecha?: (f: string) => string;
                       banda?: { bajo: (number | null)[]; alto: (number | null)[]; color: string }; etiquetaN?: string }): EChartsOption {
  const ef = o.etiquetaFecha ?? ((f: string) => f.slice(5));
  return {
    ...base(c),
    grid: { left: 8, right: series.length <= 4 ? 80 : 16, top: 12, bottom: 22, containLabel: true },
    xAxis: ejeCat(c, fechas.map(ef), { boundaryGap: false, axisLabel: { color: c.suave, hideOverlap: true } }),
    yAxis: ejeValor(c, o.fmt),
    tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: c.suave, width: 1 } }, formatter: (ps: any[]) =>
      tip(fechas[ps[0].dataIndex], ps.map((p) => ({ color: series[p.seriesIndex]?.color, etiqueta: p.seriesName + (series[p.seriesIndex]?.ns?.[p.dataIndex] != null ? ` (n=${series[p.seriesIndex].ns![p.dataIndex]})` : ''),
        valor: p.value == null || Number.isNaN(p.value) ? '—' : o.fmt(Number(p.value)) }))) },
    series: [
      ...(o.banda ? [{ type: 'line', name: '_b1', data: o.banda.bajo, stack: 'banda', lineStyle: { opacity: 0 }, symbol: 'none', silent: true, tooltip: { show: false } },
                     { type: 'line', name: '_b2', data: o.banda.alto.map((v, i) => (v == null || o.banda!.bajo[i] == null ? null : v - (o.banda!.bajo[i] as number))), stack: 'banda',
                       lineStyle: { opacity: 0 }, areaStyle: { color: o.banda.color, opacity: 0.18 }, symbol: 'none', silent: true, tooltip: { show: false } }] : []),
      ...series.map((s) => ({
        type: 'line', name: s.nombre, data: s.valores, connectNulls: false, showSymbol: s.valores.filter((v) => v !== null).length < 15,
        symbol: 'circle', symbolSize: 7, lineStyle: { color: s.color, width: 2 }, itemStyle: { color: s.color, borderColor: c.panel, borderWidth: 2 },
        stack: o.apilar ? 't' : undefined, areaStyle: o.apilar || s.area ? { color: s.color, opacity: o.apilar ? 0.85 : 0.15 } : undefined,
        emphasis: { focus: 'series' },
        endLabel: series.length <= 4 ? { show: true, color: c.texto, formatter: s.nombre, distance: 6 } : undefined,
      })),
    ] as any,
  };
}
