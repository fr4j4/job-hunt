<script lang="ts">
  // Explorador libre: elige qué agrupar, cómo medirlo y (opcional) cómo desglosarlo. Todo queda en la URL.
  import { api } from '../lib/api';
  import type { Vista } from '../lib/tipos';
  import { app, avisar, ui } from '../estado/app.svelte';
  import { filtrosActuales, ir, params, queryDeFiltros, ruta, setFiltros, setParams } from '../estado/ruta.svelte';
  import { desdeQuery } from '../motor/filtros';
  import { colores } from '../viz/tema';
  import { construir, DIMS_COLOR, DIMS_X, EJEMPLOS, ETQ_METRICA, METRICAS_EX, recomendar, type SpecEx } from '../catalogo/explorador';
  import type { Ctx } from '../catalogo/comun';
  import FiltrosBarra from '../componentes/FiltrosBarra.svelte';
  import Grafico from '../viz/Grafico.svelte';
  import ComparaSegmentos from '../componentes/ComparaSegmentos.svelte';

  const f = $derived.by(() => { void ruta.search; return filtrosActuales(); });
  const p = $derived.by(() => { void ruta.search; return params(); });
  const spec = $derived<SpecEx>({ x: p.get('x') ?? 'tech', color: p.get('c') ?? '', metrica: p.get('m') ?? 'ofertas', forma: (p.get('forma') ?? '') as SpecEx['forma'] });
  const etiquetaDim = (d: string) => app.semantica?.dimensiones[d]?.etiqueta ?? d;

  const ctx = $derived.by<Ctx | null>(() => { if (!app.almacen) return null; void ui.oscuro;
    return { a: app.almacen, f, c: colores(ui.oscuro), perfil: app.perfil, mias: new Set(app.perfil?.techs ?? []),
      filtrar: (dim, valor) => { const n = filtrosActuales(); n.sel = { ...n.sel, [dim]: [valor] }; setFiltros(n); },
      abrir: (id) => ir(`/ofertas/${encodeURIComponent(id)}`, queryDeFiltros()), verOfertas: () => ir('/ofertas', queryDeFiltros()) }; });
  const res = $derived(ctx ? construir(ctx, spec, etiquetaDim) : null);
  const recSinForma = $derived(recomendar({ ...spec, forma: '' }));
  const formas = $derived(res ? [res.rec.forma, ...res.rec.alternativas] : []);

  const cambiar = (k: string, v: string) => setParams({ [k]: v }, true);
  const ETQ_FORMA: Record<string, string> = { barras: 'Barras', strip: 'Puntos + caja', apiladas: 'Apiladas 100 %', heatmap: 'Mapa de calor', lineas: 'Líneas', histograma: 'Histograma', tabla: 'Tabla' };

  let vistas = $state<Vista[]>([]);
  const cargarVistas = () => api.vistas().then((r) => (vistas = r.vistas.filter((v) => v.tipo === 'explorador'))).catch(() => {});
  cargarVistas();
  let nombre = $state('');
  async function guardar() {
    if (!nombre.trim()) return;
    try { await api.crearVista(nombre.trim(), 'explorador', { q: queryDeFiltros(), ...spec }); nombre = ''; avisar('Vista guardada'); cargarVistas(); }
    catch { avisar('No se pudo guardar (¿nombre repetido?)'); }
  }
  function abrirSpec(q: string, s: SpecEx) {
    const u = new URLSearchParams(q); u.set('x', s.x); u.set('m', s.metrica); if (s.color) u.set('c', s.color); if (s.forma) u.set('forma', s.forma);
    ir('/explorador', u.toString());
  }
  async function copiar() { try { await navigator.clipboard.writeText(location.href); avisar('Enlace copiado'); } catch { avisar('No se pudo copiar'); } }
</script>

<h1>Explorador</h1>
<p class="suave" style:margin="0 0 8px">Pregunta lo que quieras: elige qué agrupar, cómo medirlo y cómo desglosarlo. La configuración y los filtros viajan en la URL.</p>
<FiltrosBarra />

