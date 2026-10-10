<script lang="ts">
  // Detalle de oferta: ficha + contexto de mercado (V-90 sueldo entre pares, V-91 cascada, V-92 línea de tiempo, V-93 techs).
  import { api } from '../lib/api';
  import type { DetalleOferta } from '../lib/tipos';
  import { app } from '../estado/app.svelte';
  import { clp, clpCompleto, etiquetaValor } from '../lib/formato';
  import { urlSegura } from '../lib/filas';
  import { percentil } from '../motor/estadistica';
  import { demandaTech } from '../motor/agregar';
  import { colores } from '../viz/tema';
  import { ui } from '../estado/app.svelte';
  import { base } from '../viz/formas';
  import Grafico from '../viz/Grafico.svelte';
  import EstadoBotones from '../componentes/EstadoBotones.svelte';

  let { id, alCerrar }: { id: string; alCerrar?: () => void } = $props();
  let d = $state<DetalleOferta | null>(null);
  let error = $state('');
  $effect(() => {
    const k = id; d = null; error = '';
    api.oferta(k).then((r) => { if (k === id) d = r; }).catch(() => (error = 'No se pudo cargar la oferta'));
  });

  const a = $derived(app.almacen);
  const fila = $derived(a && d ? a.indice.get(d.id) ?? -1 : -1);

  // V-90: sueldo entre pares (misma familia y seniority; si hay pocos, solo la familia)
  const pares = $derived.by(() => {
    if (!a || !d || fila < 0) return null;
    const c = a.snap.cols;
    const sacar = (soloFamilia: boolean) => {
      const v: number[] = [];
      for (let i = 0; i < a.n; i++) {
        if (i === fila || Number.isNaN(a.sueldo[i]) || c.rol_familia[i] !== c.rol_familia[fila]) continue;
        if (!soloFamilia && c.seniority[i] !== c.seniority[fila]) continue;
        v.push(a.sueldo[i]);
      }
      return v;
    };
    let alcance = `${d.rol_familia}${d.seniority ? ' · ' + etiquetaValor(d.seniority) : ''}`;
    let v = d.seniority ? sacar(false) : sacar(true);
    if (v.length < 5 && d.seniority) { v = sacar(true); alcance = d.rol_familia; }
    const mio = d.sueldo !== null && !Number.isNaN(a.sueldo[fila]) ? a.sueldo[fila] : null;
    const pc = mio !== null && v.length >= 5 ? Math.round((v.filter((x) => x <= mio).length / v.length) * 100) : null;
    return { alcance, n: v.length, p25: percentil(v, 25), p50: percentil(v, 50), p75: percentil(v, 75), mio, pc };
  });

  // V-91: cascada del desglose del score
  const cascada = $derived.by(() => {
    if (!d) return null;
    const pasos = d.desglose.map((x) => ({ n: x.etiqueta, v: Number(String(x.valor).replace('+', '').replace('−', '-')) }))
      .filter((x) => Number.isFinite(x.v) && x.v !== 0);
    if (!pasos.length) return null;
    const c = colores(ui.oscuro);
    let acum = 0; const base0: number[] = [], pos: (number | null)[] = [], neg: (number | null)[] = [];
    for (const p of pasos) { const ini = acum; acum += p.v; base0.push(Math.min(ini, acum)); pos.push(p.v > 0 ? p.v : null); neg.push(p.v < 0 ? -p.v : null); }
    return { c, pasos, base0, pos, neg, total: acum };
  });
  const opcionCascada = $derived.by(() => {
    if (!cascada || !d) return null;
    const { c, pasos, base0, pos, neg } = cascada;
    return { ...base(c), grid: { left: 8, right: 12, top: 8, bottom: 8, containLabel: true },
      xAxis: { type: 'value', axisLabel: { color: c.suave }, splitLine: { lineStyle: { color: c.rejilla } } },
      yAxis: { type: 'category', inverse: true, data: pasos.map((p) => p.n), axisLabel: { color: c.texto, width: 150, overflow: 'truncate', interval: 0 }, axisTick: { show: false } },
      tooltip: { ...base(c).tooltip, trigger: 'axis', axisPointer: { type: 'none' }, formatter: (ps: any) => { const i = ps[0].dataIndex; const el = document.createElement('div'); el.textContent = `${pasos[i].n}: ${pasos[i].v > 0 ? '+' : ''}${pasos[i].v}`; return el; } },
      series: [{ type: 'bar', stack: 'w', silent: true, itemStyle: { color: 'transparent' }, data: base0 },
        { type: 'bar', stack: 'w', barMaxWidth: 14, itemStyle: { color: c.divergente[0], borderRadius: 3 }, data: pos },
        { type: 'bar', stack: 'w', barMaxWidth: 14, itemStyle: { color: c.divergente[5], borderRadius: 3 }, data: neg }] };
  });

  const demanda = $derived.by(() => {
    if (!a || !d) return new Map<string, number>();
    const m = new Uint8Array(a.n).fill(1);
    return new Map(demandaTech(a, m, 200).items.map((t) => [t.tech, t.pct]));
  });
  const mias = $derived(new Set(app.perfil?.techs ?? []));
  const url = $derived(d ? urlSegura(d.url) : '');
  const tl = (t: string) => t.slice(0, 16).replace('T', ' ');
  const textoEvento = (e: { tipo: string; antes: string; despues: string }) => ({
    aparecida: 'Apareció en el pool', reaparecida: 'Volvió a publicarse', cerrada: 'Posiblemente cerrada',
    sueldo: `Sueldo: ${e.antes ? clpCompleto(Number(e.antes)) : 'sin dato'} → ${e.despues ? clpCompleto(Number(e.despues)) : 'sin dato'}`,
    score: `Puntaje ${e.antes} → ${e.despues}`, encaje: `Encaje ${e.antes || '—'} → ${e.despues}`, modalidad: `Modalidad ${e.antes} → ${e.despues}`,
  } as Record<string, string>)[e.tipo] ?? e.tipo;
