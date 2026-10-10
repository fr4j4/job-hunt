// Test de DATOS del catálogo: cada gráfico es una función pura (spec §12). Se verifican los números,
// el n, la cobertura y —sobre todo— la degradación con muestras chicas (nunca una conclusión sin respaldo).
import { describe, expect, it } from 'vitest';
import type { Perfil } from '../lib/tipos';
import { construirAlmacen } from '../motor/almacen';
import { filtrosVacios } from '../motor/filtros';
import { fixture, type FilaFix } from '../motor/fixture';
import { colores } from '../viz/tema';
import type { Ctx } from './comun';
import { v02, v61, v63 } from './dinamica';
import { v40, v41, v42 } from './empresas';
import { construir, recomendar } from './explorador';
import { kpis } from './kpis';
import { v72, v80 } from './posicion';
import { apilado100, barrasDim } from './roles';
import { sueldosPor, transparencia, v10, v15 } from './sueldos';
import { v20, v21, v22, v23 } from './tecnologias';

const perfil: Perfil = { techs: ['Python'], roles: ['Backend'], salary_min: 2_000_000, salary_max: 3_000_000, years_exp: 5, min_fit: 45, titulo: 'Dev' };
const ctx = (filas: FilaFix[], f = filtrosVacios()): Ctx => ({ a: construirAlmacen(fixture(filas)), f, c: colores(false), perfil, mias: new Set(['Python']),
  filtrar: () => {}, abrir: () => {}, verOfertas: () => {} });
const mk = (n: number, g: (i: number) => Partial<FilaFix>): FilaFix[] => Array.from({ length: n }, (_, i) => ({ id: `o${i}`, ...g(i) }));

describe('KPIs', () => {
  it('mediana de sueldo con n insuficiente no entrega valor y avisa', () => {
    const k = kpis(ctx(mk(10, (i) => ({ sueldo: i < 3 ? 1_000_000 + i * 100_000 : null }))));
    const sueldo = k.find((x) => x.id === 'sueldo')!;
    expect(sueldo.valor).toBeNull(); expect(sueldo.aviso).toMatch(/insuficiente/);
    expect(k.find((x) => x.id === 'transparencia')!.valor).toBeCloseTo(0.3);
  });
  it('mediana con n=6: valor + aviso de muestra chica', () => {
    const k = kpis(ctx(mk(6, (i) => ({ sueldo: 1_000_000 + i * 100_000 }))));
    expect(k.find((x) => x.id === 'sueldo')).toMatchObject({ valor: 1_250_000, aviso: 'muestra chica' });
  });
});

describe('sueldos', () => {
  it('V-10 con pocas ofertas muestra cada una (sin histograma)', () => {
    const e = v10(ctx(mk(12, (i) => ({ sueldo: 1_000_000 + i * 50_000 }))));
    expect(e.alto).toBeLessThan(200); expect(e.aviso).toMatch(/insuficiente/); expect(e.tabla!.filas).toHaveLength(12);
  });
  it('V-10 sin ningún sueldo: estado vacío con causa', () => {
    const e = v10(ctx(mk(5, () => ({ sueldo: null }))));
    expect(e.estado).toBe('vacio'); expect(e.mensaje).toMatch(/declara sueldo/);
  });
  it('V-10 con ≥30 sueldos arma el histograma y respeta el total', () => {
    const e = v10(ctx(mk(40, (i) => ({ sueldo: 800_000 + i * 60_000 }))));
    expect(e.opcion).toBeTruthy(); expect(e.tabla!.filas.reduce((s, f) => s + (f[1] as number), 0)).toBe(40);
  });
  it('V-11: familia con n<5 queda sin caja ni mediana; los puntos siguen', () => {
    const filas = [...mk(8, (i) => ({ fam: 'Desarrollo', sueldo: 1_000_000 + i * 100_000 })), ...mk(3, (i) => ({ id: `q${i}`, fam: 'QA', sueldo: 900_000 + i * 10_000 }))];
    const e = sueldosPor(ctx(filas), 'rol_familia', 'V-11', 't');
    const t = e.tabla!.filas;
    expect(t.find((f) => f[0] === 'Desarrollo')![3]).not.toBeNull();
    expect(t.find((f) => f[0] === 'QA')).toEqual(['QA', 3, null, null, null]);
    expect(e.aviso).toMatch(/sin muestra suficiente/);
  });
  it('V-14 transparencia: grupo con <10 ofertas no muestra porcentaje', () => {
    const e = transparencia(ctx(mk(6, (i) => ({ fuente: 'indeed', sueldo: i < 2 ? 1_500_000 : null }))), 'fuente', 'V-14', 't');
    expect(e.tabla!.filas[0][2]).toBeNull(); expect(e.aviso).toMatch(/sin muestra suficiente/);
  });
  it('V-15 sin segmento comparable explica por qué', () => {
    const e = v15(ctx(mk(30, (i) => ({ fam: 'QA', sueldo: i < 2 ? 1_000_000 : null }))));
    expect(e.estado).toBe('vacio'); expect(e.mensaje).toMatch(/Sin datos suficientes/);
  });
  it('V-15 con datos ubica tu mínimo en el percentil correcto', () => {
    const e = v15(ctx(mk(20, (i) => ({ fam: 'Desarrollo', sueldo: 1_000_000 + i * 100_000 }))));   // 1,0 M … 2,9 M
    expect(e.subtitulo).toMatch(/percentil 55/);       // 11 de 20 ≤ 2,0 M
  });
});

