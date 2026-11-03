import { describe, expect, it } from 'vitest';
import { construirAlmacen } from './almacen';
import { agregar, demandaTech, estadoDe, matriz } from './agregar';
import { aQuery, contar, desdeQuery, filtrosVacios, mascara } from './filtros';
import { fixture } from './fixture';

const filas = [
  { id: 'a', fuente: 'linkedin', fam: 'Desarrollo', sen: 'senior', mod: 'remoto', sueldo: 3_000_000, techs: ['Python', 'AWS'], score: 80, enc: 'alto', empresa: 'Acme' },
  { id: 'b', fuente: 'linkedin', fam: 'Desarrollo', sen: 'junior', mod: 'remoto', sueldo: 1_500_000, techs: ['Python'], score: 60, enc: 'medio', empresa: 'Acme' },
  { id: 'c', fuente: 'laborum', fam: 'Datos e IA', sen: 'senior', mod: 'hibrido', sueldo: null, techs: ['Python', 'SQL'], score: 70, enc: 'alto', empresa: 'Beta' },
  { id: 'd', fuente: 'indeed', fam: 'QA', sen: '', mod: '', sueldo: null, techs: null, score: 20, enc: '', empresa: '' },
  { id: 'e', fuente: 'laborum', fam: 'Desarrollo', sen: 'senior', mod: 'presencial', sueldo: 2_000_000, techs: ['Java'], score: 55, enc: 'bajo', empresa: 'Beta' },
];
const A = construirAlmacen(fixture(filas));

describe('filtros', () => {
  it('sin filtros pasa todo', () => expect(contar(mascara(A, filtrosVacios()))).toBe(5));
  it('selección univalor y combinación AND entre dimensiones', () => {
    const f = filtrosVacios(); f.sel.fuente = ['linkedin'];
    expect(contar(mascara(A, f))).toBe(2);
    f.sel.seniority = ['senior'];
    expect(contar(mascara(A, f))).toBe(1);
  });
  it('multivalor: o / y', () => {
    const f = filtrosVacios(); f.sel.tech = ['Python', 'AWS'];
    expect(contar(mascara(A, f))).toBe(3);                 // o
    f.techsModo = 'y';
    expect(contar(mascara(A, f))).toBe(1);                 // y
  });
  it('sin dato se puede filtrar con ""', () => {
    const f = filtrosVacios(); f.sel.modalidad = [''];
    expect(contar(mascara(A, f))).toBe(1);
  });
  it('rangos y conSueldo', () => {
    const f = filtrosVacios(); f.rangos.sueldo = [1_800_000, null];
    expect(contar(mascara(A, f))).toBe(2);
    const g = filtrosVacios(); g.conSueldo = true;
    expect(contar(mascara(A, g))).toBe(3);
  });
  it('texto libre', () => {
    const f = filtrosVacios(); f.q = 'ACME';               // indexa título+empresa+resumen, sin distinguir mayúsculas
    expect(contar(mascara(A, f))).toBe(2);
    f.q = 'no-existe';
    expect(contar(mascara(A, f))).toBe(0);
  });
  it('filtro cruzado: la dimensión agrupada no se filtra a sí misma', () => {
    const f = filtrosVacios(); f.sel.fuente = ['linkedin'];
    const conTodo = agregar(A, { dim: 'fuente', metrica: 'ofertas', mask: mascara(A, f) });
    expect(conTodo.grupos.map((g) => g.clave)).toEqual(['linkedin']);          // colapsa
    const cruzado = agregar(A, { dim: 'fuente', metrica: 'ofertas', mask: mascara(A, f, ['fuente']) });
    expect(cruzado.grupos.map((g) => g.clave).sort()).toEqual(['indeed', 'laborum', 'linkedin']);
  });
  it('URL: ida y vuelta', () => {
    const f = filtrosVacios();
    f.q = 'back end'; f.sel.rol_familia = ['Desarrollo', 'QA']; f.sel.tech = ['C#', 'Node.js']; f.techsModo = 'y';
    f.sel.modalidad = ['']; f.rangos.sueldo = [1000000, null]; f.rangos.score = [null, 80]; f.conSueldo = true;
    f.sel.empresa = ['Acme, Inc'];
    expect(desdeQuery(aQuery(f))).toEqual(f);
    expect(desdeQuery('')).toEqual(filtrosVacios());
  });
});

describe('agregación y degradación', () => {
  const todo = mascara(A, filtrosVacios());
  it('estados de muestra', () => {
    expect(estadoDe('sueldo_p50', 0)).toBe('vacio');
    expect(estadoDe('sueldo_p50', 4)).toBe('insuficiente');
    expect(estadoDe('sueldo_p50', 5)).toBe('chica');
    expect(estadoDe('sueldo_p50', 10)).toBe('ok');
    expect(estadoDe('pct_con_sueldo', 9)).toBe('insuficiente');
  });
  it('mediana de sueldo con n insuficiente NO entrega valor (se muestran puntos)', () => {
    const r = agregar(A, { dim: 'rol_familia', metrica: 'sueldo_p50', mask: todo });
    const dev = r.grupos.find((g) => g.clave === 'Desarrollo')!;
    expect(dev.nBase).toBe(3);                                              // a, b y e declaran sueldo
    expect(dev.valor).toBeNull();
    expect(dev.estado).toBe('insuficiente');
    expect(dev.filas.length).toBe(3);                                       // las filas siguen disponibles
  });
  it('pct_con_sueldo y orden por valor', () => {
    const r = agregar(A, { dim: 'fuente', metrica: 'ofertas', mask: todo });
    expect(r.grupos.map((g) => [g.clave, g.n])).toEqual([['linkedin', 2], ['laborum', 2], ['indeed', 1]]);
  });
  it('sin dato va al final y cuenta como excluido en la cobertura', () => {
    const r = agregar(A, { dim: 'modalidad', metrica: 'ofertas', mask: todo });
    expect(r.grupos[r.grupos.length - 1].clave).toBe('');
    const sin = agregar(A, { dim: 'modalidad', metrica: 'ofertas', mask: todo, sinDato: false });
    expect(sin.cobertura).toMatchObject({ total: 5, usadas: 4, excluidas: 1 });
  });
  it('top-N pliega el resto en "Otras"', () => {
    const r = agregar(A, { dim: 'fuente', metrica: 'ofertas', mask: todo, top: 2 });
    expect(r.grupos.map((g) => g.clave)).toEqual(['linkedin', 'laborum', 'Otras (1)']);
  });
  it('demanda de tecnologías usa como base las ofertas con techs CONOCIDAS', () => {
    const d = demandaTech(A, todo);
    expect(d.total).toBe(5); expect(d.base).toBe(4);                         // d no informó techs
    expect(d.items[0]).toMatchObject({ tech: 'Python', n: 3 });
    expect(d.items[0].pct).toBeCloseTo(3 / 4);
    expect(d.suficiente).toBe(false);                                        // base < 20
  });
  it('matriz fam × seniority', () => {
    const m = matriz(A, 'rol_familia', 'seniority', 'ofertas', todo);
    const c = m.celdas.find((x) => x.x === 'Desarrollo' && x.y === 'senior')!;
    expect(c.n).toBe(2);
  });
});
