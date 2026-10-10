<script lang="ts">
  // Compara dos segmentos con las mismas métricas (p. ej. remoto vs presencial). Honesto con la muestra.
  import type { Ctx } from '../catalogo/comun';
  import { comparar } from '../catalogo/comparar';
  import { clp, etiquetaValor, pct } from '../lib/formato';
  import { getDim } from '../motor/almacen';
  import { ORDEN_NATURAL } from '../viz/tema';
  import Grafico from '../viz/Grafico.svelte';

  let { x }: { x: Ctx } = $props();
  const DIMS: [string, string][] = [['modalidad', 'Modalidad'], ['rol_familia', 'Familia de rol'], ['seniority', 'Seniority'], ['fuente', 'Fuente'],
    ['encaje', 'Encaje IA'], ['region', 'Región'], ['ingles', 'Inglés'], ['empleo', 'Jornada']];
  let dim = $state('modalidad'), va = $state(''), vb = $state('');
  const valores = $derived.by(() => { const o = ORDEN_NATURAL[dim]; return getDim(x.a, dim).etiquetas.filter((e) => e !== '')
    .sort((p, q) => (o ? o.indexOf(p) - o.indexOf(q) : p.localeCompare(q, 'es'))); });
  $effect(() => { const v = valores; if (!v.includes(va)) va = v[0] ?? ''; if (!v.includes(vb) || vb === va) vb = v.find((e) => e !== va) ?? ''; });
  const r = $derived(va && vb && va !== vb ? comparar(x, dim, va, vb) : null);
  const fila = (t: string, fa: string, fb: string) => ({ t, fa, fb });
  const tabla = $derived(r ? [
    fila('Ofertas', String(r.a.n), String(r.b.n)),
    fila('Declaran sueldo', r.a.pctSueldo === null ? '—' : `${pct(r.a.pctSueldo)} (${r.a.nSueldo})`, r.b.pctSueldo === null ? '—' : `${pct(r.b.pctSueldo)} (${r.b.nSueldo})`),
    fila('Sueldo mediano', r.a.mediana === null ? `— (n=${r.a.nSueldo})` : clp(r.a.mediana), r.b.mediana === null ? `— (n=${r.b.nSueldo})` : clp(r.b.mediana)),
    fila('IC 90 % de la mediana', r.a.ic ? `${clp(r.a.ic[0])} – ${clp(r.a.ic[1])}` : '—', r.b.ic ? `${clp(r.b.ic[0])} – ${clp(r.b.ic[1])}` : '—'),
    fila('Puntaje medio', r.a.score === null ? '—' : r.a.score.toFixed(1), r.b.score === null ? '—' : r.b.score.toFixed(1)),
    fila('Encaje alto', r.a.encajeAlto === null ? '—' : pct(r.a.encajeAlto), r.b.encajeAlto === null ? '—' : pct(r.b.encajeAlto)),
    fila('Antigüedad mediana', r.a.antiguedad === null ? '—' : `${Math.round(r.a.antiguedad)} d`, r.b.antiguedad === null ? '—' : `${Math.round(r.b.antiguedad)} d`),
    fila('Tecnologías más pedidas', r.a.techs.map((t) => `${t.tech} ${Math.round(t.pct * 100)} %`).join(', ') || '—', r.b.techs.map((t) => `${t.tech} ${Math.round(t.pct * 100)} %`).join(', ') || '—'),
  ] : []);
</script>

<section class="tarjeta" aria-label="Comparar segmentos" style:margin-top="12px">
  <h2>Comparar dos segmentos</h2>
  <p class="suave" style:margin-top="0">Mismos filtros de arriba, cambiando solo la dimensión elegida (p. ej. remoto contra presencial).</p>
  <div class="chips" style:margin-bottom="8px">
    <label>Dimensión <select class="input" bind:value={dim}>{#each DIMS as [d, t]}<option value={d}>{t}</option>{/each}</select></label>
    <label>A <select class="input" bind:value={va}>{#each valores as v}<option value={v}>{etiquetaValor(v)}</option>{/each}</select></label>
    <label>B <select class="input" bind:value={vb}>{#each valores.filter((v) => v !== va) as v}<option value={v}>{etiquetaValor(v)}</option>{/each}</select></label>
  </div>
  {#if r}
    <p data-testid="veredicto"><b>{r.veredicto}</b></p>
    <div class="scroll-x"><table class="tabla"><thead><tr><th></th><th>A · {r.a.etiqueta}</th><th>B · {r.b.etiqueta}</th></tr></thead>
      <tbody>{#each tabla as f}<tr><th scope="row">{f.t}</th><td>{f.fa}</td><td>{f.fb}</td></tr>{/each}</tbody></table></div>
    <div style:margin-top="10px">
      <Grafico titulo={r.espec.titulo} subtitulo={r.espec.subtitulo} opcion={r.espec.opcion} tabla={r.espec.tabla} alto={r.espec.alto} estado={r.espec.estado}
               mensaje={r.espec.mensaje} cobertura={r.espec.cobertura} leyenda={r.espec.leyenda} onclick={r.espec.onclick} />
    </div>
  {:else}<p class="vacio">Elige dos valores distintos para comparar.</p>{/if}
</section>
