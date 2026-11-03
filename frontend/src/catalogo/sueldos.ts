// Sueldos — "¿Cuánto se paga y dónde quedo yo?" (V-10…V-15)
import { clp, etiquetaValor, pct } from '../lib/formato';
import { agregar, agrupar } from '../motor/agregar';
import { getDim } from '../motor/almacen';
import { percentil } from '../motor/estadistica';
import { barrasH, base, histograma, stripCaja } from '../viz/formas';
import { colorEntidad, ORDEN_NATURAL, rampaOrdinal } from '../viz/tema';
import { avisoChica, familiaPerfil, masc, textoCobertura, vacio, type Ctx, type EspecGrafico } from './comun';

const PASO = 250_000;

/** V-10 — distribución de sueldos (histograma; con n<30 se muestran las ofertas una a una). */
export function v10(x: Ctx): EspecGrafico {
  const m = masc(x, 'sueldo'), { a } = x;
  const v: number[] = []; for (let i = 0; i < a.n; i++) if (m[i] && !Number.isNaN(a.sueldo[i])) v.push(a.sueldo[i]);
  const total = (() => { let k = 0; for (let i = 0; i < a.n; i++) k += m[i]; return k; })();
  const titulo = '¿Cómo se distribuyen los sueldos?';
  if (!v.length) return vacio('V-10', titulo, 'Ninguna oferta de esta selección declara sueldo. Prueba quitar filtros.');
  const cob = `${v.length} de ${total} ofertas declaran sueldo (${pct(v.length / total)})`;
  const p = [25, 50, 75].map((q) => percentil(v, q) as number);
  const perfil = x.perfil;
  if (v.length < 30) {
    const puntos = []; const ids = a.ids;
    for (let i = 0; i < a.n; i++) if (m[i] && !Number.isNaN(a.sueldo[i])) puntos.push({ cat: 0, valor: a.sueldo[i], id: ids[i], titulo: a.snap.cols.titulo[i], empresa: a.snap.dicts.empresa[a.snap.cols.empresa[i]] ?? '' });
    return { id: 'V-10', titulo, span: 'c6', alto: 150, subtitulo: `Pocas ofertas (n=${v.length}): se muestra cada una como un punto`, cobertura: cob,
      aviso: 'Muestra insuficiente para un histograma', onclick: (e) => e.data?.meta?.id && x.abrir(e.data.meta.id),
      opcion: stripCaja(x.c, [{ nombre: 'Todas', color: x.c.serie[0], p25: v.length >= 5 ? p[0] : null, p50: v.length >= 5 ? p[1] : null, p75: v.length >= 5 ? p[2] : null, n: v.length }], puntos,
        { fmt: clp, banda: perfil?.salary_min ? { desde: perfil.salary_min, hasta: perfil.salary_max, texto: 'tu rango' } : undefined }),
      tabla: { columnas: ['Sueldo'], filas: v.map((s) => [s]) } };
  }
  const tope = (percentil(v, 98) as number) * 1.1, max = Math.min(Math.max(...v), Math.max(tope, 2 * PASO)), nb = Math.max(2, Math.ceil(max / PASO)), fuera = v.filter((s) => s >= nb * PASO).length;
  const cuentas = new Array(nb).fill(0); v.forEach((s) => { cuentas[Math.min(nb - 1, Math.floor(s / PASO))]++; });
  const et = cuentas.map((_, i) => clp(i * PASO));
  const idx = (s: number) => Math.min(nb - 1, Math.max(0, Math.floor(s / PASO)));
  return { id: 'V-10', titulo, span: 'c6', alto: 260, subtitulo: `Barras de ${clp(PASO)} · líneas: p25, mediana y p75`, cobertura: cob,
    aviso: [v.length < 60 ? 'muestra chica' : '', fuera ? `${fuera} oferta(s) sobre ${clp(nb * PASO)} no se dibujan (están en la tabla)` : ''].filter(Boolean).join(' · '),
    opcion: histograma(x.c, et, cuentas, { color: x.c.serie[0], etiquetaN: 'ofertas',
      lineas: p.map((s, i) => ({ idx: idx(s), texto: ['p25', 'mediana', 'p75'][i] + ' ' + clp(s), rotulo: i === 1 })),
      bandas: perfil?.salary_min ? [{ desde: idx(perfil.salary_min), hasta: idx(perfil.salary_max), texto: 'tu rango' }] : [] }),
    tabla: { columnas: ['Desde', 'Ofertas'], filas: et.map((e, i) => [e, cuentas[i]]) } };
}