describe('tecnologías', () => {
  it('V-20: con base <20 muestra conteos, no porcentajes, y lo avisa', () => {
    const e = v20(ctx(mk(10, (i) => ({ techs: i < 6 ? ['Python'] : ['Java'] }))));
    expect(e.aviso).toMatch(/conteos/); expect(e.tabla!.filas[0]).toEqual(['Python', 6, 60]);
  });
  it('V-20: el denominador excluye las ofertas sin techs conocidas', () => {
    const e = v20(ctx(mk(40, (i) => ({ techs: i < 25 ? ['Python'] : null }))));
    expect(e.cobertura).toMatch(/25 ofertas con tecnologías conocidas \(de 40\)/);
    expect(e.tabla!.filas[0]).toEqual(['Python', 25, 100]);
  });
  it('V-21: lo demandado que no está en el perfil, ordenado', () => {
    const e = v21(ctx(mk(30, (i) => ({ techs: i < 20 ? ['Java'] : ['Python'] }))));
    expect(e.tabla!.filas.map((f) => f[0])).toEqual(['Java']);
  });
  it('V-22 requiere ≥20 ofertas con techs', () => { expect(v22(ctx(mk(10, () => ({ techs: ['Python'] })))).estado).toBe('vacio'); });
  it('V-23: sin ≥10 sueldos comparables no hay prima (y lo dice)', () => {
    const e = v23(ctx(mk(30, (i) => ({ techs: ['Python'], sueldo: i < 4 ? 1_000_000 : null }))));
    expect(e.estado).toBe('vacio'); expect(e.mensaje).toMatch(/mínimo 10/);
  });
  it('V-23 no confunde seniority con tecnología: misma paga por estrato → prima 0', () => {
    const filas = [...mk(20, (i) => ({ fam: 'Desarrollo', sen: 'senior', sueldo: 3_000_000, techs: i < 10 ? ['Python'] : ['Java'] })),
                   ...mk(20, (i) => ({ id: `j${i}`, fam: 'Desarrollo', sen: 'junior', sueldo: 1_000_000, techs: i < 10 ? ['Python'] : ['Java'] }))];
    const e = v23(ctx(filas));
    for (const f of e.tabla!.filas) expect(f[1]).toBe(0);
  });
});

describe('barras y apiladas', () => {
  it('apilado100: cada fila suma 100 %', () => {
    const e = apilado100(ctx(mk(30, (i) => ({ fam: i % 2 ? 'QA' : 'Desarrollo', mod: ['remoto', 'hibrido', ''][i % 3] }))), 'V-50', 't', 'rol_familia', 'modalidad');
    for (const f of e.tabla!.filas) expect((f.slice(1, -1) as number[]).reduce((s, v) => s + v, 0)).toBe(f[f.length - 1]);
  });
  it('barrasDim top-N no agrega la barra "Otras" y dice cuántos quedaron fuera', () => {
    const e = barrasDim(ctx(mk(40, (i) => ({ empresa: `E${i % 20}` }))), 'V-40', 't', 'empresa', { top: 5 });
    expect(e.tabla!.filas).toHaveLength(5); expect(e.cobertura).toMatch(/15 más no se muestran/);
  });
  it('V-40 calcula la concentración del top-10', () => {
    const e = v40(ctx(mk(60, (i) => ({ empresa: i < 30 ? 'Grande' : `E${i}` }))));
    expect(e.subtitulo).toMatch(/Las 10 primeras concentran/);
  });
  it('V-41 y V-42: empresas con pocas ofertas', () => {
    const c = ctx(mk(6, (i) => ({ empresa: `E${i}` })));
    expect(v41(c).tabla!.filas).toHaveLength(6); expect(v42(c).estado).toBe('vacio');
  });
});

