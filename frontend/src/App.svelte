<script lang="ts">
  import { onMount } from 'svelte';
  import { app, cargar, ciclarTema, iniciarTema, ui } from './estado/app.svelte';
  import { BASE, ir, queryDeFiltros, ruta, seccion } from './estado/ruta.svelte';
  import Inicio from './rutas/Inicio.svelte';
  import Ofertas from './rutas/Ofertas.svelte';
  import Analisis from './rutas/Analisis.svelte';
  import Explorador from './rutas/Explorador.svelte';
  import Fuentes from './rutas/Fuentes.svelte';
  import Comparador from './rutas/Comparador.svelte';

  const NAV = [['/', 'Inicio'], ['/ofertas', 'Ofertas'], ['/analisis', 'Análisis'], ['/explorador', 'Explorador'], ['/seguimiento', 'Seguimiento'], ['/fuentes', 'Fuentes']] as const;
  onMount(() => { iniciarTema(); cargar(); });

  const sec = $derived.by(() => { void ruta.path; return seccion(); });
  const actual = (p: string) => (p === '/' ? sec === '' : sec === p.slice(1));
  function nav(e: MouseEvent, p: string) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    const filtrable = p === '/ofertas' || p === '/analisis' || p === '/explorador';
    ir(p === '/seguimiento' ? '/ofertas' : p, p === '/seguimiento' ? 'vista=kanban' : filtrable ? queryDeFiltros() : '');
  }
  const salir = async () => { const f = document.createElement('form'); f.method = 'post'; f.action = '/salir'; document.body.append(f); f.submit(); };
  const iconoTema = $derived(ui.tema === 'auto' ? '◐' : ui.tema === 'light' ? '☀' : '☾');
</script>

<div class="shell">
  <header class="top">
    <a class="marca" href="{BASE}" onclick={(e) => nav(e, '/')}>jobhunt</a>
    <nav class="nav" aria-label="Principal">
      {#each NAV as [p, t]}
        <a href="{BASE}{p === '/' ? '' : p}" aria-current={actual(p) && !(p === '/ofertas' && ruta.search.includes('vista=kanban')) || (p === '/seguimiento' && sec === 'ofertas' && ruta.search.includes('vista=kanban')) ? 'page' : undefined}
           onclick={(e) => nav(e, p)}>{t}</a>
      {/each}
    </nav>
    <div class="acciones">
      <a class="btn plano" href="/?clasica=1" title="Volver a la vista clásica">Clásica</a>
      <button class="btn plano" onclick={ciclarTema} title="Tema: {ui.tema}" aria-label="Cambiar tema (actual: {ui.tema})">{iconoTema}</button>
      <button class="btn plano" onclick={salir}>Salir</button>
    </div>
  </header>

  <main class="contenido">
    {#if app.sinSesion}
      <div class="centro tarjeta"><h1>Sesión no válida</h1><p>Pide un enlace nuevo con <code>/web</code> en el bot de Telegram.</p></div>
    {:else if app.error}
      <div class="centro tarjeta"><h1>No se pudo cargar</h1><p>{app.error}</p><button class="btn primario" onclick={() => cargar()}>Reintentar</button></div>
    {:else if app.cargando && !app.almacen}
      <div class="skeleton" style:height="120px"></div><div class="skeleton" style:height="320px" style:margin-top="12px"></div>
    {:else if sec === ''}<Inicio />
    {:else if sec === 'ofertas'}<Ofertas />
    {:else if sec === 'analisis'}<Analisis />
    {:else if sec === 'explorador'}<Explorador />
    {:else if sec === 'comparar'}<Comparador />
    {:else if sec === 'fuentes'}<Fuentes />
    {:else}<div class="centro tarjeta"><h1>No encontrada</h1><p><a href="{BASE}">Volver al inicio</a></p></div>{/if}
  </main>
  {#if ui.toast}<div class="toast" role="status">{ui.toast}</div>{/if}
</div>