/** V-11/12/13 — sueldos por dimensión: un punto por oferta + caja p25–p75 + mediana + IC 90 %. */
export function sueldosPor(x: Ctx, dim: string, id: string, titulo: string, span: EspecGrafico['span'] = 'c6'): EspecGrafico {
  const m = masc(x, dim), { a } = x;
  const res = agregar(a, { dim, metrica: 'sueldo_p50', mask: m, sinDato: false, bootstrap: true, orden: 'natural', ordenNatural: ORDEN_NATURAL[dim] });
  const grupos = res.grupos.filter((g) => g.n > 0);
  if (!grupos.some((g) => g.nBase > 0)) return vacio(id, titulo, 'Ninguna oferta de esta selección declara sueldo en este desglose.', span);
  const ordinal = dim === 'seniority', rampa = ordinal ? rampaOrdinal(x.c, grupos.length) : [];
  const sueldosDe = (filas: number[]) => filas.filter((i) => !Number.isNaN(a.sueldo[i]));
  const cats = grupos.map((g, k) => {
    const v = sueldosDe(g.filas).map((i) => a.sueldo[i]);
    const ok = g.estado !== 'insuficiente' && g.estado !== 'vacio';
    return { nombre: etiquetaValor(g.clave), color: ordinal ? rampa[k] : colorEntidad(x.c, dim, g.clave), n: v.length,
      p25: ok ? percentil(v, 25) : null, p50: ok ? percentil(v, 50) : null, p75: ok ? percentil(v, 75) : null, ic: ok ? g.ic ?? null : null };
  });
  const puntos = grupos.flatMap((g, k) => sueldosDe(g.filas).map((i) => ({ cat: k, valor: a.sueldo[i], id: a.ids[i], titulo: a.snap.cols.titulo[i],
    empresa: a.snap.dicts.empresa[a.snap.cols.empresa[i]] ?? '' })));
  const perfil = x.perfil;
  return { id, titulo, span, alto: Math.max(200, grupos.length * 44 + 50),
    subtitulo: 'Cada punto es una oferta · caja: p25–p75 · raya: mediana · barra inferior: IC 90 % de la mediana',
    cobertura: textoCobertura(res.cobertura, 'en el desglose') + ` · ${puntos.length} con sueldo`,
    aviso: avisoChica(grupos) || '', onclick: (e) => e.data?.meta?.id && x.abrir(e.data.meta.id),
    opcion: stripCaja(x.c, cats, puntos, { fmt: clp, banda: perfil?.salary_min ? { desde: perfil.salary_min, hasta: perfil.salary_max, texto: 'tu rango' } : undefined }),
    tabla: { columnas: ['Grupo', 'n con sueldo', 'p25', 'mediana', 'p75'], filas: cats.map((c) => [c.nombre, c.n, c.p25, c.p50, c.p75]) } };
}

/** V-14 — ¿quién publica el sueldo? (transparencia salarial) por dimensión. */
export function transparencia(x: Ctx, dim: 'fuente' | 'rol_familia', id: string, titulo: string): EspecGrafico {
  const m = masc(x, dim), { a } = x;
  const res = agregar(a, { dim, metrica: 'pct_con_sueldo', mask: m, sinDato: false });
  const g = res.grupos; if (!g.length) return vacio(id, titulo, 'Sin ofertas en esta selección.');
  let con = 0, tot = 0; for (let i = 0; i < a.n; i++) if (m[i]) { tot++; con += a.sueldoValido[i]; }
  return { id, titulo, span: 'c6', alto: Math.max(150, g.length * 30 + 40), subtitulo: '% de las ofertas del grupo que declaran sueldo', cobertura: textoCobertura(res.cobertura, 'con ese dato'),
    aviso: avisoChica(g), onclick: (e) => e.data?.meta?.clave && x.filtrar(dim, e.data.meta.clave),
    opcion: barrasH(x.c, g.map((k) => ({ nombre: k.clave, valor: k.valor, n: k.n, estado: k.estado, color: colorEntidad(x.c, dim, k.clave), clave: k.clave, filas: k.filas })),
      { fmt: (v) => pct(v), etiquetaValor: 'declaran sueldo', max: 1, referencia: tot ? { valor: con / tot, texto: `todas ${pct(con / tot)}` } : undefined }),
    tabla: { columnas: ['Grupo', 'Ofertas', '% con sueldo'], filas: g.map((k) => [k.clave, k.n, k.valor === null ? null : Math.round(k.valor * 100)]) } };
}

