<script lang="ts">
  // Comparador de 2–4 ofertas lado a lado; resalta lo que difiere entre ellas.
  import { api } from '../lib/api';
  import type { DetalleOferta } from '../lib/tipos';
  import { comparar } from '../estado/comparar.svelte';
  import { ir } from '../estado/ruta.svelte';
  import { clp, etiquetaValor } from '../lib/formato';
  import { urlSegura } from '../lib/filas';

  let datos = $state<DetalleOferta[]>([]);
  let cargando = $state(true);
  $effect(() => {
    const ids = [...comparar.ids];
    cargando = true;
    Promise.all(ids.map((id) => api.oferta(id).catch(() => null))).then((r) => { datos = r.filter((x): x is DetalleOferta => !!x); cargando = false; });
  });

  type Fila = { titulo: string; celdas: string[]; num?: boolean };
  const filas = $derived.by<Fila[]>(() => {
    const d = datos;
    const f = (titulo: string, fn: (o: DetalleOferta) => string): Fila => ({ titulo, celdas: d.map(fn) });
    const comunes = d.length ? d.map((o) => new Set(o.techs)).reduce((a, b) => new Set([...a].filter((x) => b.has(x)))) : new Set<string>();
    return [
      f('Empresa', (o) => o.empresa || 'No informada'), f('Ubicación', (o) => o.ubicacion || '—'),
      f('Puntaje de encaje', (o) => `${o.score}/100`), f('Puntaje de mercado', (o) => `${o.market_score}/100`),
      f('Encaje IA', (o) => o.encaje || 'sin evaluar'),
      f('Sueldo', (o) => (o.sueldo ? clp(o.sueldo) : 'no declarado')),
      f('Modalidad', (o) => (o.modalidad ? etiquetaValor(o.modalidad) + (o.modalidad_src === 'inferida' ? ' (inferida)' : '') : '—')),
      f('Seniority', (o) => (o.seniority ? etiquetaValor(o.seniority) : '—')), f('Familia', (o) => o.rol_familia),
      f('Experiencia pedida', (o) => (o.exp_anios !== null ? `${o.exp_anios} años` : '—')),
      f('Tecnologías', (o) => o.techs.map((t) => (comunes.has(t) ? t : `${t}*`)).join(', ') || '—'),
      f('Beneficios', (o) => o.beneficios.slice(0, 5).join(' · ') || '—'),
      f('A considerar', (o) => o.alertas.slice(0, 4).join(' · ') || '—'),
      f('A favor', (o) => o.a_favor.slice(0, 4).join(' · ') || '—'),
      f('Fuentes', (o) => o.fuentes.join(', ') || o.fuente),
      f('Vista por primera vez', (o) => o.first_seen.slice(0, 10)),
    ];
  });
  const difiere = (f: Fila) => new Set(f.celdas).size > 1;
  const quitar = (id: number) => { const i = comparar.ids.indexOf(id); if (i >= 0) comparar.ids.splice(i, 1); };
</script>

<h1>Comparar ofertas</h1>
{#if comparar.ids.length < 2}
  <div class="tarjeta vacio">Elige al menos 2 ofertas: en el detalle de una oferta, usa «Agregar a comparación».
    <p><button class="btn" onclick={() => ir('/ofertas')}>Ir a Ofertas</button></p></div>
{:else if cargando}
  <div class="skeleton" style:height="260px"></div>
{:else}
  <p class="suave">Lo que difiere entre las ofertas aparece resaltado. En «Tecnologías», un asterisco (*) marca las que no tienen todas.</p>
  <div class="tarjeta scroll-x" style:padding="0">
    <table class="tabla" aria-label="Comparación de ofertas">
      <thead><tr><th></th>{#each datos as o}
        <th style:min-width="220px" style:vertical-align="top">
          <a href="/v2/ofertas/{o.id}" onclick={(e) => { e.preventDefault(); ir(`/ofertas/${o.id}`); }}>{o.titulo}</a>
          <div><button class="btn chico plano" onclick={() => quitar(o.id)}>Quitar ✕</button>
          {#if urlSegura(o.url)}<a class="btn chico plano" href={urlSegura(o.url)} target="_blank" rel="noopener noreferrer">Abrir ↗</a>{/if}</div>
        </th>{/each}</tr></thead>
      <tbody>
        {#each filas as f}
          <tr><th scope="row">{f.titulo}</th>{#each f.celdas as c}<td style:background={difiere(f) ? 'var(--hover)' : undefined}>{c}</td>{/each}</tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
