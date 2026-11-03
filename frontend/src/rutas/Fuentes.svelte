<script lang="ts">
  // Salud de fuentes (V-70), cobertura de datos por fuente (V-71) y frescura de la materialización (V-74).
  import { api } from '../lib/api';
  import type { FuentesResp } from '../lib/tipos';
  import { app, ui } from '../estado/app.svelte';
  import { colores, colorEntidad } from '../viz/tema';
  import { base, heatmap, type CeldaHM } from '../viz/formas';
  import { pct } from '../lib/formato';
  import Grafico from '../viz/Grafico.svelte';

  let d = $state<FuentesResp | null>(null);
  let error = $state('');
  api.fuentes().then((r) => (d = r)).catch(() => (error = 'No se pudo cargar el estado de las fuentes'));

  const ICONO = { ok: '●', vigilar: '▲', caida: '✖' } as const;
  const TEXTO = { ok: 'funcionando', vigilar: 'a vigilar', caida: 'caída' } as const;
  
  const CAMPOS: [string, string][] = [['sueldo', 'Sueldo'], ['techs', 'Tecnologías'], ['modalidad', 'Modalidad'], ['ubicacion', 'Ubicación'],
    ['seniority', 'Seniority'], ['experiencia', 'Años exp.'], ['ingles', 'Inglés'], ['descripcion', 'Descripción']];

  function serie(f: NonNullable<typeof d>['fuentes'][number]) {
    const c = colores(ui.oscuro);
    const orden = [...(d?.barridos ?? [])].reverse();
    const vals = [...f.serie].reverse().map((x) => (x ? x.n : null));
    const errs = [...f.serie].reverse().map((x) => (x ? x.err : 0));
    return { c, orden, vals, errs, color: colorEntidad(c, 'fuente', f.fuente) };
  }
  function opcion(f: NonNullable<typeof d>['fuentes'][number]) {
    const { c, orden, vals, errs, color } = serie(f);
    return { ...base(c), grid: { left: 4, right: 4, top: 6, bottom: 4 },
      xAxis: { type: 'category', data: orden, show: false }, yAxis: { type: 'value', show: false },
      tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'none' }, formatter: (ps: any) => { const i = ps[0].dataIndex; const el = document.createElement('div'); el.textContent = `${orden[i].slice(5, 16).replace('T', ' ')} · ${vals[i] ?? 'no corrió'} ofertas${errs[i] ? ` · ${errs[i]} errores` : ''}`; return el; } },
      series: [{ type: 'bar', barMaxWidth: 7, data: vals.map((v, i) => ({ value: v ?? 0, itemStyle: { color: errs[i] ? c.estado.serio : color, borderRadius: [2, 2, 0, 0] } })) }] };
  }
  const opcionCob = $derived.by(() => {
    if (!d || !d.cobertura.length) return null;
    const c = colores(ui.oscuro);
    const ys = d.cobertura.map((r) => String(r.fuente));
    const celdas: CeldaHM[] = [];
    d.cobertura.forEach((r, yi) => CAMPOS.forEach(([k], xi) => {
      const n = Number(r.n), v = Number(r[k] ?? 0);
      celdas.push({ xi, yi, valor: n ? v / n : null, n, estado: n >= 10 ? 'ok' : n ? 'chica' : 'insuficiente' });
    }));
    return heatmap(c, CAMPOS.map(([, t]) => t), ys, celdas, { fmt: (v) => pct(v), etiquetaValor: '% de ofertas con el dato', rampa: c.secuencial, min: 0, max: 1, valorEnCelda: true, rotX: 0 });
  });
</script>

<h1>Fuentes y calidad de datos</h1>
<p class="suave">¿Puedo confiar en estos números? Cada barra es un barrido (el más reciente a la derecha); en naranja, barridos con errores de conexión.</p>

{#if error}<p class="vacio">{error}</p>
{:else if !d}<div class="skeleton" style:height="200px"></div>
{:else}
  {#if d.analytics}
    <p class="suave" data-testid="analytics-meta">Última materialización: {d.analytics.ts.slice(0, 16).replace('T', ' ')} UTC · {d.analytics.filas} ofertas · {d.analytics.ms} ms · normalización {d.analytics.norm}</p>
  {/if}
  <div class="grilla">
    {#each d.fuentes as f (f.fuente)}
      <div class="c4">
        <Grafico titulo={f.nombre} subtitulo="Ofertas por barrido (naranja: con errores)" opcion={opcion(f)} alto={70}
                 cobertura="{ICONO[f.estado]} {TEXTO[f.estado]}{f.estado === 'caida' ? ` · ${f.racha_ceros} barridos en 0` : ''}" />
      </div>
    {/each}
    <div class="c12">
      <Grafico titulo="¿Qué datos trae cada fuente?" subtitulo="% de las ofertas activas de la fuente que traen el dato" opcion={opcionCob} alto={Math.max(180, d.cobertura.length * 40 + 90)}
               tabla={{ columnas: ['Fuente', 'Ofertas', ...CAMPOS.map(([, t]) => t)], filas: d.cobertura.map((r) => [String(r.fuente), Number(r.n), ...CAMPOS.map(([k]) => Number(r[k] ?? 0))]) }}
               cobertura="Solo ofertas activas · celdas con trama = fuente con muy pocas ofertas" />
    </div>
  </div>
{/if}