/** V-15 — ¿es realista mi rango? Tu rango objetivo frente al p25–p75 del mercado de tu familia. */
export function v15(x: Ctx): EspecGrafico {
  const titulo = '¿Es realista mi rango?', p = x.perfil, { a } = x;
  if (!p?.salary_min) return vacio('V-15', titulo, 'Define PROFILE_SALARY_MIN/MAX en .env para comparar tu rango.');
  const fam = x.f.sel.rol_familia?.[0] ?? familiaPerfil(p);
  const sen = x.f.sel.seniority?.[0] ?? '';
  const d = getDim(a, 'rol_familia'), ds = getDim(a, 'seniority');
  const filtro = (conSen: boolean) => { const v: number[] = []; for (let i = 0; i < a.n; i++) if (!Number.isNaN(a.sueldo[i]) && d.etiquetas[d.cod![i]] === fam && (!conSen || !sen || ds.etiquetas[ds.cod![i]] === sen)) v.push(a.sueldo[i]); return v; };
  let v = filtro(true), alcance = `${fam}${sen ? ' · ' + sen : ''}`;
  if (v.length < 10 && sen) { v = filtro(false); alcance = fam; }
  if (v.length < 5) return { ...vacio('V-15', titulo, `Sin datos suficientes para tu segmento (${alcance}: n=${v.length}, mínimo 5).`), subtitulo: 'Usa los filtros para probar otro segmento' };
  const [p25, p50, p75] = [25, 50, 75].map((q) => percentil(v, q) as number);
  const pc = Math.round((v.filter((s) => s <= p.salary_min).length / v.length) * 100);
  const c = x.c;
  const opcion = { ...base(c), grid: { left: 8, right: 24, top: 10, bottom: 26, containLabel: true },
    xAxis: { type: 'value', axisLabel: { color: c.suave, formatter: (s: number) => clp(s) }, splitLine: { lineStyle: { color: c.rejilla } }, min: 0 },
    yAxis: { type: 'category', inverse: true, data: [`Mercado (${alcance})`, 'Tu rango'], axisTick: { show: false }, axisLabel: { color: c.texto } },
    tooltip: { ...base(c).tooltip, trigger: 'item', formatter: (e: any) => { const el = document.createElement('div'); el.textContent = e.data.meta; return el; } },
    series: [{ type: 'bar', stack: 'r', silent: true, itemStyle: { color: 'transparent' }, data: [p25, p.salary_min] },
      { type: 'bar', stack: 'r', barMaxWidth: 22, itemStyle: { borderRadius: 4 }, data: [
        { value: p75 - p25, meta: `p25–p75: ${clp(p25)} – ${clp(p75)} (n=${v.length})`, itemStyle: { color: c.neutro } },
        { value: p.salary_max - p.salary_min, meta: `Tu rango: ${clp(p.salary_min)} – ${clp(p.salary_max)}`, itemStyle: { color: c.serie[0] } }],
        markLine: { symbol: 'none', silent: true, lineStyle: { color: c.texto, width: 2, type: 'solid' }, label: { formatter: `mediana ${clp(p50)}`, color: c.suave, position: 'end' }, data: [{ xAxis: p50 }] } }] };
  return { id: 'V-15', titulo, span: 'c6', alto: 150, opcion, subtitulo: `Tu mínimo (${clp(p.salary_min)}) está en el percentil ${pc} del mercado de ${alcance}`,
    cobertura: `n=${v.length} ofertas con sueldo en ${alcance}`, aviso: v.length < 10 ? 'muestra chica' : '',
    tabla: { columnas: ['Dato', 'Valor'], filas: [['p25', p25], ['mediana', p50], ['p75', p75], ['tu mínimo', p.salary_min], ['tu máximo', p.salary_max], ['percentil de tu mínimo', pc]] } };
}
