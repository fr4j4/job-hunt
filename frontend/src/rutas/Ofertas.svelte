<script lang="ts">
  // Ofertas en 4 formatos sobre el mismo filtro: lista densa (virtualizada), tarjetas, maestro-detalle y kanban.
  import { app, avisar, cargar } from '../estado/app.svelte';
  import { filtrosActuales, params, ruta, setParams, subruta, ir } from '../estado/ruta.svelte';
  import { mascara } from '../motor/filtros';
  import { fila, urlSegura, type FilaOferta } from '../lib/filas';
  import { clp, edad, etiquetaValor } from '../lib/formato';
  import { api } from '../lib/api';
  import FiltrosBarra from '../componentes/FiltrosBarra.svelte';
  import Detalle from './Detalle.svelte';
  import { comparar } from '../estado/comparar.svelte';

  const VISTAS = [['lista', 'Lista'], ['tarjetas', 'Tarjetas'], ['md', 'Maestro-detalle'], ['kanban', 'Kanban']] as const;
  const COLS: { id: string; t: string; num?: boolean }[] = [
    { id: 'score', t: 'Puntaje', num: true }, { id: 'encaje', t: 'Encaje' }, { id: 'titulo', t: 'Cargo' }, { id: 'empresa', t: 'Empresa' },
    { id: 'modalidad', t: 'Modalidad' }, { id: 'sueldo', t: 'Sueldo', num: true }, { id: 'fuente', t: 'Fuente' }, { id: 'antiguedad', t: 'Publicada', num: true },
  ];
  const ALTO_FILA = 42;

  const p = $derived.by(() => { void ruta.search; return params(); });
  const vista = $derived((p.get('vista') ?? 'lista') as (typeof VISTAS)[number][0]);
  const orden = $derived(p.get('orden') ?? 'score');
  const dir = $derived(p.get('dir') === 'asc' ? 'asc' : 'desc');
  // la URL lleva el id numérico (/ofertas/1234); un enlace viejo con el group_id se traduce y se reescribe
  const refRuta = $derived.by(() => { void ruta.path; return subruta(); });
  const idSel = $derived(/^\d+$/.test(refRuta) ? Number(refRuta) : null);
  $effect(() => {
    const ref = refRuta;
    if (ref && !/^\d+$/.test(ref)) api.oferta(ref).then((d) => ir(`/ofertas/${d.id}`, ruta.search, true)).catch(() => ir('/ofertas', ruta.search, true));
  });
  const a = $derived(app.almacen);
  const f = $derived.by(() => { void ruta.search; return filtrosActuales(); });

  const indices = $derived.by(() => {
    if (!a) return [] as number[];
    const m = mascara(a, f);
    const ix: number[] = [];
    for (let i = 0; i < a.n; i++) if (m[i]) ix.push(i);
    const c = a.snap.cols, s = a.snap.dicts, sg = dir === 'asc' ? 1 : -1;
    const clave: Record<string, (i: number) => number | string> = {
      score: (i) => a.score[i], encaje: (i) => ['', 'ninguno', 'bajo', 'medio', 'alto'].indexOf(c.encaje[i]),
      titulo: (i) => c.titulo[i].toLowerCase(), empresa: (i) => (s.empresa[c.empresa[i]] ?? '').toLowerCase(),
      modalidad: (i) => c.modalidad[i], sueldo: (i) => (Number.isNaN(a.sueldo[i]) ? -1 : a.sueldo[i]),
      fuente: (i) => a.dim.fuente.etiquetas[a.dim.fuente.cod![i]], antiguedad: (i) => a.antiguedad[i],
    };
    const k = clave[orden] ?? clave.score;
    ix.sort((x, y) => { const kx = k(x), ky = k(y); return (kx < ky ? -1 : kx > ky ? 1 : 0) * sg || a.score[y] - a.score[x]; });
    return ix;
  });

  function ordenar(id: string) {
    setParams({ orden: id, dir: orden === id && dir === 'desc' ? 'asc' : 'desc' });
  }
  // se conserva TODO el query (filtros + vista + orden + dir): reconstruirlo solo con los filtros perdía el orden
  function abrir(i: number) { ir(`/ofertas/${a!.ids[i]}`, ruta.search.replace(/^\?/, ''), false); }
  function cerrar() { ir('/ofertas', ruta.search.replace(/^\?/, ''), false); }
  const cambiarVista = (v: string) => setParams({ vista: v === 'lista' ? null : v }, true);

  // ---- lista virtualizada ----
  let scrollTop = $state(0), altoVista = $state(560);
  const desde = $derived(Math.max(0, Math.floor(scrollTop / ALTO_FILA) - 6));
  const hasta = $derived(Math.min(indices.length, Math.ceil((scrollTop + altoVista) / ALTO_FILA) + 6));
  const visibles = $derived(indices.slice(desde, hasta).map((i) => fila(a!, i)));
  let listaEl: HTMLDivElement | undefined = $state();
  $effect(() => { void ruta.search; if (listaEl) { listaEl.scrollTop = 0; scrollTop = 0; } });

  // ---- tarjetas (carga incremental) ----
  let limite = $state(60);
  $effect(() => { void ruta.search; limite = 60; });
  const tarjetas = $derived(indices.slice(0, limite).map((i) => fila(a!, i)));
  const mias = $derived(new Set(app.perfil?.techs ?? []));

  // ---- teclado ----
  function tecla(e: KeyboardEvent) {
    const t = e.target as HTMLElement;
    if (t.matches('input, textarea, select')) { if (e.key === 'Escape') t.blur(); return; }
    if (e.key === '/') { e.preventDefault(); document.querySelector<HTMLInputElement>('.filtros .buscar')?.focus(); return; }
    const pos = idSel && a ? indices.indexOf(a.indice.get(idSel) ?? -1) : -1;
    if (e.key === 'j' || e.key === 'k') {
      const sig = Math.max(0, Math.min(indices.length - 1, pos + (e.key === 'j' ? 1 : -1)));
      if (indices.length) { abrir(indices[sig]); if (listaEl) { const y = sig * ALTO_FILA; if (y < listaEl.scrollTop || y > listaEl.scrollTop + altoVista - ALTO_FILA) listaEl.scrollTop = y - altoVista / 2; } }
    } else if (e.key === 'Escape' && idSel) cerrar();
    else if ((e.key === 's' || e.key === 'x') && idSel) cambiarEstado(idSel, e.key === 's' ? 'guardada' : 'descartada');
  }
  async function cambiarEstado(id: number, estado: string) {
    try { if (estado) await api.ponerEstado(id, estado); else await api.quitarEstado(id); await cargar(true); avisar(estado ? `Marcada como ${etiquetaValor(estado)}` : 'Estado quitado'); }
    catch { avisar('No se pudo cambiar el estado'); }
  }

  // ---- kanban ----
  const ESTADOS = ['guardada', 'postulada', 'entrevista', 'oferta', 'descartada'];
  const kanban = $derived.by(() => {
    if (!a) return {} as Record<string, FilaOferta[]>;
    const out: Record<string, FilaOferta[]> = Object.fromEntries(ESTADOS.map((e) => [e, []]));
    for (const i of indices) { const e = a.snap.cols.estado[i]; if (e && out[e]) out[e].push(fila(a, i)); }
    return out;
  });
  let sobre = $state('');
  function soltar(e: DragEvent, estado: string) {
    e.preventDefault(); sobre = '';
    const id = Number(e.dataTransfer?.getData('text/plain')); if (id) cambiarEstado(id, estado);
  }

  // ---- exportar CSV (anti inyección de fórmulas) ----
  function exportar() {
    if (!a) return;
    const cel = (v: unknown) => { let s = v == null ? '' : String(v); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const cab = ['id', 'cargo', 'empresa', 'puntaje', 'encaje', 'modalidad', 'sueldo_clp', 'fuente', 'dias', 'techs', 'url'];
    const filas = indices.map((i) => { const r = fila(a, i); return [r.id, r.titulo, r.empresa, r.score, r.encaje, r.modalidad, r.sueldo ?? '', r.fuente, r.antiguedad, r.techs.join(';'), urlSegura(r.url)]; });
    const blob = new Blob(['﻿' + [cab, ...filas].map((r) => r.map(cel).join(',')).join('\n')], { type: 'text/csv;charset=utf-8' });
    const l = document.createElement('a'); l.href = URL.createObjectURL(blob); l.download = 'ofertas.csv'; l.click();
    setTimeout(() => URL.revokeObjectURL(l.href), 2000);
  }
  const pillEnc = (e: string) => (e === 'alto' ? 'alto' : e === 'medio' ? 'medio' : e ? 'bajo' : '');
  const grid = '64px 74px minmax(220px, 1.6fr) minmax(120px, 1fr) 100px 90px 100px 80px';
</script>

<svelte:window onkeydown={tecla} />

<FiltrosBarra />

<div class="fila" style:display="flex" style:gap="8px" style:align-items="center" style:margin-bottom="8px" style:flex-wrap="wrap">
  <div class="chips" role="group" aria-label="Formato">
    {#each VISTAS as [id, t]}<button class="btn chico" aria-pressed={vista === id} onclick={() => cambiarVista(id)}>{t}</button>{/each}
  </div>
  <span class="suave tnum">{indices.length.toLocaleString('es-CL')} resultados</span>
  <span style:margin-left="auto"></span>
  {#if comparar.ids.length}<button class="btn chico" onclick={() => ir('/comparar')}>Comparar ({comparar.ids.length})</button>{/if}
  <button class="btn chico" onclick={exportar}>Exportar CSV</button>
</div>

{#if !a}
  <div class="skeleton" style:height="300px"></div>
{:else if indices.length === 0 && vista !== 'kanban'}
  <div class="tarjeta vacio">Ninguna oferta coincide con estos filtros. Quita alguno de los chips de arriba.</div>
{:else if vista === 'lista'}
  <div class="tarjeta" style:padding="0">
    <div class="vfila" style:position="relative" style:display="grid" style:grid-template-columns={grid} style:height="34px" style:cursor="default" role="row" aria-label="Encabezados">
      {#each COLS as c}
        <button class="btn plano chico" style:justify-content={c.num ? 'flex-end' : 'flex-start'} onclick={() => ordenar(c.id)} aria-label="Ordenar por {c.t}">
          {c.t}{orden === c.id ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}
        </button>
      {/each}
    </div>
    <div class="vlist" bind:this={listaEl} bind:clientHeight={altoVista} style:height="calc(100vh - 290px)" style:min-height="320px"
         onscroll={(e) => (scrollTop = e.currentTarget.scrollTop)} role="grid" tabindex="-1">
      <div style:height="{indices.length * ALTO_FILA}px" style:position="relative">
        {#each visibles as r, k (r.id)}
          <div class="vfila" class:sel={idSel === r.id} role="row" tabindex="0" style:top="{(desde + k) * ALTO_FILA}px" style:height="{ALTO_FILA}px"
               style:grid-template-columns={grid} onclick={() => abrir(r.i)} onkeydown={(e) => e.key === 'Enter' && abrir(r.i)}>
            <span class="tnum" style:text-align="right"><span class="pill">{r.score}</span></span>
            <span>{#if r.encaje}<span class="pill {pillEnc(r.encaje)}">{r.encaje}</span>{/if}</span>
            <span class="cortar" title={r.titulo}>{r.titulo}{#if r.estado} <span class="chip">{etiquetaValor(r.estado)}</span>{/if}{#if r.posibleCerrada} <span class="chip" title="Posiblemente cerrada">⚠</span>{/if}</span>
            <span class="cortar suave" title={r.empresa}>{r.empresa}</span>
            <span class="cortar">{etiquetaValor(r.modalidad) === 'Sin dato' ? '' : etiquetaValor(r.modalidad)}</span>
            <span class="tnum" style:text-align="right">{r.sueldo ? clp(r.sueldo) : ''}</span>
            <span class="cortar suave">{r.fuente}</span>
            <span class="tnum suave" style:text-align="right">{edad(r.antiguedad)}</span>
          </div>
        {/each}
      </div>
    </div>
  </div>
{:else if vista === 'tarjetas'}
  <div class="rejilla-tarjetas">
    {#each tarjetas as r (r.id)}
      <button class="tarjeta oferta-card" onclick={() => abrir(r.i)}>
        <div style:display="flex" style:gap="6px" style:align-items="center">
          <span class="pill">{r.score}</span>{#if r.encaje}<span class="pill {pillEnc(r.encaje)}">{r.encaje}</span>{/if}
          {#if r.estado}<span class="chip">{etiquetaValor(r.estado)}</span>{/if}
          <span class="suave" style:margin-left="auto">{edad(r.antiguedad)}</span>
        </div>
        <div class="t">{r.titulo}</div>
        <div class="suave">{r.empresa || 'Empresa no informada'}{r.comuna ? ' · ' + r.comuna : r.region && r.region !== 'desconocida' ? ' · ' + r.region : ''}</div>
        {#if r.resumen}<div class="suave" style:font-size="12.5px">{r.resumen.slice(0, 150)}{r.resumen.length > 150 ? '…' : ''}</div>{/if}
        <div class="techs">{#each r.techs.slice(0, 6) as t}<span class="chip" class:mia={mias.has(t)}>{t}</span>{/each}</div>
        <div style:display="flex" style:gap="10px" class="suave">
          <b style:color="var(--texto)">{r.sueldo ? clp(r.sueldo) : 'Sin sueldo'}</b>
          {#if r.modalidad}<span>{etiquetaValor(r.modalidad)}</span>{/if}{#if r.seniority}<span>{etiquetaValor(r.seniority)}</span>{/if}<span style:margin-left="auto">{r.fuente}</span>
        </div>
      </button>
    {/each}
  </div>
  {#if limite < indices.length}<p style:text-align="center"><button class="btn" onclick={() => (limite += 60)}>Mostrar más ({indices.length - limite} restantes)</button></p>{/if}
{:else if vista === 'md'}
  <div class="md">
    <div class="vlist" style:height="calc(100vh - 290px)" style:min-height="320px" role="listbox" tabindex="-1">
      {#each indices.slice(0, 400) as i (a.ids[i])}
        {@const r = fila(a, i)}
        <div style:position="relative" style:height="{ALTO_FILA + 14}px" style:border-bottom="1px solid var(--rejilla)">
          <div class="vfila" class:sel={idSel === r.id} role="option" aria-selected={idSel === r.id} tabindex="0" style:top="0" style:height="{ALTO_FILA + 14}px"
               style:grid-template-columns="48px 1fr" onclick={() => abrir(i)} onkeydown={(e) => e.key === 'Enter' && abrir(i)}>
            <span class="pill">{r.score}</span>
            <span><div class="cortar" style:font-weight="600">{r.titulo}</div><div class="cortar suave">{r.empresa}{r.sueldo ? ' · ' + clp(r.sueldo) : ''}{r.modalidad ? ' · ' + etiquetaValor(r.modalidad) : ''}</div></span>
          </div>
        </div>
      {/each}
    </div>
    <div class="tarjeta panel-detalle">
      {#if idSel}<Detalle id={idSel} alCerrar={cerrar} />{:else}<p class="vacio">Elige una oferta de la lista (o usa <kbd>j</kbd> / <kbd>k</kbd>).</p>{/if}
    </div>
  </div>
{:else}
  <p class="suave">Arrastra una tarjeta entre columnas para cambiar su estado. También sirve el teclado: en el detalle, <kbd>s</kbd> guarda y <kbd>x</kbd> descarta.</p>
  <div class="kanban">
    {#each ESTADOS as e}
      <div class="kcol" class:sobre={sobre === e} role="list" aria-label={etiquetaValor(e)} ondragover={(ev) => { ev.preventDefault(); sobre = e; }} ondragleave={() => (sobre = '')} ondrop={(ev) => soltar(ev, e)}>
        <h3>{etiquetaValor(e)} <span class="muted tnum">{kanban[e]?.length ?? 0}</span></h3>
        {#each kanban[e] ?? [] as r (r.id)}
          <div class="tarjeta kcard" role="listitem" draggable="true" ondragstart={(ev) => ev.dataTransfer?.setData('text/plain', String(r.id))}>
            <button class="btn plano" style:padding="0" style:text-align="left" onclick={() => abrir(r.i)}><b>{r.titulo}</b></button>
            <div class="suave cortar">{r.empresa}</div>
            <div class="chips" style:margin-top="4px">
              <span class="pill">{r.score}</span>{#if r.sueldo}<span class="chip">{clp(r.sueldo)}</span>{/if}
              <select class="input" style:padding="1px 4px" style:font-size="12px" aria-label="Mover a" onchange={(ev) => cambiarEstado(r.id, ev.currentTarget.value)}>
                {#each ESTADOS as o}<option value={o} selected={o === e}>{etiquetaValor(o)}</option>{/each}
              </select>
            </div>
          </div>
        {/each}
        {#if !(kanban[e]?.length)}<p class="muted" style:text-align="center">Vacío</p>{/if}
      </div>
    {/each}
  </div>
{/if}

{#if idSel && vista !== 'md'}
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
  <div class="fondo-drawer" onclick={cerrar}></div>
  <aside class="drawer" aria-label="Detalle de la oferta"><Detalle id={idSel} alCerrar={cerrar} /></aside>
{/if}
