// Tooltips como nodos DOM construidos con textContent: los títulos/empresas son datos NO confiables
// y nunca pasan por innerHTML (spec §7.1).
export interface FilaTip { color?: string; etiqueta: string; valor: string; fuerte?: boolean }

export function tip(titulo: string, filas: FilaTip[], pie = ''): HTMLElement {
  const raiz = document.createElement('div');
  raiz.className = 'tip';
  const t = document.createElement('div');
  t.textContent = titulo;
  t.className = 'tip-t';
  raiz.append(t);
  for (const f of filas) {
    const r = document.createElement('div');
    r.className = 'tip-f';
    if (f.color) { const k = document.createElement('span'); k.className = 'tip-k'; k.style.background = f.color; r.append(k); }
    const v = document.createElement('b'); v.textContent = f.valor; v.className = 'tip-v';
    const e = document.createElement('span'); e.textContent = f.etiqueta; e.className = 'tip-e';
    r.append(v, e);
    raiz.append(r);
  }
  if (pie) { const p = document.createElement('div'); p.textContent = pie; p.className = 'tip-p'; raiz.append(p); }
  return raiz;
}
