<script lang="ts">
  // Barra de filtros global: la misma en Ofertas, Análisis y Explorador (los filtros viajan en la URL).
  import { app } from '../estado/app.svelte';
  import { filtrosActuales, ruta, setFiltros } from '../estado/ruta.svelte';
  import { contar, contarFiltros, filtrosVacios, mascara, type Filtros } from '../motor/filtros';
  import { agrupar } from '../motor/agregar';
  import { getDim } from '../motor/almacen';
  import { etiquetaValor } from '../lib/formato';
  import { api } from '../lib/api';

  const FACETS: { dim: string; etiqueta: string; buscable?: boolean }[] = [
    { dim: 'rol_familia', etiqueta: 'Familia' }, { dim: 'seniority', etiqueta: 'Seniority' },
    { dim: 'modalidad', etiqueta: 'Modalidad' }, { dim: 'tech', etiqueta: 'Tecnologías', buscable: true },
    { dim: 'fuente', etiqueta: 'Fuente' }, { dim: 'encaje', etiqueta: 'Encaje' },
    { dim: 'region', etiqueta: 'Región' }, { dim: 'empresa', etiqueta: 'Empresa', buscable: true },
    { dim: 'ingles', etiqueta: 'Inglés' }, { dim: 'empleo', etiqueta: 'Jornada' }, { dim: 'estado', etiqueta: 'Mi estado' },
  ];
  const ETQ: Record<string, string> = Object.fromEntries(FACETS.map((f) => [f.dim, f.etiqueta]));

  let abierto = $state<string | null>(null);
  let movilAbierto = $state(false);   // en pantallas chicas los facets se pliegan tras un botón
  let busca = $state('');
  let qLocal = $state(filtrosActuales().q);
  let profundo = $state(false);
  let timer: ReturnType<typeof setTimeout>;

  const f = $derived.by(() => { void ruta.search; return filtrosActuales(); });
  const a = $derived(app.almacen);
  const n = $derived(a ? contar(mascara(a, f)) : 0);
  const conSueldo = $derived.by(() => { if (!a) return 0; const m = mascara(a, f); let k = 0; for (let i = 0; i < a.n; i++) k += m[i] & a.sueldoValido[i]; return k; });

  function aplicar(nuevo: Filtros, reemplazar = false) { setFiltros(nuevo, reemplazar); }
  function conBusqueda(valor: string) {
    qLocal = valor;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const nuevo = { ...filtrosActuales(), q: valor.trim() };
      if (profundo && valor.trim().length >= 2) {
        try { nuevo.qIds = (await api.buscar(valor.trim())).ids; nuevo.q = ''; } catch { /* sin búsqueda profunda */ }
      }
      aplicar(nuevo, true);
    }, 220);
  }
  function alternar(dim: string, valor: string) {
    const nuevo = filtrosActuales();
    const act = new Set(nuevo.sel[dim] ?? []);
    if (act.has(valor)) act.delete(valor); else act.add(valor);
    nuevo.sel = { ...nuevo.sel, [dim]: [...act] };
    aplicar(nuevo);
  }
  function rango(id: 'sueldo' | 'score' | 'antiguedad', lado: 0 | 1, texto: string, escala = 1) {
    const nuevo = filtrosActuales();
    const r = [...(nuevo.rangos[id] ?? [null, null])] as [number | null, number | null];
    r[lado] = texto.trim() === '' ? null : Number(texto) * escala;
    nuevo.rangos = { ...nuevo.rangos, [id]: r };
    aplicar(nuevo, true);
  }
  const valorRango = (id: 'sueldo' | 'score' | 'antiguedad', lado: 0 | 1, escala = 1) => {
    const v = f.rangos[id]?.[lado]; return v === null || v === undefined ? '' : String(v / escala);
  };

  function opciones(dim: string) {
    if (!a) return [];
    const d = getDim(a, dim);
    const cuenta = new Map<number, number>();
    for (const [c, filas] of agrupar(a, dim, mascara(a, f, [dim]))) cuenta.set(c, filas.length);
    const sel = new Set(f.sel[dim] ?? []);
    const todas = d.etiquetas.map((e, c) => ({ valor: e, n: cuenta.get(c) ?? 0 })).filter((o) => o.n > 0 || sel.has(o.valor));
    const q = busca.trim().toLowerCase();
    return todas.filter((o) => !q || o.valor.toLowerCase().includes(q))
      .sort((x, y) => (x.valor === '' ? 1 : y.valor === '' ? -1 : y.n - x.n)).slice(0, 80);
  }
  const chips = $derived(Object.entries(f.sel).filter(([, v]) => v.length));
  const rangoTxt = (id: string, r: [number | null, number | null]) =>
    id === 'sueldo' ? `Sueldo ${r[0] ? '≥ $' + (r[0] / 1e6).toLocaleString('es-CL') + ' M' : ''} ${r[1] ? '≤ $' + (r[1] / 1e6).toLocaleString('es-CL') + ' M' : ''}`
    : id === 'score' ? `Puntaje ${r[0] ?? ''}–${r[1] ?? ''}` : id === 'antiguedad' ? `Antigüedad ≤ ${r[1] ?? r[0]} d` : `Fecha`;
  const rangosActivos = $derived(Object.entries(f.rangos).filter(([, r]) => r && (r[0] !== null || r[1] !== null)) as [string, [number | null, number | null]][]);
  function quitarRango(id: string) { const nuevo = filtrosActuales(); delete nuevo.rangos[id as 'sueldo']; aplicar(nuevo); }
  function quitarSel(dim: string, v: string) { alternar(dim, v); }
