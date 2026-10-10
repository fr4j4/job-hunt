<script lang="ts">
  // Análisis de mercado: pestañas de gráficos con filtro cruzado. Todo parte del mismo almacén y los mismos filtros (en la URL).
  import { api } from '../lib/api';
  import { app, ui } from '../estado/app.svelte';
  import { filtrosActuales, ir, params, queryDeFiltros, ruta, setFiltros, subruta, BASE } from '../estado/ruta.svelte';
  import { colores } from '../viz/tema';
  import { especs, historiaVacia, PESTANAS, type Pestana } from '../catalogo';
  import { kpis } from '../catalogo/kpis';
  import type { Ctx } from '../catalogo/comun';
  import { clp, num, pct } from '../lib/formato';
  import FiltrosBarra from '../componentes/FiltrosBarra.svelte';
  import Grafico from '../viz/Grafico.svelte';

  const tab = $derived.by(() => { void ruta.path; const t = subruta().split('/')[0] as Pestana; return PESTANAS.some(([id]) => id === t) ? t : 'resumen'; });
  const f = $derived.by(() => { void ruta.search; return filtrosActuales(); });

  // la tecla Mayús al hacer clic suma valores a la misma dimensión (en vez de reemplazarlos)
  let mayus = false;
  if (typeof window !== 'undefined') window.addEventListener('pointerdown', (e) => (mayus = e.shiftKey), true);

  function filtrar(dim: string, valor: string, aditivo = false) {
    const nuevo = filtrosActuales();
    const act = nuevo.sel[dim] ?? [];
    const suma = aditivo || mayus;
    nuevo.sel = { ...nuevo.sel, [dim]: act.includes(valor) ? act.filter((v) => v !== valor) : suma ? [...act, valor] : [valor] };
    setFiltros(nuevo);
  }
  const verOfertas = () => ir('/ofertas', queryDeFiltros());
  const abrir = (id: string) => ir(`/ofertas/${encodeURIComponent(id)}`, queryDeFiltros());

  const ctx = $derived.by<Ctx | null>(() => {
    if (!app.almacen) return null;
    void ui.oscuro; void ruta.search;
    return { a: app.almacen, f, c: colores(ui.oscuro), perfil: app.perfil, mias: new Set(app.perfil?.techs ?? []), filtrar, abrir, verOfertas };
  });

  let hist = $state(historiaVacia());
  let cargada = false;
  $effect(() => {
    if (tab !== 'dinamica' || cargada) return;
    cargada = true;
    Promise.all([api.diaria('n_nuevas'), api.diaria('n_cerradas'), api.diaria('n_activas', 'rol_familia'), api.diaria('sueldo_p50'), api.diaria('sueldo_p25'),
                 api.diaria('sueldo_p75'), api.supervivencia()]).then(([diaria, cerradas, porFamilia, p50, p25, p75, surv]) => {
      hist = { diaria, cerradas, porFamilia, p50, p25, p75, surv, techs: hist.techs };
    }).catch(() => (cargada = false));
  });

  let techsCargadas = false;
  $effect(() => {
    if (tab !== 'tecnologias' || techsCargadas || !app.almacen) return;
    techsCargadas = true;
    api.techsSemanal([]).then((techs) => (hist = { ...hist, techs })).catch(() => (techsCargadas = false));
  });

  const lista = $derived(ctx ? especs(tab, ctx, hist) : []);
  const tarjetas = $derived(ctx && tab === 'resumen' ? kpis(ctx) : []);
  const fmt = (k: { valor: number | null; formato: string }) => (k.valor === null ? '—' : k.formato === 'clp' ? clp(k.valor) : k.formato === 'pct' ? pct(k.valor) : num(k.valor));
</script>

<h1>Análisis de mercado</h1>
<p class="suave" style:margin="0 0 8px">Los filtros afectan a todos los gráficos. Haz clic en una barra para filtrar (Mayús suma valores); cada gráfico dice cuántas ofertas lo respaldan.</p>
<FiltrosBarra />

<nav class="pestanas" aria-label="Secciones del análisis">
  {#each PESTANAS as [id, t]}
    <a href="{BASE}/analisis/{id}{ruta.search}" aria-current={tab === id ? 'page' : undefined}
       onclick={(e) => { e.preventDefault(); ir(`/analisis/${id}`, ruta.search); }}>{t}</a>
  {/each}
</nav>

{#if tarjetas.length}
  <div class="kpis">
    {#each tarjetas as k}
      <div class="tarjeta kpi" data-kpi={k.id}>
        <div class="l">{k.etiqueta}</div><div class="v tnum">{fmt(k)}</div>
        <div class="d suave">{k.nota}{#if k.aviso} · <span class="aviso-muestra">⚠ {k.aviso}</span>{/if}</div>
      </div>
    {/each}
  </div>
{/if}

{#if !ctx}
  <div class="skeleton" style:height="300px"></div>
{:else}
  <div class="grilla">
    {#each lista as e (e.id)}
      <div class={e.span ?? 'c6'}>
        <Grafico titulo={e.titulo} subtitulo={e.subtitulo} opcion={e.opcion} tabla={e.tabla} alto={e.alto} cobertura={e.cobertura} aviso={e.aviso}
                 estado={e.estado} mensaje={e.mensaje} leyenda={e.leyenda} onclick={e.onclick} tablaDirecta={e.tablaDirecta} id={e.id}
                 acciones={[{ texto: 'Ver estas ofertas →', fn: verOfertas }, ...(e.acciones ?? [])]} />
      </div>
    {/each}
  </div>
  {#if tab === 'calidad'}
    <p class="suave" style:margin-top="12px">La salud de cada fuente y su cobertura de datos están en <a href="{BASE}/fuentes" onclick={(e) => { e.preventDefault(); ir('/fuentes'); }}>Fuentes</a>.</p>
  {/if}
{/if}
