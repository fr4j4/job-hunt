// Ofertas elegidas para comparar (2–4). Vive en memoria: se pierde al recargar, a propósito.
export const comparar = $state<{ ids: string[] }>({ ids: [] });
export const MAX_COMPARAR = 4;

export function alternarComparar(id: string): boolean {
  const i = comparar.ids.indexOf(id);
  if (i >= 0) { comparar.ids.splice(i, 1); return true; }
  if (comparar.ids.length >= MAX_COMPARAR) return false;
  comparar.ids.push(id);
  return true;
}