</script>

<svelte:window onclick={() => (abierto = null)} onkeydown={(e) => { if (e.key === 'Escape') abierto = null; }} />

<div class="filtros">
  <div class="fila">
    <input class="input buscar" type="search" placeholder="Buscar cargo, empresa o resumen…" aria-label="Buscar"
           value={qLocal} oninput={(e) => conBusqueda(e.currentTarget.value)}>
    <label class="chip" title="Busca también dentro de la descripción completa (más lento)">
      <input type="checkbox" bind:checked={profundo} onchange={() => conBusqueda(qLocal)}> en descripción
    </label>
    <button class="btn solo-movil" aria-expanded={movilAbierto} onclick={(e) => { e.stopPropagation(); movilAbierto = !movilAbierto; }}>Filtros{contarFiltros(f) ? ` (${contarFiltros(f)})` : ''} ▾</button>
    <div class="facets" class:abierto={movilAbierto}>
    {#each FACETS as fc}
      <div class="facet">
        <button class="btn" aria-haspopup="listbox" aria-expanded={abierto === fc.dim}
                onclick={(e) => { e.stopPropagation(); abierto = abierto === fc.dim ? null : fc.dim; busca = ''; }}>
          {fc.etiqueta}{#if f.sel[fc.dim]?.length}<span class="n">{f.sel[fc.dim].length}</span>{/if} ▾
        </button>
        {#if abierto === fc.dim}
          <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
          <div class="popover" onclick={(e) => e.stopPropagation()} role="listbox" tabindex="-1">
            {#if fc.buscable}<input class="input" style:width="100%" placeholder="Filtrar lista…" bind:value={busca}>{/if}
            {#if fc.dim === 'tech'}
              <div class="chips"><span class="suave">Combinar:</span>
                <button class="btn chico" aria-pressed={f.techsModo === 'o'} onclick={() => aplicar({ ...filtrosActuales(), techsModo: 'o' })}>alguna</button>
                <button class="btn chico" aria-pressed={f.techsModo === 'y'} onclick={() => aplicar({ ...filtrosActuales(), techsModo: 'y' })}>todas</button>
              </div>
            {/if}
            {#each opciones(fc.dim) as o}
              <label class:cero={o.n === 0}>
                <input type="checkbox" checked={(f.sel[fc.dim] ?? []).includes(o.valor)} onchange={() => alternar(fc.dim, o.valor)}>
                <span class="cortar">{etiquetaValor(o.valor)}</span><span class="cnt">{o.n}</span>
              </label>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
    <div class="facet">
      <button class="btn" aria-expanded={abierto === 'rangos'} onclick={(e) => { e.stopPropagation(); abierto = abierto === 'rangos' ? null : 'rangos'; }}>
        Sueldo y puntaje ▾
      </button>
      {#if abierto === 'rangos'}
        <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
        <div class="popover" onclick={(e) => e.stopPropagation()} style:min-width="270px">
          <div class="suave">Sueldo mensual (millones CLP)</div>
          <div class="fila">
            <input class="input" type="number" step="0.1" min="0" placeholder="mín" style:width="90px" value={valorRango('sueldo', 0, 1e6)} onchange={(e) => rango('sueldo', 0, e.currentTarget.value, 1e6)}>
            <span>a</span>
            <input class="input" type="number" step="0.1" min="0" placeholder="máx" style:width="90px" value={valorRango('sueldo', 1, 1e6)} onchange={(e) => rango('sueldo', 1, e.currentTarget.value, 1e6)}>
          </div>
          <label><input type="checkbox" checked={f.conSueldo} onchange={(e) => aplicar({ ...filtrosActuales(), conSueldo: e.currentTarget.checked })}> Solo con sueldo declarado</label>
          <div class="suave">Puntaje de encaje</div>
          <div class="fila">
            <input class="input" type="number" min="0" max="100" placeholder="mín" style:width="90px" value={valorRango('score', 0)} onchange={(e) => rango('score', 0, e.currentTarget.value)}>
            <span>a</span>
            <input class="input" type="number" min="0" max="100" placeholder="máx" style:width="90px" value={valorRango('score', 1)} onchange={(e) => rango('score', 1, e.currentTarget.value)}>
          </div>
          <div class="suave">Antigüedad máxima (días)</div>
          <input class="input" type="number" min="0" placeholder="días" style:width="90px" value={valorRango('antiguedad', 1)} onchange={(e) => rango('antiguedad', 1, e.currentTarget.value)}>
        </div>
      {/if}
    </div>
    </div>
    <span class="resultado tnum" aria-live="polite">{n.toLocaleString('es-CL')} ofertas · {conSueldo} con sueldo</span>
  </div>
  {#if contarFiltros(f) > 0}
    <div class="chips" aria-label="Filtros activos">
      {#if f.q}<span class="chip">“{f.q}” <button aria-label="Quitar búsqueda" onclick={() => { qLocal = ''; aplicar({ ...filtrosActuales(), q: '' }); }}>×</button></span>{/if}
      {#if f.qIds}<span class="chip">en descripción ({f.qIds.length}) <button aria-label="Quitar" onclick={() => aplicar({ ...filtrosActuales(), qIds: null })}>×</button></span>{/if}
      {#each chips as [dim, vals]}
        {#each vals as v}<span class="chip">{ETQ[dim] ?? dim}: {etiquetaValor(v)} <button aria-label="Quitar {etiquetaValor(v)}" onclick={() => quitarSel(dim, v)}>×</button></span>{/each}
      {/each}
      {#each rangosActivos as [id, r]}<span class="chip">{rangoTxt(id, r)} <button aria-label="Quitar rango" onclick={() => quitarRango(id)}>×</button></span>{/each}
      {#if f.conSueldo}<span class="chip">con sueldo <button aria-label="Quitar" onclick={() => aplicar({ ...filtrosActuales(), conSueldo: false })}>×</button></span>{/if}
      <button class="btn chico plano" onclick={() => { qLocal = ''; aplicar(filtrosVacios()); }}>Limpiar todo</button>
    </div>
  {/if}
</div>
