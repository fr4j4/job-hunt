<script lang="ts">
  // Inicio: ¿qué cambió y qué hago hoy? KPIs + lo mejor para ti + novedades.
  import { api } from '../lib/api';
  import type { EventoHist, HistoriaDiaria } from '../lib/tipos';
  import { app, ui } from '../estado/app.svelte';
  import { ir } from '../estado/ruta.svelte';
  import { filtrosVacios } from '../motor/filtros';
  import { colores } from '../viz/tema';
  import { kpis } from '../catalogo/kpis';
  import { v02 } from '../catalogo/dinamica';
  import type { Ctx } from '../catalogo/comun';
  import { fila } from '../lib/filas';
  import { clp, num, pct, edad, etiquetaValor } from '../lib/formato';
  import Grafico from '../viz/Grafico.svelte';

  let diaria = $state<HistoriaDiaria | null>(null);
  let eventos = $state<EventoHist[]>([]);
  api.diaria('n_nuevas').then((d) => (diaria = d)).catch(() => {});
  api.eventos('', 40).then((r) => (eventos = r.eventos.filter((e) => e.tipo !== 'aparecida').slice(0, 10))).catch(() => {});

  const ctx = $derived.by<Ctx | null>(() => app.almacen ? { a: app.almacen, f: filtrosVacios(), c: colores(ui.oscuro), perfil: app.perfil, mias: new Set(app.perfil?.techs ?? []),
    filtrar: () => {}, abrir: (id) => ir(`/ofertas/${id}`), verOfertas: () => ir('/ofertas') } : null);
  const tarjetas = $derived(ctx ? kpis(ctx) : []);
  const top = $derived.by(() => {
    const a = app.almacen; if (!a) return [];
    const ix: number[] = [];
    for (let i = 0; i < a.n; i++) if (!['descartada', 'postulada'].includes(a.snap.cols.estado[i]) && ['alto', 'medio'].includes(a.snap.cols.encaje[i]) && a.antiguedad[i] <= 14) ix.push(i);
    return ix.sort((p, q) => a.score[q] - a.score[p] || a.antiguedad[p] - a.antiguedad[q]).slice(0, 10).map((i) => fila(a, i));
  });
  const fmt = (k: { valor: number | null; formato: string }) => (k.valor === null ? '—' : k.formato === 'clp' ? clp(k.valor) : k.formato === 'pct' ? pct(k.valor) : num(k.valor));
  const TXT: Record<string, string> = { cerrada: 'posiblemente cerrada', reaparecida: 'volvió a publicarse', sueldo: 'cambió el sueldo', score: 'cambió el puntaje', encaje: 'cambió el encaje', modalidad: 'cambió la modalidad' };
  const spec = $derived(ctx ? v02(ctx, diaria) : null);
</script>

<h1>Inicio</h1>
<p class="suave">Resumen de todo el pool activo. Para filtrar y explorar, usa Ofertas o Análisis.</p>

<div class="kpis">
  {#each tarjetas as k}
    <div class="tarjeta kpi" data-kpi={k.id}>
      <div class="l">{k.etiqueta}</div><div class="v tnum">{fmt(k)}</div>
      <div class="d suave">{k.nota}{#if k.aviso} · <span class="aviso-muestra">⚠ {k.aviso}</span>{/if}</div>
    </div>
  {/each}
</div>

<div class="grilla">
  <section class="tarjeta c6" aria-label="Lo mejor para ti">
    <h2>Lo mejor para ti ahora</h2>
    <p class="suave" style:margin-top="0">Encaje alto o medio, publicadas en los últimos 14 días, que no has descartado.</p>
    {#if !top.length}<p class="vacio">Ninguna oferta cumple estos criterios.</p>{/if}
    <ol style:padding-left="20px" style:margin="0">
      {#each top as r (r.id)}
        <li style:margin-bottom="6px">
          <a href="/v2/ofertas/{r.id}" onclick={(e) => { e.preventDefault(); ir(`/ofertas/${r.id}`); }}><b>{r.titulo}</b></a>
          <div class="suave">{r.empresa || '—'} · <span class="pill">{r.score}</span> {r.sueldo ? clp(r.sueldo) : 'sin sueldo'} · {edad(r.antiguedad)}{r.modalidad ? ' · ' + etiquetaValor(r.modalidad) : ''}</div>
        </li>
      {/each}
    </ol>
  </section>

  <div class="c6">
    {#if spec}<Grafico titulo={spec.titulo} subtitulo={spec.subtitulo} opcion={spec.opcion} tabla={spec.tabla} alto={spec.alto} estado={spec.estado} mensaje={spec.mensaje} cobertura={spec.cobertura} />{/if}
    <section class="tarjeta" style:margin-top="12px" aria-label="Novedades">
      <h2>Qué cambió</h2>
      {#if !eventos.length}<p class="suave">Todavía no hay cambios registrados (sueldos, cierres, encaje). Se acumulan con cada barrido.</p>{/if}
      <ul style:padding-left="18px" style:margin="0">
        {#each eventos as e}<li><a href="/v2/ofertas/{e.oferta_id}" onclick={(ev) => { ev.preventDefault(); ir(`/ofertas/${e.oferta_id}`); }}>{e.title}</a> — <span class="suave">{TXT[e.tipo] ?? e.tipo}</span></li>{/each}
      </ul>
    </section>
  </div>
</div>