<div class="tarjeta" style:margin-bottom="12px">
  <div class="fila" style:display="flex" style:gap="10px" style:flex-wrap="wrap" style:align-items="end">
    <label>Agrupar por<br><select class="input" value={spec.x} onchange={(e) => cambiar('x', e.currentTarget.value)}>
      {#each DIMS_X as d}<option value={d}>{etiquetaDim(d)}</option>{/each}</select></label>
    <label>Desglosar por<br><select class="input" value={spec.color} onchange={(e) => cambiar('c', e.currentTarget.value)}>
      <option value="">— nada —</option>{#each DIMS_COLOR.filter((d) => d !== spec.x) as d}<option value={d}>{etiquetaDim(d)}</option>{/each}</select></label>
    <label>Medir<br><select class="input" value={spec.metrica} onchange={(e) => cambiar('m', e.currentTarget.value)}>
      {#each METRICAS_EX as m}<option value={m}>{ETQ_METRICA[m]}</option>{/each}</select></label>
    <div>Forma<br><span class="chips" role="group" aria-label="Forma del gráfico">
      {#each formas as fm}<button class="btn chico" aria-pressed={res?.espec && (spec.forma || res.rec.forma) === fm} onclick={() => cambiar('forma', fm === recSinForma.forma ? '' : fm)}>{ETQ_FORMA[fm]}{fm === res?.rec.forma ? ' ★' : ''}</button>{/each}
    </span></div>
    <span style:margin-left="auto" class="chips">
      <button class="btn chico" onclick={copiar}>Copiar enlace</button>
      <input class="input" placeholder="Nombre para guardar" bind:value={nombre} maxlength="60" style:width="170px">
      <button class="btn chico primario" onclick={guardar} disabled={!nombre.trim()}>Guardar vista</button>
    </span>
  </div>
  {#if res?.rec.avisos.length}<ul class="suave" style:margin="8px 0 0" style:padding-left="18px">{#each res.rec.avisos as av}<li>{av}</li>{/each}</ul>{/if}
</div>

{#if res}
  <Grafico titulo={res.espec.titulo} subtitulo={res.espec.subtitulo} opcion={res.espec.opcion} tabla={res.espec.tabla} alto={res.espec.alto} cobertura={res.espec.cobertura}
           aviso={res.espec.aviso} estado={res.espec.estado} mensaje={res.espec.mensaje} leyenda={res.espec.leyenda} onclick={res.espec.onclick} tablaDirecta={res.espec.tablaDirecta}
           acciones={[{ texto: 'Ver estas ofertas →', fn: () => ir('/ofertas', queryDeFiltros()) }]} />
  <details class="tarjeta" style:margin-top="12px">
    <summary>Cómo se calculó</summary>
    <p>{ETQ_METRICA[spec.metrica]} agrupando por <b>{etiquetaDim(spec.x)}</b>{spec.color ? ` y ${etiquetaDim(spec.color)}` : ''}, sobre las ofertas que pasan los filtros activos
      (excepto el de la propia dimensión agrupada, para que se vea el contexto). Con pocas ofertas por grupo el valor se oculta y se muestran los datos individuales.</p>
    <p class="suave">Definiciones: {app.semantica?.metricas[spec.metrica]?.denominador ? `denominador = ${app.semantica.metricas[spec.metrica].denominador}; ` : ''}mínimo de ofertas = {app.semantica?.metricas[spec.metrica]?.min_n ?? 1}.</p>
  </details>
{/if}

{#if ctx}<ComparaSegmentos x={ctx} />{/if}

<div class="grilla" style:margin-top="12px">
  <section class="tarjeta c6"><h2>Preguntas de ejemplo</h2>
    <ul style:padding-left="18px" style:margin="0">{#each EJEMPLOS as ej}<li><button class="btn plano" style:padding="2px 0" onclick={() => abrirSpec(ej.q, ej.spec)}>{ej.nombre}</button></li>{/each}</ul>
  </section>
  <section class="tarjeta c6"><h2>Mis vistas guardadas</h2>
    {#if !vistas.length}<p class="suave">Aún no guardas ninguna vista del Explorador.</p>{/if}
    <ul style:padding-left="18px" style:margin="0">
      {#each vistas as v}<li>
        <button class="btn plano" style:padding="2px 0" onclick={() => abrirSpec(String(v.spec.q ?? ''), v.spec as unknown as SpecEx)}>{v.nombre}</button>
        <button class="btn plano chico" aria-label="Borrar {v.nombre}" onclick={async () => { await api.borrarVista(v.id); cargarVistas(); }}>✕</button>
      </li>{/each}
    </ul>
  </section>
</div>
