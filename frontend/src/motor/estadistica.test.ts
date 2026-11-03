import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { bootstrapMediana, lift, mulberry32, percentil, primaEstratificada } from './estadistica';

// Los MISMOS vectores que valida Python (tests/test_analytics_normalizar.py).
const f = JSON.parse(readFileSync(new URL('../../../tests/fixtures/estadistica.json', import.meta.url), 'utf8'));

describe('paridad con Python', () => {
  it('percentil', () => {
    for (const c of f.percentil) expect(percentil(c.v, c.p)).toBeCloseTo(c.e, 9);
    expect(percentil([], 50)).toBeNull();
  });
  it('mulberry32', () => {
    const r = mulberry32(f.mulberry32.seed);
    f.mulberry32.secuencia.forEach((e: number) => expect(r()).toBeCloseTo(e, 12));
  });
  it('bootstrap', () => {
    const b = f.bootstrap;
    const [lo, hi] = bootstrapMediana(b.v, b.B, b.nivel, b.semilla)!;
    expect(lo).toBeCloseTo(b.e[0], 9);
    expect(hi).toBeCloseTo(b.e[1], 9);
    expect(bootstrapMediana([5])).toBeNull();
  });
  it('lift', () => {
    const l = f.lift;
    expect(lift(l.n_ab, l.n_a, l.n_b, l.n)).toBeCloseTo(l.e, 9);
    expect(lift(0, 0, 5, 100)).toBeNull();
  });
  it('prima estratificada', () => {
    const [p, n] = primaEstratificada(f.prima.filas);
    expect(p).toBeCloseTo(f.prima.e[0], 9);
    expect(n).toBe(f.prima.e[1]);
  });
});
