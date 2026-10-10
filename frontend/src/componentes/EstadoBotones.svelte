<script lang="ts">
  import { api } from '../lib/api';
  import { avisar, cargar } from '../estado/app.svelte';
  import { etiquetaValor } from '../lib/formato';
  let { id, actual = '', nota = '', alCambiar }: { id: number; actual?: string; nota?: string; alCambiar?: (e: string) => void } = $props();
  const ESTADOS = ['guardada', 'postulada', 'entrevista', 'oferta', 'descartada'];
  let notaLocal = $state(nota);
  async function poner(e: string) {
    try {
      if (e === actual) { await api.quitarEstado(id); alCambiar?.(''); }
      else { await api.ponerEstado(id, e, notaLocal); alCambiar?.(e); }
      await cargar(true);
    } catch { avisar('No se pudo guardar el estado'); }
  }
  async function guardarNota() { if (actual) { try { await api.ponerEstado(id, actual, notaLocal); avisar('Nota guardada'); } catch { avisar('No se pudo guardar la nota'); } } }
</script>
<div class="chips" role="group" aria-label="Mi estado">
  {#each ESTADOS as e}
    <button class="btn chico" aria-pressed={actual === e} onclick={() => poner(e)}>{etiquetaValor(e)}</button>
  {/each}
</div>
{#if actual}
  <textarea class="input" style:width="100%" style:margin-top="6px" rows="2" maxlength="2000" placeholder="Notas privadas…"
            bind:value={notaLocal} onblur={guardarNota}></textarea>
{/if}