describe('posición y calidad', () => {
  it('V-80 necesita al menos 3 sueldos; las ofertas sin sueldo NO se descartan', () => {
    expect(v80(ctx(mk(10, () => ({ sueldo: null })))).estado).toBe('vacio');
    const e = v80(ctx(mk(10, (i) => ({ sueldo: i < 4 ? 2_500_000 : null, score: 50 + i }))));
    expect(e.cobertura).toMatch(/4 ofertas con sueldo · 6 sin sueldo/); expect(e.tabla!.filas).toHaveLength(10);
  });
  it('V-72 detecta ofertas en varias fuentes', () => {
    const e = v72(ctx(mk(10, (i) => ({ fuente: 'indeed', nf: i < 3 ? 2 : 1 }))));
    expect(e.tabla!.filas[0]).toEqual(['indeed', 10]);
  });
});

describe('dinámica: oculta hasta tener historia', () => {
  const h = (dias: number) => ({ fechas: ['2026-10-09'], dias_historia: dias, metrica: 'n_nuevas', por: '*', series: [{ clave: '*', valores: [5], n_con_sueldo: [0] }] });
  it('V-02 con 3 días acumula; con 8 dibuja', () => {
    const c = ctx(mk(3, () => ({})));
    expect(v02(c, h(3)).estado).toBe('oculto'); expect(v02(c, h(3)).mensaje).toMatch(/3\/7 días/);
    expect(v02(c, h(8)).opcion).toBeTruthy();
  });
  it('V-61 oculta con pocas ofertas o cierres', () => {
    const c = ctx(mk(3, () => ({})));
    expect(v61(c, { oculto: true, n: 10, cierres: 1, minimo_n: 20, minimo_cierres: 5 }).mensaje).toMatch(/10\/20 ofertas y 1\/5 cierres/);
    expect(v61(c, { oculto: false, n: 100, cierres: 30, mediana: 12, t: [1, 12], s: [0.9, 0.5] }).opcion).toBeTruthy();
  });
  it('V-63 siempre disponible (usa el snapshot)', () => {
    const e = v63(ctx(mk(10, (i) => ({ ant: i }))));
    expect(e.tabla!.filas.reduce((s, f) => s + (f[1] as number), 0)).toBe(10);
  });
});

describe('explorador', () => {
  it('recomendador de forma', () => {
    expect(recomendar({ x: 'fuente', color: '', metrica: 'ofertas', forma: '' }).forma).toBe('barras');
    expect(recomendar({ x: 'rol_familia', color: 'modalidad', metrica: 'ofertas', forma: '' }).forma).toBe('apiladas');
    expect(recomendar({ x: 'rol_familia', color: 'modalidad', metrica: 'sueldo_p50', forma: '' }).forma).toBe('heatmap');
    expect(recomendar({ x: 'modalidad', color: '', metrica: 'sueldo_p50', forma: '' }).forma).toBe('strip');
    expect(recomendar({ x: 'dia', color: '', metrica: 'ofertas', forma: '' }).forma).toBe('lineas');
    expect(recomendar({ x: 'sueldo', color: '', metrica: 'ofertas', forma: '' }).forma).toBe('histograma');
  });
  it('baranda: más de 8 colores fuerza mapa de calor y avisa', () => {
    const r = recomendar({ x: 'rol_familia', color: 'region', metrica: 'ofertas', forma: '' }, 12);
    expect(r.forma).toBe('heatmap'); expect(r.avisos.join(' ')).toMatch(/más de 8 colores/); expect(r.invalidas.apiladas).toBeTruthy();
  });
  it('baranda: una forma inválida cae a la recomendada', () => {
    const { rec } = construir(ctx(mk(20, (i) => ({ dia: `2026-10-0${1 + (i % 5)}` }))), { x: 'dia', color: '', metrica: 'ofertas', forma: 'apiladas' }, (d) => d);
    expect(rec.invalidas.apiladas).toBeTruthy();
  });
  it('el explorador reproduce lo que muestra el dashboard (misma métrica)', () => {
    const c = ctx(mk(40, (i) => ({ fuente: i < 30 ? 'linkedin' : 'indeed', sueldo: i % 4 === 0 ? 1_500_000 : null })));
    const { espec } = construir(c, { x: 'fuente', color: '', metrica: 'pct_con_sueldo', forma: 'barras' }, (d) => d);
    const dash = transparencia(c, 'fuente', 'V-14', 't');
    expect(espec.tabla!.filas.map((f) => [f[0], f[1]])).toEqual(dash.tabla!.filas.map((f) => [f[0], f[1]]));
  });
});
