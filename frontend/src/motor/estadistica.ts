// Gemela de jobhunt/analytics/estadistica.py. Ambas pasan tests/fixtures/estadistica.json.
export function percentil(valores: ArrayLike<number | null | undefined>, p: number): number | null {
  const v: number[] = [];
  for (let i = 0; i < valores.length; i++) {
    const x = valores[i];
    if (x !== null && x !== undefined && !Number.isNaN(x)) v.push(x);
  }
  if (!v.length) return null;
  v.sort((a, b) => a - b);
  if (v.length === 1) return v[0];
  const pos = ((v.length - 1) * p) / 100;
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, v.length - 1);
  return v[lo] + (v[hi] - v[lo]) * (pos - lo);
}
export const mediana = (v: ArrayLike<number | null | undefined>) => percentil(v, 50);

/** PRNG determinista (mulberry32); idéntico al de Python para que el bootstrap sea reproducible. */
export function mulberry32(semilla: number): () => number {
  let a = semilla >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function bootstrapMediana(valores: number[], B = 500, nivel = 0.9, semilla = 12345): [number, number] | null {
  const n = valores.length;
  if (n < 2) return null;
  const rng = mulberry32(semilla);
  const meds: number[] = [];
  const muestra = new Array<number>(n);
  for (let b = 0; b < B; b++) {
    for (let i = 0; i < n; i++) muestra[i] = valores[Math.floor(rng() * n)];
    meds.push(mediana(muestra) as number);
  }
  const a = ((1 - nivel) / 2) * 100;
  return [percentil(meds, a) as number, percentil(meds, 100 - a) as number];
}

export function lift(nAB: number, nA: number, nB: number, n: number): number | null {
  if (!(n && nA && nB)) return null;
  return nAB / n / ((nA / n) * (nB / n));
}

/** Prima salarial de una tech controlando por estrato (ver spec §2.3). */
export function primaEstratificada(
  filas: [string, number | null, boolean][], minPorLado = 3,
): [number | null, number] {
  const por = new Map<string, [number[], number[]]>();
  for (const [estrato, sueldo, con] of filas) {
    if (sueldo === null) continue;
    let e = por.get(estrato);
    if (!e) por.set(estrato, (e = [[], []]));
    e[con ? 0 : 1].push(sueldo);
  }
  let num = 0, den = 0, nCon = 0;
  for (const [con, sin] of por.values()) {
    if (con.length >= minPorLado && sin.length >= minPorLado) {
      const ms = mediana(sin) as number;
      if (ms) {
        num += (con.length * ((mediana(con) as number) - ms)) / ms;
        den += con.length;
        nCon += con.length;
      }
    }
  }
  return [den ? num / den : null, nCon];
}