</script>

{#if error}<p class="vacio">{error}</p>
{:else if !d}<div class="skeleton" style:height="180px"></div>
{:else}
  <article>
    <div style:display="flex" style:gap="8px" style:align-items="flex-start">
      <div style:flex="1">
        <h1>{d.titulo}</h1>
        <div class="suave">{d.empresa || 'Empresa no informada'}{d.ubicacion ? ' · ' + d.ubicacion : ''}</div>
      </div>
      {#if alCerrar}<button class="btn plano" onclick={alCerrar} aria-label="Cerrar detalle">✕</button>{/if}
    </div>
    <div class="cabecera-oferta" style:margin="8px 0">
      <span class="pill">Puntaje {d.score}</span>
      <span class="pill">Mercado {d.market_score}</span>
      {#if d.encaje}<span class="pill {d.encaje === 'alto' ? 'alto' : d.encaje === 'medio' ? 'medio' : 'bajo'}">Encaje {d.encaje}</span>{/if}
      {#if d.modalidad}<span class="chip">{etiquetaValor(d.modalidad)}{d.modalidad_src === 'inferida' ? ' (inferida)' : ''}</span>{/if}
      {#if d.seniority}<span class="chip">{etiquetaValor(d.seniority)}</span>{/if}
      <span class="chip">{d.rol_familia}</span>
      {#each d.fuentes as f}<span class="chip">{f}</span>{/each}
      {#if a && fila >= 0 && a.snap.cols.posible_cerrada[fila]}<span class="chip" title="Dejó de aparecer en los últimos barridos">⚠ posiblemente cerrada</span>{/if}
      {#if d.pasa_fit && d.es_dev}<span style:color="var(--ok)">✔ pasa el filtro del canal</span>
      {:else}<span style:color="var(--mal)">✖ no pasa el filtro del canal</span>{/if}
    </div>
    {#if url}<p><a class="btn primario" href={url} target="_blank" rel="noopener noreferrer">Ver y postular ↗</a></p>{/if}
    <EstadoBotones id={d.id} actual={d.estado?.estado ?? ''} nota={d.estado?.nota ?? ''} />

    <h2 style:margin-top="14px">Datos</h2>
    <dl style:display="grid" style:grid-template-columns="auto 1fr" style:gap="4px 14px" style:margin="0">
      <dt class="suave">Sueldo</dt><dd style:margin="0">{d.sueldo ? clpCompleto(d.sueldo) + ' / mes' : d.sueldo_texto || 'No declarado'}{d.sueldo_status && d.sueldo_status !== 'trusted' ? ` (${d.sueldo_status})` : ''}</dd>
      {#if d.exp_anios !== null}<dt class="suave">Experiencia</dt><dd style:margin="0">{d.exp_anios} años</dd>{/if}
      {#if d.idiomas.length}<dt class="suave">Idiomas</dt><dd style:margin="0">{#each d.idiomas as i, k}{i.idioma}{i.nivel ? ' ' + i.nivel : ''}{i.excluyente ? ' (excluyente)' : ''}{k < d.idiomas.length - 1 ? ', ' : ''}{/each}</dd>{/if}
      <dt class="suave">Vista por primera vez</dt><dd style:margin="0">{tl(d.first_seen)}</dd>
      <dt class="suave">Última vez vista</dt><dd style:margin="0">{tl(d.last_seen)}</dd>
    </dl>

    {#if d.techs.length}
      <h2 style:margin-top="14px">Tecnologías <span class="suave" style:font-weight="400">(% de ofertas que la piden)</span></h2>
      <div class="techs">
        {#each d.techs as t}<span class="chip" class:mia={mias.has(t)} title={mias.has(t) ? 'Está en tu perfil' : ''}>{t}{#if demanda.get(t) !== undefined} · {Math.round((demanda.get(t) ?? 0) * 100)} %{/if}</span>{/each}
      </div>
    {/if}

    {#if pares}
      <h2 style:margin-top="14px">Sueldo entre pares</h2>
      {#if pares.n < 5}
        <p class="suave">Pocos pares con sueldo declarado en {pares.alcance} (n={pares.n}): no alcanza para comparar.</p>
      {:else}
        <p>
          Mediana de {pares.alcance}: <b>{clp(pares.p50)}</b> (p25–p75: {clp(pares.p25)} – {clp(pares.p75)}, n={pares.n}).
          {#if pares.mio !== null}Esta oferta paga <b>{clp(pares.mio)}</b>, percentil <b>{pares.pc}</b> de sus pares.{:else}Esta oferta no declara sueldo.{/if}
          {#if pares.n < 10}<span class="aviso-muestra"> ⚠ muestra chica</span>{/if}
        </p>
      {/if}
    {/if}

    <h2 style:margin-top="14px">Por qué tiene ese puntaje ({d.score}/100)</h2>
    {#if d.score !== d.score_guardado}<p class="suave">El guardado ({d.score_guardado}) es de un criterio anterior; corre <code>python -m jobhunt rescore</code>.</p>{/if}
    {#if opcionCascada && cascada}
      <Grafico titulo="Cascada del puntaje" subtitulo="Cada barra suma o resta puntos desde 0" opcion={opcionCascada} alto={Math.max(120, cascada.pasos.length * 26 + 20)}
               tabla={{ columnas: ['Factor', 'Puntos'], filas: cascada.pasos.map((p) => [p.n, p.v]) }} />
    {/if}
    <table class="desglose"><tbody>
      {#each d.desglose as x}<tr><td>{x.etiqueta}</td><td class="num">{x.valor}</td></tr>{/each}
    </tbody></table>
    <h3 style:margin-top="10px">Puntaje de mercado: {d.market_score}/100</h3>
    <table class="desglose"><tbody>{#each d.mdesglose as x}<tr><td>{x.etiqueta}</td><td class="num">{x.valor}</td></tr>{/each}</tbody></table>

    {#if d.resumen || d.opinion || d.fit_reason || d.alertas.length || d.a_favor.length || d.beneficios.length}
      <h2 style:margin-top="14px">Análisis IA</h2>
      {#if d.resumen}<p>📝 {d.resumen}</p>{/if}
      {#if d.opinion}<p>💬 {d.opinion}</p>{/if}
      {#if d.fit_reason}<p>🎯 {d.fit_reason}</p>{/if}
      {#if d.alertas.length}<p>⚠️ <b>A considerar:</b> {d.alertas.join(' · ')}</p>{/if}
      {#if d.a_favor.length}<p>✅ <b>A favor:</b> {d.a_favor.join(' · ')}</p>{/if}
      {#if d.beneficios.length}<p>🎁 <b>Beneficios:</b> {d.beneficios.join(' · ')}</p>{/if}
    {/if}

    {#if d.eventos.length}
      <h2 style:margin-top="14px">Historial</h2>
      <ul style:padding-left="18px" style:margin="0">{#each d.eventos as e}<li><span class="suave tnum">{tl(e.ts)}</span> — {textoEvento(e)}</li>{/each}</ul>
    {/if}

    {#if d.descripcion}
      <h2 style:margin-top="14px">Descripción</h2>
      <div class="descripcion">{d.descripcion}</div>
    {/if}
  </article>
{/if}
