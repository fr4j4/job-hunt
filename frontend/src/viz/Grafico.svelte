<script lang="ts">
  // Contenedor común de TODOS los gráficos (spec §7.6): título-pregunta, pie de cobertura, estados,
  // menú (tabla / CSV / PNG / abrir ofertas), selección por clic y tabla alternativa accesible.
  import type { Snippet } from 'svelte';
  import { echarts, type EChartsOption } from './echarts';
  import { ui } from '../estado/app.svelte';

  export interface TablaDatos { columnas: string[]; filas: (string | number | null)[][] }
  interface Leyenda { nombre: string; color: string }
  interface Props {
    titulo: string; subtitulo?: string; opcion?: EChartsOption | null; tabla?: TablaDatos | null; alto?: number;
    cobertura?: string; aviso?: string; estado?: 'ok' | 'vacio' | 'oculto'; mensaje?: string;
    leyenda?: Leyenda[]; cargando?: boolean; acciones?: { texto: string; fn: () => void }[];
    onclick?: (p: any) => void; children?: Snippet; clase?: string; id?: string; tablaDirecta?: boolean;
  }
  let { titulo, subtitulo = '', opcion = null, tabla = null, alto = 260, cobertura = '', aviso = '', estado = 'ok',
        mensaje = '', leyenda = [], cargando = false, acciones = [], onclick, children, clase = '', id = '', tablaDirecta = false }: Props = $props();

  let host: HTMLDivElement | undefined = $state();
  let chart: ReturnType<typeof echarts.init> | null = null;
  let verTabla = $state(tablaDirecta);
  let menu = $state(false);
  const reducir = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  $effect(() => {
    if (!host || estado !== 'ok' || !opcion || verTabla) return;
    if (!chart) {
      chart = echarts.init(host, null, { renderer: 'svg' });
      chart.on('click', (p: any) => onclick?.(p));
    }
    chart.setOption({ animation: !reducir, animationDuration: 250, ...opcion } as any, true);
    void ui.oscuro;
  });
  $effect(() => {
    if (!host) return;
    const ro = new ResizeObserver(() => chart?.resize());
    ro.observe(host);
    return () => { ro.disconnect(); chart?.dispose(); chart = null; };
  });
  $effect(() => { if (verTabla && chart) { chart.dispose(); chart = null; } });

  function descargar(nombre: string, blob: Blob) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = nombre; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  const slug = () => titulo.toLowerCase().replace(/[^a-z0-9áéíóúñ]+/gi, '-').replace(/^-|-$/g, '').slice(0, 50) || 'grafico';
  function csv() {
    if (!tabla) return;
    // anti inyección de fórmulas: celdas que empiezan con = + - @ se prefijan con '
    const cel = (v: unknown) => {
      let s = v === null || v === undefined ? '' : String(v);
      if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
      return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const t = [tabla.columnas, ...tabla.filas].map((f) => f.map(cel).join(',')).join('\n');
    descargar(`${slug()}.csv`, new Blob(['﻿' + t], { type: 'text/csv;charset=utf-8' }));
    menu = false;
  }
  function png() {
    if (!chart) return;
    const url = chart.getDataURL({ type: 'svg' } as any);
    const img = new Image();
    img.onload = () => {
      const k = 2, cv = document.createElement('canvas');
      cv.width = img.width * k; cv.height = img.height * k;
      const g = cv.getContext('2d')!;
      g.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--panel').trim() || '#fff';
      g.fillRect(0, 0, cv.width, cv.height);
      g.drawImage(img, 0, 0, cv.width, cv.height);
      cv.toBlob((b) => b && descargar(`${slug()}.png`, b));
    };
    img.src = url;
    menu = false;
  }
</script>

<section class="tarjeta grafico {clase}" {id} aria-label={titulo}>
  <header>
    <div class="tt">
      <h3>{titulo}</h3>
      {#if subtitulo}<div class="sub">{subtitulo}</div>{/if}
    </div>
    <div class="menu-g">
      <button class="btn chico plano" aria-haspopup="menu" aria-expanded={menu} aria-label="Opciones del gráfico"
              onclick={() => (menu = !menu)}>⋯</button>
      {#if menu}
        <div class="popover" role="menu">
          {#if tabla}<button role="menuitem" onclick={() => { verTabla = !verTabla; menu = false; }}>{verTabla ? 'Ver gráfico' : 'Ver como tabla'}</button>{/if}
          {#if tabla}<button role="menuitem" onclick={csv}>Descargar CSV</button>{/if}
          {#if opcion && !verTabla}<button role="menuitem" onclick={png}>Descargar PNG</button>{/if}
          {#each acciones as a}<button role="menuitem" onclick={() => { a.fn(); menu = false; }}>{a.texto}</button>{/each}
        </div>
      {/if}
    </div>
  </header>

  {#if leyenda.length > 1 && estado === 'ok' && !verTabla}
    <div class="leyenda" aria-label="Leyenda">
      {#each leyenda as l}<span class="it"><i class="sw" style:background={l.color}></i>{l.nombre}</span>{/each}
    </div>
  {/if}

  {#if estado === 'vacio' || estado === 'oculto'}
    <div class="vacio" style:min-height="{Math.min(alto, 140)}px">{mensaje || 'Sin datos para esta selección.'}</div>
  {:else if verTabla && tabla}
    <div class="scroll-x" style:max-height="{alto + 40}px" style:overflow-y="auto">
      <table class="tabla">
        <thead><tr>{#each tabla.columnas as c}<th>{c}</th>{/each}</tr></thead>
        <tbody>{#each tabla.filas as f}<tr>{#each f as v, i}<td class:num={typeof v === 'number'}>{v ?? '—'}</td>{/each}</tr>{/each}</tbody>
      </table>
    </div>
  {:else if children}
    <div class="lienzo" class:atenuado={cargando}>{@render children()}</div>
  {:else}
    <div class="lienzo" class:atenuado={cargando} bind:this={host} style:height="{alto}px" role="img" aria-label={titulo}></div>
  {/if}

  {#if cobertura || aviso}
    <div class="pie">
      {#if cobertura}<span>{cobertura}</span>{/if}
      {#if aviso}<span class="aviso-muestra">⚠ {aviso}</span>{/if}
    </div>
  {/if}
</section>
